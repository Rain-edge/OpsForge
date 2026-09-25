# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：执行引擎状态机用例（约定验收 1：job 状态流转）
import json

import pytest

from apps.account.models import User
from apps.host.models import Host
from apps.ansible_ops import executor
from apps.ansible_ops.models import AnsibleJob


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


def run_with_output(monkeypatch, tmp_path, job, recap_text=None, returncode=0):
    """mock subprocess.run：把 recap 文本写进日志文件，模拟 ansible 真实输出。"""
    monkeypatch.setattr(executor, 'LOG_DIR', tmp_path)

    def fake_run(command, stdout=None, **kwargs):
        if recap_text is not None:
            stdout.write(recap_text)
        return type('Proc', (), {'returncode': returncode})()

    monkeypatch.setattr(executor.subprocess, 'run', fake_run)
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
