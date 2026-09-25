# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：执行引擎状态机用例（约定验收 1：job 状态流转）
import json

import pytest
from types import SimpleNamespace

from apps.account.models import User
from apps.host.models import Host
from apps.ansible_ops import executor
from apps.ansible_ops.models import AnsibleJob, Playbook


TEST_KEY = '-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----\n'

RECAP_OK = (
    'web-01 : ok=1   changed=0   unreachable=0   failed=0   skipped=0   rescued=0   ignored=0\n'
    'web-02 : ok=1   changed=1   unreachable=0   failed=0   skipped=0   rescued=0   ignored=0\n'
)
RECAP_PARTIAL = RECAP_OK + (
    'db-01 : ok=0   changed=0   unreachable=1   failed=0   skipped=0   rescued=0   ignored=0\n'
)
RECAP_ALL_FAILED = (
    'db-01 : ok=0   changed=0   unreachable=1   failed=0   skipped=0   rescued=0   ignored=0\n'
)


def test_parse_recap_success():
    recap = executor.parse_recap('PLAY RECAP *************\n' + RECAP_OK)
    assert recap == {'ok': 2, 'changed': 1, 'unreachable': 0, 'failed': 0}


def test_parse_recap_no_output():
    assert executor.parse_recap('连接超时，无 recap') is None


ADHOC_OK = (
    'web-01 | SUCCESS => {\n    "ping": "pong"\n}\n'
    'web-02 | SUCCESS => {\n    "ping": "pong"\n}\n'
)
ADHOC_PARTIAL = ADHOC_OK + 'db-01 | UNREACHABLE! => {\n    "unreachable": true\n}\n'


def test_parse_adhoc_success():
    assert executor.parse_adhoc(ADHOC_OK) == {
        'ok': 2, 'changed': 0, 'unreachable': 0, 'failed': 0}


def test_parse_adhoc_partial():
    assert executor.parse_adhoc(ADHOC_PARTIAL) == {
        'ok': 2, 'changed': 0, 'unreachable': 1, 'failed': 0}


def test_parse_output_prefers_recap():
    # playbook 场景：recap 与结果行并存时以 recap 为准（精确计数）
    text = 'PLAY RECAP ********\nweb-01 : ok=2 changed=1 unreachable=0 failed=0\n' + ADHOC_OK
    assert executor.parse_output(text)['ok'] == 2


@pytest.mark.django_db
def test_run_job_adhoc_success(user, host, monkeypatch, tmp_path):
    job = run_with_output(monkeypatch, tmp_path, make_job(user, host), ADHOC_OK)
    assert job.status == AnsibleJob.STATUS_SUCCESS
    assert json.loads(job.summary) == {'ok': 2, 'changed': 0, 'unreachable': 0, 'failed': 0}


@pytest.fixture
def user(db):
    return User.objects.create(username='tester', nickname='Tester', password_hash='x', is_supper=True)


@pytest.fixture
def host(user, db):
    return Host.objects.create(
        name='node-1', hostname='node-1', port=22, username='ops',
        pkey=TEST_KEY, created_by=user)


def make_job(user, host):
    return AnsibleJob.objects.create(
        user=user, host_ids=json.dumps([host.id]),
        raw_module=json.dumps({'name': 'ping'}), extra_vars='{}')


def run_with_output(monkeypatch, tmp_path, job, recap_text=None, returncode=0, events=None,
                    on_event=None):
    """mock subprocess.Popen（起逐行流式）：把 recap 文本按行喂给 stdout 模拟
    ansible 真实输出；events 传 list 时捕获 _push_event 事件流，on_event 事件回调
    供模拟中途注入 redis 状态（如 kill 标记）。"""
    monkeypatch.setattr(executor, 'LOG_DIR', tmp_path)

    def fake_push(job_id, event):
        if events is not None:
            events.append((job_id, event))
        if on_event is not None:
            on_event(job_id, event)

    monkeypatch.setattr(executor, '_push_event', fake_push)

    class FakeProc:
        pid = 424242

        def __init__(self):
            # 类体读不到闭包变量，赋值须放方法内；run_job 以 readline 迭代 stdout
            self.returncode = returncode
            self._lines = recap_text.splitlines(keepends=True) if recap_text is not None else []
            self.stdout = self

        def readline(self):
            return self._lines.pop(0) if self._lines else ''

        def wait(self):
            return self.returncode

    monkeypatch.setattr(executor.subprocess, 'Popen', lambda *a, **kw: FakeProc())
    executor.run_job(job.id)
    job.refresh_from_db()
    return job


