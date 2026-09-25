# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Playbook 管理与版本化 API 用例
# （约定：CRUD + versions + rollback + syntax-check 拒保存）
import json

import pytest
from django.test import RequestFactory, override_settings

from apps.account.models import User
from apps.ansible_ops import repo
from apps.ansible_ops.models import Playbook
from apps.ansible_ops.views import (
    PlaybookListView, PlaybookDetailView, PlaybookVersionsView, PlaybookRollbackView)

# 常量不带尾换行：spug JsonParser 对字符串做 strip，API 入库内容与常量保持可比
VALID_PB = '- hosts: all\n  tasks:\n    - name: ping\n      ansible.builtin.ping:'
VALID_PB_V2 = '- hosts: all\n  tasks:\n    - name: ping\n      ping:'
BROKEN_PB = '- hosts: [broken\n  tasks: {{{'


@pytest.fixture
def repo_dir(tmp_path):
    # git 仓库落临时目录，不污染容器内 /data/repos 运行时数据
    with override_settings(REPOS_DIR=str(tmp_path / 'repos_root')):
        yield


@pytest.fixture
def user(db):
    return User.objects.create(username='tester', nickname='Tester', password_hash='x', is_supper=True)


def call(view, user, method, body=None, pk=None):
    factory = RequestFactory()
    kwargs = {'pk': pk} if pk is not None else {}
    if method == 'get':
        request = factory.get('/api/ansible_ops/playbooks/')
    else:
        request = getattr(factory, method)(
            '/api/ansible_ops/playbooks/', data=json.dumps(body), content_type='application/json')
    request.user = user
    return view.as_view()(request, **kwargs)


def data_of(response):
    return json.loads(response.content)


@pytest.mark.django_db
def test_create_creates_row_file_and_commit(user, repo_dir):
    data = data_of(call(PlaybookListView, user, 'post', {
        'name': 'demo', 'description': '测试', 'content': VALID_PB}))
    assert not data['error']
    pb_id = data['data']['id']
    pb = Playbook.objects.get(pk=pb_id)
    assert pb.name == 'demo'
    assert pb.updated_by == user
    assert repo.playbook_file(pb_id).exists()
    assert len(repo.list_versions(pb_id)) == 1


@pytest.mark.django_db
def test_create_rejects_duplicate_name(user, repo_dir):
    Playbook.objects.create(name='demo', content='', updated_by=user)
    data = data_of(call(PlaybookListView, user, 'post', {
        'name': 'demo', 'content': VALID_PB}))
    assert '已存在' in data['error']


@pytest.mark.django_db
def test_create_rejects_broken_syntax_without_side_effect(user, repo_dir):
    # 约定：语法错误拒绝保存并返回 ansible 输出；不留半成品（行/文件/提交）
    data = data_of(call(PlaybookListView, user, 'post', {
        'name': 'bad', 'content': BROKEN_PB}))
    assert '语法校验未通过' in data['error']
    assert data['error'].strip() != '语法校验未通过：'  # 附带 ansible 输出而非空尾巴
    assert not Playbook.objects.filter(name='bad').exists()
    assert not repo.list_versions(1) and not repo.playbook_file(1).exists()


@pytest.mark.django_db
def test_detail_includes_updater(user, repo_dir):
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    data = data_of(call(PlaybookDetailView, user, 'get', pk=pb.id))['data']
    assert data['name'] == 'demo'
    assert data['updated_by_name'] == 'Tester'
    assert data['updated_by_id'] == user.id


@pytest.mark.django_db
def test_put_updates_content_and_version(user, repo_dir):
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    repo.write_and_commit(pb.id, VALID_PB, 'opsforge: update demo', user)
    data = data_of(call(PlaybookDetailView, user, 'put', {
        'name': 'demo', 'description': '改过', 'content': VALID_PB_V2}, pk=pb.id))
    assert not data['error']
    pb.refresh_from_db()
    assert pb.content == VALID_PB_V2
    assert pb.description == '改过'
    assert len(repo.list_versions(pb.id)) == 2
    assert repo.playbook_file(pb.id).read_text(encoding='utf-8') == VALID_PB_V2


@pytest.mark.django_db
def test_put_rejects_broken_syntax_keeps_old(user, repo_dir):
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    repo.write_and_commit(pb.id, VALID_PB, 'opsforge: update demo', user)
    data = data_of(call(PlaybookDetailView, user, 'put', {
        'name': 'demo', 'content': BROKEN_PB}, pk=pb.id))
    assert '语法校验未通过' in data['error']
    pb.refresh_from_db()
    assert pb.content == VALID_PB
    assert len(repo.list_versions(pb.id)) == 1


@pytest.mark.django_db
def test_put_rejects_duplicate_name(user, repo_dir):
    Playbook.objects.create(name='other', content='', updated_by=user)
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    data = data_of(call(PlaybookDetailView, user, 'put', {
        'name': 'other', 'content': VALID_PB}, pk=pb.id))
    assert '已存在' in data['error']


@pytest.mark.django_db
def test_delete_removes_row_and_file(user, repo_dir):
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    repo.write_and_commit(pb.id, VALID_PB, 'opsforge: update demo', user)
    data = data_of(call(PlaybookDetailView, user, 'delete', pk=pb.id))
    assert not data['error']
    assert not Playbook.objects.filter(pk=pb.id).exists()
    assert not repo.playbook_file(pb.id).exists()


