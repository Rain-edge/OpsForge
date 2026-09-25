# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：ExecView 参数校验与 job 创建用例（约定验收 1）
import json

import pytest
from django.test import RequestFactory

from apps.account.models import User
from apps.host.models import Host
from apps.ansible_ops.models import AnsibleJob
from apps.ansible_ops.executor import RUNNING_KEY
from apps.ansible_ops.views import ExecView, JobListView, JobDetailView


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
def test_exec_rejects_playbook_before_t3(user, host):
    # Playbook 库随 Playbook 管理 落地，本次任务 playbook_id 一律拒绝
    response = call_exec(user, {'host_ids': [host.id], 'playbook_id': 1})
    assert 'Playbook 管理' in json.loads(response.content)['error']


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
    assert response.status_code == 202
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
