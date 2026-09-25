# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：ExecView 参数校验与 job 创建用例（约定验收 1）；实时回传增加终止用例；
# Playbook 管理 放开 playbook 执行（原"Playbook 管理 前拒绝"占位用例随之改写）
import json
import signal

import pytest
from django.test import RequestFactory
from django_redis import get_redis_connection

from apps.account.models import User
from apps.host.models import Host
from apps.ansible_ops import views as ansible_views
from apps.ansible_ops.models import AnsibleJob, Playbook
from apps.ansible_ops.executor import RUNNING_KEY, PID_KEY, KILL_KEY
from apps.ansible_ops.views import ExecView, JobListView, JobDetailView, JobKillView


@pytest.fixture(autouse=True)
def clean_concurrency_slot(db):
    """exec 用例会真实占用 redis 并发名额（executor 被 mock 不回收），
    每个用例后清零，防止跨用例/跨运行累积触发"并发已满"。"""
    yield
    from django_redis import get_redis_connection
    get_redis_connection().delete(RUNNING_KEY)


@pytest.fixture
def user(db):
    return User.objects.create(username='tester', nickname='Tester', password_hash='x', is_supper=True)


@pytest.fixture
def host(user, db):
    return Host.objects.create(
        name='node-1', hostname='node-1', port=22, username='ops', created_by=user)


def make_request(user, body):
    request = RequestFactory().post(
        '/api/ansible_ops/exec/', data=json.dumps(body), content_type='application/json')
    request.user = user
    return request


def call_exec(user, body):
    return ExecView.as_view()(make_request(user, body))


@pytest.mark.django_db
def test_exec_requires_host_ids(user):
    response = call_exec(user, {'raw_module': {'name': 'ping'}})
    assert json.loads(response.content)['error']


@pytest.mark.django_db
def test_exec_rejects_playbook_and_raw_together(user, host):
    body = {
        'host_ids': [host.id],
        'playbook_id': 1,
        'raw_module': {'name': 'ping'},
    }
    response = call_exec(user, body)
    assert '二选一' in json.loads(response.content)['error']


@pytest.mark.django_db
def test_exec_rejects_missing_playbook(user, host):
    # Playbook 管理 起 playbook 执行解锁：不存在的 id 给出可读错误
    response = call_exec(user, {'host_ids': [host.id], 'playbook_id': 424242})
    assert 'Playbook 不存在' in json.loads(response.content)['error']


@pytest.mark.django_db
def test_exec_playbook_creates_pending_job(user, host, monkeypatch):
    # playbook 模式（约定：exec 的 playbook_id 即 Playbook.id）；
    # 不真正启动执行线程（执行引擎有独立用例），只验证 API 契约
    submitted = []
    monkeypatch.setattr(ansible_views, 'submit_job', lambda job_id: submitted.append(job_id))
    pb = Playbook.objects.create(name='demo', content='', updated_by=user)
    response = call_exec(user, {'host_ids': [host.id], 'playbook_id': pb.id})
    data = json.loads(response.content)
    assert not data['error']
    job = AnsibleJob.objects.get(pk=data['data']['job_id'])
    assert job.playbook_id == pb.id
    assert job.raw_module is None
    assert submitted == [job.id]


@pytest.mark.django_db
def test_exec_rejects_invalid_host_ids(user):
    response = call_exec(user, {'host_ids': ['abc'], 'raw_module': {'name': 'ping'}})
    assert '整数数组' in json.loads(response.content)['error']


@pytest.mark.django_db
def test_exec_rejects_unknown_host(user):
    response = call_exec(user, {'host_ids': [9999], 'raw_module': {'name': 'ping'}})
    assert '无效主机' in json.loads(response.content)['error']


@pytest.mark.django_db
def test_exec_creates_pending_job(user, host, monkeypatch):
    # 不真正启动执行线程（执行引擎有独立用例），只验证 API 契约
    from apps.ansible_ops import views
    submitted = []
    monkeypatch.setattr(views, 'submit_job', lambda job_id: submitted.append(job_id))
    response = call_exec(user, {'host_ids': [host.id], 'raw_module': {'name': 'ping', 'args': None}})
    data = json.loads(response.content)
    assert response.status_code == 200  # 约定偏差 202→200（前端封装限制），见问题 2
    assert 'job_id' in data['data']
    job = AnsibleJob.objects.get(pk=data['data']['job_id'])
    assert job.status == AnsibleJob.STATUS_PENDING
    assert json.loads(job.host_ids) == [host.id]
    assert submitted == [job.id]


