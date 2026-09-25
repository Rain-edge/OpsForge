# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：JobConsumer 用例——鉴权拒绝、入组补发、终态关闭、断开清理。
# 不走 channels communicator（测试环境无 pytest-asyncio），直接实例化消费者
# 打桩 send/channel_layer，逐方法验证逻辑分支。
import json
from unittest.mock import MagicMock

import pytest

from apps.account.models import User
from apps.host.models import Host
from apps.ansible_ops.consumer import JobConsumer, group_name
from apps.ansible_ops.models import AnsibleJob


TEST_KEY = '-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----\n'


@pytest.fixture
def user(db):
    return User.objects.create(username='tester', nickname='Tester', password_hash='x', is_supper=True)


@pytest.fixture
def host(user, db):
    return Host.objects.create(
        name='node-1', hostname='node-1', port=22, username='ops',
        pkey=TEST_KEY, created_by=user)


def make_job(user, host, **kwargs):
    return AnsibleJob.objects.create(
        user=user, host_ids=json.dumps([host.id]),
        raw_module=json.dumps({'name': 'ping'}), extra_vars='{}', **kwargs)


def make_consumer(job_id, user):
    """构造可直接调用 init/_replay/disconnect 的消费者实例。
    channel_layer.group_add/discard 必须是协程函数（async_to_sync 包装调用）。"""
    consumer = JobConsumer()
    consumer.scope = {'url_route': {'kwargs': {'id': job_id}}}
    consumer.user = user
    consumer.channel_name = 'test.channel'
    layer_calls = {'add': [], 'discard': []}

    async def fake_group_add(group, channel):
        layer_calls['add'].append((group, channel))

    async def fake_group_discard(group, channel):
        layer_calls['discard'].append((group, channel))

    consumer.channel_layer = MagicMock()
    consumer.channel_layer.group_add = fake_group_add
    consumer.channel_layer.group_discard = fake_group_discard
    sends = []
    consumer.send = lambda text_data=None, bytes_data=None: sends.append(text_data)
    consumer.close = MagicMock()
    return consumer, sends, layer_calls


@pytest.mark.django_db
def test_init_joins_group_and_replays_log(user, host, tmp_path):
    job = make_job(user, host, log_path=str(tmp_path / '1.log'))
    (tmp_path / '1.log').write_text('line-1\nline-2\n', encoding='utf-8')
    consumer, sends, layer_calls = make_consumer(job.id, user)

    consumer.init()

    assert layer_calls['add'] == [(group_name(job.id), 'test.channel')]
    assert consumer._joined is True
    assert [json.loads(x) for x in sends] == [
        {'type': 'log', 'line': 'line-1', 'ts': None},
        {'type': 'log', 'line': 'line-2', 'ts': None},
    ]
    # 运行中任务不补发 done、不关闭连接
    consumer.close.assert_not_called()


@pytest.mark.django_db
def test_init_finished_job_replays_then_sends_done(user, host, tmp_path):
    from libs import human_datetime
    job = make_job(user, host, log_path=str(tmp_path / '2.log'),
                   status=AnsibleJob.STATUS_SUCCESS,
                   summary=json.dumps({'ok': 1, 'changed': 0, 'failed': 0}),
                   finished_at=human_datetime())
    (tmp_path / '2.log').write_text('out\n', encoding='utf-8')
    consumer, sends, layer_calls = make_consumer(job.id, user)

    consumer.init()

    frames = [json.loads(x) for x in sends]
    assert frames[0] == {'type': 'log', 'line': 'out', 'ts': None}
    assert frames[-1]['type'] == 'done'
    assert frames[-1]['status'] == AnsibleJob.STATUS_SUCCESS
    consumer.close.assert_called_once()


@pytest.mark.django_db
def test_init_missing_job_closes_without_join(user, host):
    consumer, sends, layer_calls = make_consumer(99999, user)
    consumer.close_with_message = MagicMock()

    consumer.init()

    consumer.close_with_message.assert_called_once()
    assert layer_calls['add'] == []
    assert consumer._joined is False
    assert sends == []


def test_disconnect_discards_only_when_joined(db):
    consumer, _, layer_calls = make_consumer(1, None)
    # 鉴权失败路径（init 未被调用）：disconnect 不得抛 AttributeError、不得退组
    consumer.disconnect(1000)
    assert layer_calls['discard'] == []

    consumer._joined = True
    consumer.job_id = 1
    consumer.disconnect(1000)
    assert layer_calls['discard'] == [(group_name(1), 'test.channel')]