@pytest.mark.django_db
def test_versions_lists_commits_newest_first(user, repo_dir):
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    repo.write_and_commit(pb.id, VALID_PB, 'opsforge: update demo', user)
    repo.write_and_commit(pb.id, VALID_PB_V2, 'opsforge: update demo v2', user)
    data = data_of(call(PlaybookVersionsView, user, 'get', pk=pb.id))['data']
    assert [v['message'] for v in data] == ['opsforge: update demo v2', 'opsforge: update demo']
    assert all('patch' in v and 'id' in v and 'author' in v for v in data)


@pytest.mark.django_db
def test_versions_rejects_missing_playbook(user, repo_dir):
    data = data_of(call(PlaybookVersionsView, user, 'get', pk=424242))
    assert '不存在' in data['error']


@pytest.mark.django_db
def test_rollback_restores_content_as_new_commit(user, repo_dir):
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    c1 = repo.write_and_commit(pb.id, VALID_PB, 'opsforge: update demo', user)
    repo.write_and_commit(pb.id, VALID_PB_V2, 'opsforge: update demo v2', user)
    data = data_of(call(PlaybookRollbackView, user, 'post', {'commit': c1.hexsha}, pk=pb.id))
    assert not data['error']
    pb.refresh_from_db()
    # 内容回退到 v1，且以新提交形式入库（历史线性，不丢 v2 记录）
    assert pb.content == VALID_PB
    assert repo.playbook_file(pb.id).read_text(encoding='utf-8') == VALID_PB
    versions = repo.list_versions(pb.id)
    assert len(versions) == 3
    assert c1.hexsha[:7] in versions[0]['message']


@pytest.mark.django_db
def test_rollback_rejects_unknown_commit(user, repo_dir):
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    data = data_of(call(PlaybookRollbackView, user, 'post', {'commit': 'deadbeef'}, pk=pb.id))
    assert '不存在' in data['error']


@pytest.mark.django_db
def test_rollback_requires_commit_argument(user, repo_dir):
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    data = data_of(call(PlaybookRollbackView, user, 'post', {}, pk=pb.id))
    assert data['error']


@pytest.mark.django_db
def test_missing_playbook_returns_readable_error(user, repo_dir):
    data = data_of(call(PlaybookDetailView, user, 'put', {
        'name': 'x', 'content': VALID_PB}, pk=424242))
    assert '不存在' in data['error']


# ---- 自审修复回归：git 失败路径与防御 ----

@pytest.mark.django_db
def test_put_git_failure_returns_readable_error(user, repo_dir, monkeypatch):
    # 双 worker 下 index.lock 冲突等 git 异常：返回可读错误而非 500
    from apps.ansible_ops import views as playbook_views
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    repo.write_and_commit(pb.id, VALID_PB, 'v1', user)

    def boom(*args, **kwargs):
        raise RuntimeError('index.lock conflict')
    monkeypatch.setattr(playbook_views, 'write_and_commit', boom)
    data = data_of(call(PlaybookDetailView, user, 'put', {
        'name': 'demo', 'content': VALID_PB_V2}, pk=pb.id))
    assert 'git 提交失败' in data['error']
    pb.refresh_from_db()
    assert pb.content == VALID_PB  # 失败不落库


@pytest.mark.django_db
def test_rollback_git_failure_returns_readable_error(user, repo_dir, monkeypatch):
    from apps.ansible_ops import views as playbook_views
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    c1 = repo.write_and_commit(pb.id, VALID_PB, 'v1', user)

    def boom(*args, **kwargs):
        raise RuntimeError('index.lock conflict')
    monkeypatch.setattr(playbook_views, 'write_and_commit', boom)
    data = data_of(call(PlaybookRollbackView, user, 'post', {'commit': c1.hexsha}, pk=pb.id))
    assert 'git 提交失败' in data['error']


@pytest.mark.django_db
def test_delete_self_heals_without_repo_history(user, repo_dir):
    # 版本卷被重建（无文件无历史）：删除仍应成功而非 500
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    data = data_of(call(PlaybookDetailView, user, 'delete', pk=pb.id))
    assert not data['error']
    assert not Playbook.objects.filter(pk=pb.id).exists()


@pytest.mark.django_db
def test_put_metadata_only_skips_empty_commit(user, repo_dir):
    # 仅改名称/描述不产生空提交污染版本历史
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    repo.write_and_commit(pb.id, VALID_PB, 'v1', user)
    data = data_of(call(PlaybookDetailView, user, 'put', {
        'name': 'demo', 'description': '只改描述', 'content': VALID_PB}, pk=pb.id))
    assert not data['error']
    pb.refresh_from_db()
    assert pb.description == '只改描述'
    assert len(repo.list_versions(pb.id)) == 1


@pytest.mark.django_db
def test_rollback_rejects_non_hex_commit(user, repo_dir):
    # commit 白名单：'--' 开头的串不会被当作 git 选项解析
    pb = Playbook.objects.create(name='demo', content=VALID_PB, updated_by=user)
    data = data_of(call(PlaybookRollbackView, user, 'post', {'commit': '--all'}, pk=pb.id))
    assert '非法' in data['error']
