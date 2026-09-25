# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Playbook git 版本化与语法校验用例（真实调用 ansible-playbook）
from types import SimpleNamespace

import pytest
from django.test import override_settings

from apps.ansible_ops import repo

USER = SimpleNamespace(nickname='Tester', username='tester')

VALID_PB = '- hosts: all\n  tasks:\n    - name: ping\n      ansible.builtin.ping:\n'


@pytest.fixture
def repo_dir(tmp_path):
    # 测试专用 REPOS_DIR：git 仓库落临时目录，不污染容器内 /data/repos 运行时数据
    with override_settings(REPOS_DIR=str(tmp_path / 'repos_root')):
        yield


@pytest.mark.django_db
def test_write_and_commit_creates_versions(repo_dir):
    repo.write_and_commit(1, '- hosts: all\n  tasks: []\n', 'opsforge: update demo', USER)
    repo.write_and_commit(1, VALID_PB, 'opsforge: update demo v2', USER)
    versions = repo.list_versions(1)
    assert [v['message'] for v in versions] == ['opsforge: update demo v2', 'opsforge: update demo']
    assert versions[0]['author'] == 'Tester'
    # 日期格式与上游 gitlib._format_date 一致
    assert len(versions[0]['date']) == len('2026-09-16 12:00')
    assert repo.playbook_file(1).read_text(encoding='utf-8') == VALID_PB


@pytest.mark.django_db
def test_patch_visible_in_versions(repo_dir):
    repo.write_and_commit(1, '- hosts: all\n  tasks: []\n', 'v1', USER)
    repo.write_and_commit(1, VALID_PB, 'v2', USER)
    patch = repo.list_versions(1)[0]['patch']
    assert 'diff --git' in patch
    assert '-  tasks: []' in patch
    assert '+      ansible.builtin.ping:' in patch


@pytest.mark.django_db
def test_commit_author_records_operator(repo_dir):
    user = SimpleNamespace(nickname='耿浩然', username='geng')
    repo.write_and_commit(1, VALID_PB, 'opsforge: update demo', user)
    assert repo.list_versions(1)[0]['author'] == '耿浩然'


@pytest.mark.django_db
def test_write_normalizes_crlf(repo_dir):
    # 踩坑预案（容器内外换行符差异）：\r\n 入库前统一为 LF
    repo.write_and_commit(1, '- hosts: all\r\n  tasks:\r\n    - ping\r\n', 'v1', USER)
    raw = repo.playbook_file(1).read_bytes()
    assert b'\r\n' not in raw


@pytest.mark.django_db
def test_get_content_at_and_rollback_chain(repo_dir):
    c1 = repo.write_and_commit(1, '- hosts: all\n  tasks: []\n', 'v1', USER)
    repo.write_and_commit(1, VALID_PB, 'v2', USER)
    assert repo.get_content_at(1, c1.hexsha) == '- hosts: all\n  tasks: []\n'
    repo.write_and_commit(
        1, repo.get_content_at(1, c1.hexsha), f'opsforge: rollback demo to {c1.hexsha[:7]}', USER)
    versions = repo.list_versions(1)
    assert len(versions) == 3
    assert 'rollback' in versions[0]['message']


@pytest.mark.django_db
def test_get_content_at_invalid_commit(repo_dir):
    repo.write_and_commit(1, VALID_PB, 'v1', USER)
    with pytest.raises(ValueError):
        repo.get_content_at(1, 'deadbeef')


@pytest.mark.django_db
def test_delete_and_commit(repo_dir):
    repo.write_and_commit(1, VALID_PB, 'v1', USER)
    repo.delete_and_commit(1, 'opsforge: delete demo', USER)
    assert not repo.playbook_file(1).exists()
    # 删除也产生 commit，历史可追溯（列表新→旧）
    versions = repo.list_versions(1)
    assert len(versions) == 2
    assert versions[0]['message'] == 'opsforge: delete demo'


@pytest.mark.django_db
def test_list_versions_on_empty_repo(repo_dir):
    # REPOS_DIR 从未初始化（挂卷为空）时应返回空列表而非异常
    assert repo.list_versions(999) == []


def test_syntax_check_accepts_valid():
    ok, output = repo.syntax_check(VALID_PB)
    assert ok
    assert 'ERROR' not in output


def test_syntax_check_rejects_broken():
    ok, output = repo.syntax_check('- hosts: [broken\n  tasks: {{{\n')
    assert not ok
    assert output  # 约定：失败拒绝保存并返回 ansible 输出


def test_syntax_check_normalizes_crlf():
    # \r\n 不应造成假阴性：Windows 浏览器提交的内容必须能通过校验
    ok, _ = repo.syntax_check(VALID_PB.replace('\n', '\r\n'))
    assert ok
