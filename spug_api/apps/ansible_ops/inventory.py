# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：动态 Inventory 生成
import hashlib
import os
import re
from pathlib import Path

from apps.setting.utils import AppSetting


def safe_token(value):
    """INI 关键字（组名/主机别名）只允许字母数字下划线连字符，其余一律替换为下划线。"""
    return re.sub(r'[^A-Za-z0-9_-]', '_', value or '')


def write_key_file(key_dir, private_key):
    """把 PEM 私钥文本落到 key_dir（若同 key 已写过则复用），返回文件名。

    os.open 一步以 0600 落盘，避免先写后 chmod 的 0644 窗口。
    """
    key_dir = Path(key_dir)
    key_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(private_key.encode()).hexdigest()[:12]
    path = key_dir / f'{digest}.pem'
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(private_key)
    return path.name


def generate_inventory(hosts, key_dir=None):
    """生成 INI 格式 Inventory 文本。

    - 组 = 平台主机分组（v4.0.1 host_groups，Host.groups M2M），未分组主机归 [ungrouped]
    - 每主机行：别名 ansible_host=连接地址 ansible_port=端口 ansible_user=用户名
      ansible_ssh_private_key_file=私钥文件（host.private_key，即 hosts.pkey 或全局密钥）
    - key_dir 为私钥临时文件目录（executor 每次执行创建，结束删除）；为 None 时不写
      私钥变量（依赖 ansible 默认密钥路径）
    """
    lines = {}
    alias_seen = {}
    for host in hosts:
        # 连接地址与用户名直接拼入 INI 行，含空白字符会破坏行结构（INJ 防御：
        # 平台建机时有真连验证兜底，这里再显式拒绝，让 job failed 而非静默错跑）
        if re.search(r'\s', host.hostname + host.username):
            raise ValueError(f'主机 {host.name!r} 的连接地址或用户名含空白字符')
        # 与 Host.private_key 同口径（pkey 优先、回退全局密钥），但用 get_default
        # 防御：全局密钥未配置时该主机不写密钥变量（执行时报 unreachable），不炸整个 job
        private_key = host.pkey or AppSetting.get_default('private_key')
        key_file = None
        if key_dir and private_key:
            key_file = write_key_file(key_dir, private_key)
        alias = safe_token(host.name)
        while alias in alias_seen:
            alias = f'{alias}_{host.id}'
        alias_seen[alias] = True
        line = (f'{alias} ansible_host={host.hostname} ansible_port={host.port} '
                f'ansible_user={host.username}')
        if key_file:
            line += f' ansible_ssh_private_key_file={key_dir}/{key_file}'
        groups = list(host.groups.all())
        if groups:
            for group in groups:
                lines.setdefault(safe_token(group.name), []).append(line)
        else:
            lines.setdefault('ungrouped', []).append(line)
    chunks = []
    for group, host_lines in lines.items():
        chunks.append(f'[{group}]')
        chunks.extend(host_lines)
    return '\n'.join(chunks) + '\n' if chunks else ''