@pytest.mark.django_db
def test_job_list_pagination(user, host):
    for _ in range(3):
        AnsibleJob.objects.create(
            user=user, host_ids=json.dumps([host.id]), extra_vars='{}')
    request = RequestFactory().get('/api/ansible_ops/jobs/?page=1&page_size=2')
    request.user = user
    data = json.loads(JobListView.as_view()(request).content)
    assert data['data']['total'] == 3
    assert len(data['data']['jobs']) == 2


@pytest.mark.django_db
def test_job_detail_includes_summary_and_hosts(user, host):
    job = AnsibleJob.objects.create(
        user=user, host_ids=json.dumps([host.id]),
        summary=json.dumps({'ok': 1, 'changed': 0, 'failed': 0, 'unreachable': 0}),
        status=AnsibleJob.STATUS_SUCCESS, extra_vars='{}')
    request = RequestFactory().get(f'/api/ansible_ops/jobs/{job.id}/')
    request.user = user
    data = json.loads(JobDetailView.as_view()(request, pk=job.id).content)['data']
    assert data['summary']['ok'] == 1
    assert data['hosts'] == [{'id': host.id, 'name': 'node-1', 'hostname': 'node-1'}]


# ---- 终止 API ----

def call_kill(user, job_id):
    request = RequestFactory().post(f'/api/ansible_ops/jobs/{job_id}/kill/')
    request.user = user
    return JobKillView.as_view()(request, pk=job_id)


@pytest.mark.django_db
def test_kill_missing_job(user):
    data = json.loads(call_kill(user, 99999).content)
    assert '任务不存在' in data['error']


@pytest.mark.django_db
def test_kill_rejects_finished_job(user, host):
    job = AnsibleJob.objects.create(
        user=user, host_ids=json.dumps([host.id]),
        status=AnsibleJob.STATUS_SUCCESS, extra_vars='{}')
    data = json.loads(call_kill(user, job.id).content)
    assert '已结束' in data['error']


@pytest.mark.django_db
def test_kill_running_sends_signal(user, host, monkeypatch):
    # 不真的 killpg（会波及测试进程），mock 信号调用只验证契约
    job = AnsibleJob.objects.create(
        user=user, host_ids=json.dumps([host.id]),
        status=AnsibleJob.STATUS_RUNNING, extra_vars='{}')
    rds = get_redis_connection()
    rds.set(PID_KEY.format(job.id), 424242, ex=60)
    killed = []

    monkeypatch.setattr(ansible_views.os, 'getpgid', lambda pid: pid)
    monkeypatch.setattr(ansible_views.os, 'killpg', lambda pgid, sig: killed.append((pgid, sig)))
    data = json.loads(call_kill(user, job.id).content)
    assert not data['error']
    assert killed == [(424242, signal.SIGTERM)]
    assert rds.get(KILL_KEY.format(job.id))
    rds.delete(PID_KEY.format(job.id))
    rds.delete(KILL_KEY.format(job.id))


@pytest.mark.django_db
def test_kill_running_without_pid(user, host):
    # 进程尚未注册 pid（排队/刚提交窗口）：给出可读提示而非 500
    job = AnsibleJob.objects.create(
        user=user, host_ids=json.dumps([host.id]),
        status=AnsibleJob.STATUS_RUNNING, extra_vars='{}')
    data = json.loads(call_kill(user, job.id).content)
    assert '尚未就绪' in data['error']


@pytest.mark.django_db
def test_kill_pending_sets_flag(user, host):
    # pending 任务无进程可杀：预置标记，executor 启动时检查并跳过
    job = AnsibleJob.objects.create(
        user=user, host_ids=json.dumps([host.id]),
        status=AnsibleJob.STATUS_PENDING, extra_vars='{}')
    rds = get_redis_connection()
    try:
        data = json.loads(call_kill(user, job.id).content)
        assert not data['error']
        assert rds.get(KILL_KEY.format(job.id))
    finally:
        rds.delete(KILL_KEY.format(job.id))