@pytest.mark.django_db
def test_run_job_success(user, host, monkeypatch, tmp_path):
    job = run_with_output(monkeypatch, tmp_path, make_job(user, host), RECAP_OK)
    assert job.status == AnsibleJob.STATUS_SUCCESS
    assert json.loads(job.summary) == {'ok': 2, 'changed': 1, 'unreachable': 0, 'failed': 0}
    assert job.finished_at


@pytest.mark.django_db
def test_run_job_partial(user, host, monkeypatch, tmp_path):
    job = run_with_output(monkeypatch, tmp_path, make_job(user, host), RECAP_PARTIAL)
    assert job.status == AnsibleJob.STATUS_PARTIAL
    assert json.loads(job.summary)['unreachable'] == 1


@pytest.mark.django_db
def test_run_job_all_unreachable(user, host, monkeypatch, tmp_path):
    job = run_with_output(monkeypatch, tmp_path, make_job(user, host), RECAP_ALL_FAILED)
    assert job.status == AnsibleJob.STATUS_FAILED


@pytest.mark.django_db
def test_run_job_no_recap_fails(user, host, monkeypatch, tmp_path):
    job = run_with_output(monkeypatch, tmp_path, make_job(user, host), None, returncode=4)
    assert job.status == AnsibleJob.STATUS_FAILED
    assert '无 recap' in json.loads(job.summary)['error']


@pytest.mark.django_db
def test_run_job_invalid_host_fails_and_cleans(user, monkeypatch, tmp_path):
    job = AnsibleJob.objects.create(
        user=user, host_ids=json.dumps([9999]),
        raw_module=json.dumps({'name': 'ping'}), extra_vars='{}')
    monkeypatch.setattr(executor, 'LOG_DIR', tmp_path)
    executor.run_job(job.id)
    job.refresh_from_db()
    assert job.status == AnsibleJob.STATUS_FAILED
    assert '未找到任何有效主机' in json.loads(job.summary)['error']
    # 临时 inventory/私钥目录执行完必须清理
    assert not list(tmp_path.parent.glob(f'ansible_ops_{job.id}_*'))


@pytest.mark.django_db
def test_run_job_writes_log_file(user, host, monkeypatch, tmp_path):
    job = run_with_output(monkeypatch, tmp_path, make_job(user, host), RECAP_OK)
    log_file = tmp_path / f'{job.id}.log'
    assert log_file.exists()
    content = log_file.read_text(encoding='utf-8')
    assert f'AnsibleJob {job.id}' in content
    assert 'command' in content


# ---- 实时回传与 kill 链路 ----

@pytest.mark.django_db
def test_run_job_streams_log_events(user, host, monkeypatch, tmp_path):
    # 约定：逐行 log 事件 + 终态一次 done 事件（头部 3 行元信息不推送）
    events = []
    job = run_with_output(monkeypatch, tmp_path, make_job(user, host), ADHOC_OK, events=events)
    types = [ev['type'] for _, ev in events]
    assert all(jid == job.id for jid, _ in events)
    assert types.count('log') == len(ADHOC_OK.splitlines())
    first_log = events[0][1]
    assert first_log['line'] == ADHOC_OK.splitlines()[0]
    assert 'ts' in first_log
    dones = [ev for _, ev in events if ev['type'] == 'done']
    assert len(dones) == 1
    assert dones[0]['status'] == AnsibleJob.STATUS_SUCCESS
    assert dones[0]['summary'] == {'ok': 2, 'changed': 0, 'unreachable': 0, 'failed': 0}


