# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Playbook 存储与 git 版本化。
# 单仓库多文件（约定）：REPOS_DIR/ansible_ops/playbooks/ 即 git 工作树，文件 {id}.yml。
# 用 GitPython（上游 libs/gitlib.py 同款依赖，零新增）；REPOS_DIR 运行时才可读，
# 故一律调用时读取 settings，不做模块级常量（测试经 override_settings 换临时目录）。
from django.conf import settings
from git import Actor, BadName, BadObject, InvalidGitRepositoryError, Repo
from datetime import datetime
from pathlib import Path
import os
import re
import subprocess
import tempfile

SUB_DIR = Path('ansible_ops') / 'playbooks'
# 提交者固定为平台身份，作者按操作人区分（审计：谁改的版本可查）
COMMITTER = Actor('OpsForge', 'opsforge@local')
VERSION_LIMIT = 50


def get_repo_dir():
    return Path(settings.REPOS_DIR) / SUB_DIR


def get_repo():
    """懒初始化：目录/仓库不存在则创建（REPOS_DIR 为运行时挂卷，可能为空）。"""
    repo_dir = get_repo_dir()
    repo_dir.mkdir(parents=True, exist_ok=True)
    try:
        return Repo(repo_dir)
    except InvalidGitRepositoryError:
        return Repo.init(repo_dir)


def file_rel(pk):
    # git 仓库根即 playbooks 目录，文件在仓库内是顶层 {id}.yml
    # （物理路径 REPOS_DIR/ansible_ops/playbooks/{id}.yml 符合约定存储位置）
    return f'{pk}.yml'


def playbook_file(pk):
    return get_repo_dir() / f'{pk}.yml'


def normalize_content(content):
    """入库内容统一 LF：ansible 在容器（Linux）内运行，Windows 浏览器提交的
    \r\n 若直接入库会带来行为噪声。DB content 与 git 文件共用此归一化，
    保证"content 与版本库文件互为镜像"字节级成立。"""
    return content.replace('\r\n', '\n').replace('\r', '\n')


def _commit(repo, rel_path, message, user):
    # add 用绝对路径（不依赖 Repo.git 的 cwd 假设）；对已删除文件同样生效（staged deletion）
    repo.git.add('--', str(Path(repo.working_dir) / rel_path))
    author = Actor(user.nickname, f'{user.username}@opsforge.local')
    return repo.index.commit(message, author=author, committer=COMMITTER)


def write_and_commit(pk, content, message, user):
    """写文件并提交（内容经 normalize_content 归一化）。"""
    repo = get_repo()
    path = playbook_file(pk)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(normalize_content(content))
    return _commit(repo, file_rel(pk), message, user)


def delete_and_commit(pk, message, user):
    """删除文件并提交删除记录；文件与版本历史都不存在时（卷被重建等）跳过 git
    直接返回，保证 DELETE 接口总能自愈。"""
    repo = get_repo()
    path = playbook_file(pk)
    if not path.exists() and not list_versions(pk):
        return None
    if path.exists():
        path.unlink()
    return _commit(repo, file_rel(pk), message, user)


def get_content_at(pk, commit_id):
    """取指定 commit 时点的文件内容（rollback 用）。
    commit_id 做十六进制白名单（前端字符串直通 git rev-parse，防选项注入）；
    走对象 API 读 blob：git show 的字符串输出会被 GitPython 去掉末尾换行，
    内容不再字节级精确。"""
    commit_id = str(commit_id)
    if not re.fullmatch(r'[0-9a-fA-F]{6,40}', commit_id):
        raise ValueError('非法的 commit 标识')
    rel = file_rel(pk)
    try:
        blob = get_repo().commit(commit_id).tree / rel
        return blob.data_stream.read().decode('utf-8')
    except (BadName, BadObject, KeyError):
        raise ValueError('目标 commit 不存在或该版本无此文件')


def list_versions(pk):
    """指定 playbook 文件的 commit 列表（新→旧），每条附该次提交的 patch 文本。

    patch 用 git show --format= 提取（首个 commit 自然呈现为全量新增），
    前端直接渲染文本，满足约定"diff 可见"。
    """
    repo = get_repo()
    if not repo.head.is_valid():
        # 空仓库（无任何提交）：git log 会 fatal，直接返回空列表
        return []
    rel = file_rel(pk)
    versions = []
    for commit in repo.iter_commits(paths=rel, max_count=VERSION_LIMIT):
        try:
            patch = repo.git.show('--format=', commit.hexsha, '--', rel)
        except Exception:
            patch = ''
        versions.append({
            'id': commit.hexsha,
            'author': commit.author.name,
            # 格式与上游 libs/gitlib.py _format_date 一致（spug 的 human_time 只到时分秒）
            'date': datetime.fromtimestamp(commit.committed_date).strftime('%Y-%m-%d %H:%M'),
            'message': commit.message.strip(),
            'patch': patch.strip('\n'),
        })
    return versions


def syntax_check(content):
    """ansible-playbook --syntax-check 校验（约定：失败拒绝保存并返回 ansible 输出）。"""
    fd, tmp_path = tempfile.mkstemp(suffix='.yml')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
        try:
            proc = subprocess.run(
                ['ansible-playbook', '--syntax-check', tmp_path],
                capture_output=True, text=True, timeout=60,
                # PYTHONWARNINGS=ignore：ansible 启动时的 paramiko 弃用警告会混入
                # stderr，污染返回给前端的报错文本（真正的 ansible ERROR 不受影响）
                env=dict(os.environ, ANSIBLE_HOST_KEY_CHECKING='False', PYTHONWARNINGS='ignore'))
        except subprocess.TimeoutExpired:
            return False, 'syntax-check 超时（60s），请检查 Playbook 是否有阻塞写法'
        output = (proc.stderr or '') + (proc.stdout or '')
        return proc.returncode == 0, output.strip()
    finally:
        os.unlink(tmp_path)
