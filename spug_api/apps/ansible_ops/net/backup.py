# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：netmiko 设备配置备份与变更审计。
#
# 设备清单默认内置 net/devices.yaml（env OPSFORGE_NET_DEVICES 覆盖）；netmiko
# device_type=linux 经 SSH 执行 vtysh 采集 show running-config（FRR 官方镜像
# 无 netmiko 原生驱动，SSH+vtysh 是任务踩坑建议口径，r1/r2 实测可用）。
# 备份落 storage/netbackup/{device}/{ts}.cfg（storage 为持久卷，重建不丢）；
# 与最近一份存量版本逐行 diff——内容有变化才写新文件并落 NetConfigChange，
# 「相邻版本」即相邻的不同配置版本，回溯任意时间点取该时点前最近版本（验收 3）。
# 口令读取（不进仓库）：设备项 password_env 指名的进程环境变量优先，其次
# 配置文件 NET_DEVICE_ENV_FILE（默认 storage/net_devices.env，KEY=VALUE 行格式，
# 沿用 heal_webhook.env 的 env+文件兜底惯例且 storage 为持久卷）。
import difflib
import fcntl
import os
from datetime import datetime
from pathlib import Path

import yaml
from django.conf import settings
from libs import human_datetime

from apps.ansible_ops.models import NetConfigChange

DEFAULT_DEVICES_PATH = os.path.join(os.path.dirname(__file__), 'devices.yaml')
TS_FORMAT = '%Y%m%d-%H%M%S'


def netbackup_dir():
    return Path(settings.BASE_DIR) / 'storage' / 'netbackup'


def device_env_file():
    return Path(os.environ.get(
        'NET_DEVICE_ENV_FILE',
        Path(settings.BASE_DIR) / 'storage' / 'net_devices.env'))


def _device_password(device):
    """设备口令：password_env 指名的环境变量优先，其次配置文件 KEY=VALUE。"""
    key = device.get('password_env', 'OPS_PASSWORD')
    value = os.environ.get(key)
    if value:
        return value
    try:
        with open(device_env_file(), encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                if k.strip() == key:
                    return v.strip()
    except FileNotFoundError:
        pass
    raise ValueError(f'设备 {device.get("name")} 口令未配置（env {key} 或 {device_env_file()}）')


def load_devices(path=None):
    """设备清单 → [dict]；缺 name/host/username 抛 ValueError（调度方记日志）。"""
    path = path or os.environ.get('OPSFORGE_NET_DEVICES') or DEFAULT_DEVICES_PATH
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    devices = []
    for item in data.get('devices') or []:
        name = str(item.get('name') or '').strip()
        host = str(item.get('host') or '').strip()
        if not name or not host:
            raise ValueError(f'备份设备缺少 name/host：{item}')
        if not item.get('username'):
            raise ValueError(f'备份设备 {name} 缺少 username')
        devices.append(item)
    if not devices:
        raise ValueError(f'设备备份清单为空：{path}')
    return devices


def _clean_config(output):
    """剥掉 vtysh 警告行（% 前缀，如 vtysh.conf 缺失提示）与首尾空行，
    正文逐行保留（含 Building configuration 头部，内容恒定不影响 diff）。"""
    lines = (output or '').replace('\r\n', '\n').split('\n')
    lines = [ln for ln in lines if not ln.lstrip().startswith('%')]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines) + '\n'


def _latest_version(device_dir):
    """设备目录内最新一份 .cfg（ts 文件名字典序=时间序），无则 None。"""
    if not device_dir.is_dir():
        return None
    cfgs = sorted(device_dir.glob('*.cfg'))
    return cfgs[-1] if cfgs else None


def _collect(device):
    """netmiko 采集单台设备运行配置 → 清洗后文本。

    netmiko 延迟导入：单测环境可对 netmiko.ConnectHandler 打桩，不真连设备。
    """
    from netmiko import ConnectHandler
    connection = ConnectHandler(
        device_type=device.get('device_type', 'linux'),
        host=device['host'], port=int(device.get('port', 22)),
        username=device['username'],
        password=_device_password(device),
        timeout=15)
    try:
        output = connection.send_command_timing(
            device.get('command', "vtysh -c 'show running-config'"),
            read_timeout=30)
    finally:
        connection.disconnect()
    return _clean_config(output)


def backup_device(device, ts=None):
    """采集并落盘单台设备 → dict(name, ts, changed, error)。

    与最近存量版本内容相同则不写新文件不落变更（定时轮高频采集中防噪）；
    首次备份只落基线文件，无变更记录。采集/落盘/锁任一环节异常都记 error
    返回，不中断整轮。
    """
    name = device['name']
    ts = ts or datetime.now().strftime(TS_FORMAT)
    try:
        content = _collect(device)
    except Exception as e:
        return {'name': name, 'ts': ts, 'changed': False, 'error': f'{type(e).__name__}: {e}'}
    device_dir = netbackup_dir() / name
    device_dir.mkdir(parents=True, exist_ok=True)
    try:
        # 设备级文件锁：手动单轮与 supervisor 定时轮并发时串行化"读 latest→比对→写"，
        # 防两进程基于同一 latest 各写一份（diff 链跳版本/重复变更记录）
        with open(device_dir / '.lock', 'w') as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                latest = _latest_version(device_dir)
                if latest is not None and latest.read_text(encoding='utf-8') == content:
                    return {'name': name, 'ts': ts, 'changed': False, 'error': None}
                # 原子写：临时文件 + os.replace，读端不会见到半写文件；
                # tmp 前缀带点，不落入 *.cfg 版本 glob
                tmp_path = device_dir / f'.{ts}.cfg.tmp'
                tmp_path.write_text(content, encoding='utf-8')
                os.replace(tmp_path, device_dir / f'{ts}.cfg')
                if latest is not None:
                    diff = '\n'.join(difflib.unified_diff(
                        latest.read_text(encoding='utf-8').splitlines(), content.splitlines(),
                        fromfile=f'{latest.stem}.cfg', tofile=f'{ts}.cfg', lineterm=''))
                    NetConfigChange.objects.create(
                        device=name, from_ts=latest.stem, to_ts=ts, diff=diff,
                        detected_at=human_datetime())
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
    except Exception as e:
        return {'name': name, 'ts': ts, 'changed': False, 'error': f'{type(e).__name__}: {e}'}
    return {'name': name, 'ts': ts, 'changed': True, 'error': None}


def run_backup_round():
    """一轮备份：逐台采集落盘，单台任何异常记 error 不中断整轮。"""
    results = []
    for device in load_devices():
        try:
            results.append(backup_device(device))
        except Exception as e:
            results.append({'name': device.get('name', '?'), 'ts': None,
                            'changed': False, 'error': f'{type(e).__name__}: {e}'})
    return results