@pytest.mark.django_db
def test_run_job_skipped_when_killed_pending(user, host, monkeypatch, tmp_path):
    # pending 排队期间被终止：不执行直接落 failed(reason=killed)
    job = make_job(user, host)
    rds = _redis()
    rds.set(executor.KILL_KEY.format(job.id), 'killed', ex=60)
    try:
        events = []
        run_with_output(monkeypatch, tmp_path, job, None, events=events)
        job.refresh_from_db()
        assert job.status == AnsibleJob.STATUS_FAILED
        assert json.loads(job.summary) == {'reason': 'killed'}
        assert job.finished_at
        assert [ev['type'] for _, ev in events] == ['done']
    finally:
        rds.delete(executor.KILL_KEY.format(job.id))


@pytest.mark.django_db
def test_run_job_killed_flag_beats_recap(user, host, monkeypatch, tmp_path):
    # 模拟 kill 在执行中途到达：首个 log 事件后置标记，此后输出只是残片。
    # returncode=-15 对应真实 SIGTERM 组死亡，executor 仅对信号死亡采信标记
    job = make_job(user, host)
    rds = _redis()

    def inject_kill(job_id, event):
        if event['type'] == 'log':
            rds.set(executor.KILL_KEY.format(job.id), 'killed', ex=60)

    try:
        run_with_output(monkeypatch, tmp_path, job, RECAP_OK, returncode=-15, on_event=inject_kill)
        job.refresh_from_db()
        assert job.status == AnsibleJob.STATUS_FAILED
        assert json.loads(job.summary) == {'reason': 'killed'}
    finally:
        rds.delete(executor.KILL_KEY.format(job.id))


@pytest.mark.django_db
def test_run_job_ignores_late_kill_flag_on_natural_exit(user, host, monkeypatch, tmp_path):
    # 自然退出（returncode >= 0）后到达的迟到 kill 标记不采信：
    # 真实结果（recap 统计）不被改写为 killed
    job = make_job(user, host)
    rds = _redis()

    def inject_late_kill(job_id, event):
        if event['type'] == 'log':
            rds.set(executor.KILL_KEY.format(job.id), 'killed', ex=60)

    try:
        run_with_output(monkeypatch, tmp_path, job, RECAP_OK, returncode=0, on_event=inject_late_kill)
        job.refresh_from_db()
        assert job.status == AnsibleJob.STATUS_SUCCESS
        assert json.loads(job.summary) == {'ok': 2, 'changed': 1, 'unreachable': 0, 'failed': 0}
    finally:
        rds.delete(executor.KILL_KEY.format(job.id))


@pytest.mark.django_db
def test_run_job_pending_timeout_flag(user, host, monkeypatch, tmp_path):
    # pending 分支预置 timeout 标记：不执行直接落 failed(reason=timeout)，
    # 理由取标记值而非硬编码 killed（看门狗 _on_timeout 的置标记另有单测）
    job = make_job(user, host)
    rds = _redis()
    rds.set(executor.KILL_KEY.format(job.id), 'timeout', ex=60)
    try:
        run_with_output(monkeypatch, tmp_path, job, None)
        job.refresh_from_db()
        assert job.status == AnsibleJob.STATUS_FAILED
        assert json.loads(job.summary) == {'reason': 'timeout'}
    finally:
        rds.delete(executor.KILL_KEY.format(job.id))


def test_on_timeout_sets_flag_and_kills_process_group(monkeypatch):
    # 看门狗路径：置 timeout 标记 + 对进程组 SIGKILL
    import signal as signal_mod
    rds = _redis()
    killed = []
    monkeypatch.setattr(executor.os, 'getpgid', lambda pid: pid)
    monkeypatch.setattr(executor.os, 'killpg', lambda pgid, sig: killed.append((pgid, sig)))
    proc = type('P', (), {'pid': 424242})()
    executor._on_timeout(999, proc)
    try:
        assert rds.get(executor.KILL_KEY.format(999)) == b'timeout'
        assert killed == [(424242, signal_mod.SIGKILL)]
    finally:
        rds.delete(executor.KILL_KEY.format(999))


# ---- Playbook 执行分支 ----

PLAYBOOK = '- hosts: all\n  tasks:\n    - name: ping\n      ping:\n'


@pytest.mark.django_db
def test_build_command_adhoc(user, host, tmp_path):
    # 重构保护：ad-hoc 命令形状不回归（-m/-a 与 extra_vars 文件参数）
    job = AnsibleJob.objects.create(
        user=user, host_ids=json.dumps([host.id]),
        raw_module=json.dumps({'name': 'shell', 'args': 'hostname'}),
        extra_vars=json.dumps({'k': 'v'}))
    command = executor.build_command(job, tmp_path / 'inv.ini', tmp_path)
    assert command[0] == 'ansible'
    assert '-m' in command and 'shell' in command
    assert '-a' in command and 'hostname' in command
    assert '-e' in command and command[command.index('-e') + 1].startswith('@')


@pytest.mark.django_db
def test_build_command_playbook(user, host, tmp_path):
    from django.test import override_settings
    from apps.ansible_ops.repo import write_and_commit
    user_ns = SimpleNamespace(nickname='Tester', username='tester')
    with override_settings(REPOS_DIR=str(tmp_path / 'repos')):
        # FK 完整性：job 必须指向真实 Playbook 行（sqlite 事务结束会校验）
        pb = Playbook.objects.create(name='demo', content=PLAYBOOK, updated_by=user)
        write_and_commit(pb.id, PLAYBOOK, 'opsforge: update demo', user_ns)
        job = AnsibleJob.objects.create(
            user=user, host_ids=json.dumps([host.id]), playbook_id=pb.id, extra_vars='{}')
        command = executor.build_command(job, tmp_path / 'inv.ini', tmp_path)
    assert command[0] == 'ansible-playbook'
    assert command[1:5] == ['-i', str(tmp_path / 'inv.ini'), '-f', '10']
    assert str(command[5]).endswith(f'{pb.id}.yml')


@pytest.mark.django_db
def test_build_command_playbook_missing_file(user, host, tmp_path):
    from django.test import override_settings
    with override_settings(REPOS_DIR=str(tmp_path / 'repos')):
        # 行存在但文件缺失（如卷被清理）：给出可读错误而非 500
        pb = Playbook.objects.create(name='demo', content=PLAYBOOK, updated_by=user)
        job = AnsibleJob.objects.create(
            user=user, host_ids=json.dumps([host.id]), playbook_id=pb.id, extra_vars='{}')
        with pytest.raises(Exception, match='Playbook 文件缺失'):
            executor.build_command(job, tmp_path / 'inv.ini', tmp_path)


@pytest.mark.django_db
def test_run_job_playbook_mode(user, host, monkeypatch, tmp_path):
    # 端到端（mock 进程输出）：playbook job 走 ansible-playbook 分支并按 recap 落终态
    from django.test import override_settings
    from apps.ansible_ops.repo import write_and_commit
    user_ns = SimpleNamespace(nickname='Tester', username='tester')
    with override_settings(REPOS_DIR=str(tmp_path / 'repos')):
        pb = Playbook.objects.create(name='demo', content=PLAYBOOK, updated_by=user)
        write_and_commit(pb.id, PLAYBOOK, 'opsforge: update demo', user_ns)
        job = AnsibleJob.objects.create(
            user=user, host_ids=json.dumps([host.id]), playbook_id=pb.id, extra_vars='{}')
        result = run_with_output(monkeypatch, tmp_path, job, RECAP_OK)
    assert result.status == AnsibleJob.STATUS_SUCCESS
    assert json.loads(result.summary) == {'ok': 2, 'changed': 1, 'unreachable': 0, 'failed': 0}


def _redis():
    from django_redis import get_redis_connection
    return get_redis_connection()
