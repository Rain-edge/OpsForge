# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：网络探测引擎——矩阵加载、asyncio 并发探测、断链状态机。
#
# 目标矩阵（约定：主机 × ICMP/TCP 端口 YAML）默认内置 net/matrix.yaml，
# env OPSFORGE_PROBE_MATRIX 覆盖。并发上限 50（约定）：asyncio.Semaphore，
# ICMP 走系统 ping 子进程（spug 容器内已实测可用），TCP 走 open_connection。
# 断链状态机（约定：连续 N 次失败走 ALERT 告警通道）：每（目标×检查项）
# 连续失败 ≥N（env OPSFORGE_NET_FAIL_THRESHOLD，默认 3）落 Alarm(status='1')，
# 恢复落 Alarm(status='2')；手动轮与定时轮走同一状态机（口径一致）。
import asyncio
import json
import os
import re
from datetime import datetime

import yaml
from django.db import transaction
from libs import human_datetime

from apps.alarm.models import Alarm
from apps.ansible_ops.models import NetProbeRun, NetTargetState

DEFAULT_MATRIX_PATH = os.path.join(os.path.dirname(__file__), 'matrix.yaml')
PROBE_CONCURRENCY = 50   # 约定：≤50 并发
TCP_TIMEOUT = 3          # 单次 TCP 连接超时秒
ICMP_TIMEOUT = 2         # ping -W 超时秒
FAIL_THRESHOLD_DEFAULT = 3
ALARM_TYPE = '网络探测'
# 目标名上限：Alarm.name(max_length=50) = name(35) + 空格 + check(9) + " 网络断链"(5)
NAME_MAX = 35


def fail_threshold():
    raw = os.environ.get('OPSFORGE_NET_FAIL_THRESHOLD')
    try:
        return int(raw) if raw else FAIL_THRESHOLD_DEFAULT
    except ValueError:
        return FAIL_THRESHOLD_DEFAULT


def load_matrix(path=None):
    """加载目标矩阵 → [{'name': str, 'checks': ['icmp', 'tcp:22', ...]}]。

    文件缺失/格式非法抛 ValueError（调度方记日志跳过本轮，不中断常驻进程）。
    """
    path = path or os.environ.get('OPSFORGE_PROBE_MATRIX') or DEFAULT_MATRIX_PATH
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    targets = []
    seen = set()
    for item in data.get('targets') or []:
        name = str(item.get('name') or '').strip()
        if not name:
            raise ValueError(f'矩阵目标缺少 name：{item}')
        if len(name) > NAME_MAX:
            raise ValueError(f'矩阵目标 name 过长（≤{NAME_MAX}）：{name[:20]}…')
        raw_tcp = item.get('tcp') or []
        if isinstance(raw_tcp, int):  # 标量写法 tcp: 22 归一为列表
            raw_tcp = [raw_tcp]
        checks = []
        if item.get('icmp'):
            checks.append('icmp')
        for port in raw_tcp:
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError(f'矩阵目标 {name} 端口非法：{port}')
            checks.append(f'tcp:{port}')
        if not checks:
            raise ValueError(f'矩阵目标 {name} 未定义任何检查项')
        # (name, check) 去重：重复项会让 apply_state 同轮双计数，断链阈值被静默减半
        for check in checks:
            key = (name, check)
            if key in seen:
                raise ValueError(f'矩阵目标重复：{name} {check}')
            seen.add(key)
        targets.append({'name': name, 'checks': checks})
    if not targets:
        raise ValueError(f'探测矩阵为空：{path}')
    return targets


async def _probe_icmp(target):
    """系统 ping 单发探测 → cell dict。延迟优先取 ping 回显 time=（真实 RTT），
    无则退化取子进程墙钟耗时。OSError 兜底为 fail cell（ping 二进制缺失等
    环境问题不应穿透炸掉整轮）。"""
    import time
    t0 = time.monotonic()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            'ping', '-c', '1', '-W', str(ICMP_TIMEOUT), target,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), ICMP_TIMEOUT + 2)
    except asyncio.TimeoutError:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except OSError:
                pass  # 进程已自行退出，无需收尸
        return {'target': target, 'check': 'icmp', 'status': 'fail',
                'latency_ms': None, 'detail': 'ping 进程超时'}
    except OSError as e:
        return {'target': target, 'check': 'icmp', 'status': 'fail',
                'latency_ms': None, 'detail': f'ping 启动失败: {e}'}
    if proc.returncode != 0:
        return {'target': target, 'check': 'icmp', 'status': 'fail',
                'latency_ms': None, 'detail': f'ping 退出码 {proc.returncode}'}
    match = re.search(r'time[=<]([\d.]+)', stdout.decode(errors='ignore'))
    # 容器内 RTT 常 <1ms，保留 1 位小数（int 截断会把 0.4ms 压成 0ms）
    latency = round(float(match.group(1)), 1) if match else round((time.monotonic() - t0) * 1000, 1)
    return {'target': target, 'check': 'icmp', 'status': 'ok',
            'latency_ms': latency, 'detail': None}


async def _probe_tcp(target, port):
    """asyncio TCP 连接探测 → cell dict。"""
    check = f'tcp:{port}'
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(target, port), TCP_TIMEOUT)
    except ConnectionRefusedError:
        return {'target': target, 'check': check, 'status': 'fail',
                'latency_ms': None, 'detail': '连接被拒绝'}
    except asyncio.TimeoutError:
        return {'target': target, 'check': check, 'status': 'fail',
                'latency_ms': None, 'detail': '连接超时'}
    except OSError as e:
        return {'target': target, 'check': check, 'status': 'fail',
                'latency_ms': None, 'detail': f'{type(e).__name__}: {e}'}
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass  # 半开连接关闭异常不影响探测结论（连接已成功）
    return {'target': target, 'check': check, 'status': 'ok',
            'latency_ms': None, 'detail': None}


async def _collect_cells(targets):
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def _run(target, check):
        async with sem:
            if check == 'icmp':
                return await _probe_icmp(target)
            return await _probe_tcp(target, int(check.split(':', 1)[1]))

    pairs = [(t['name'], c) for t in targets for c in t['checks']]
    tasks = [_run(name, check) for name, check in pairs]
    # return_exceptions：单 cell 异常不炸整轮，统一转 fail cell（与 ICMP OSError
    # 兜底同层防御，探测引擎故障只降级数据不中断调度）
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    cells = []
    for (name, check), result in zip(pairs, raw):
        if isinstance(result, Exception):
            cells.append({'target': name, 'check': check, 'status': 'fail',
                          'latency_ms': None, 'detail': f'{type(result).__name__}: {result}'})
        else:
            cells.append(result)
    return cells


def run_round(trigger):
    """一轮探测（同步入口，内部 asyncio.run）：快照落库 + 状态机推进。"""
    targets = load_matrix()
    cells = asyncio.run(_collect_cells(targets))
    total_ok = sum(1 for c in cells if c['status'] == 'ok')
    run = NetProbeRun.objects.create(
        trigger=trigger, total_ok=total_ok, total_fail=len(cells) - total_ok,
        cells=json.dumps(cells, ensure_ascii=False))
    apply_state(cells)
    return run


def _alarm_name(cell):
    # 告警名不含失败次数等变化值：断链/恢复两条记录同名，报警历史可对照；
    # 且不匹配任何自愈规则关键字（heal 匹配 name/target/type 子串），自然旁路
    return f'{cell["target"]} {cell["check"]} 网络断链'


def _duration_since(first_fail_at, now):
    """断链持续秒数（恢复告警的 duration 展示值）；解析失败返回 0。"""
    try:
        start = datetime.strptime(first_fail_at, '%Y-%m-%d %H:%M:%S')
        return max(int((datetime.strptime(now, '%Y-%m-%d %H:%M:%S') - start).total_seconds()), 0)
    except (TypeError, ValueError):
        return 0


def apply_state(cells, threshold=None):
    """断链状态机（约定：连续 N 次失败 → 记录并走 ALERT 告警通道）。

    逐 cell 事务 + 行锁（select_for_update，SQLite 测试库退化为普通读）防
    定时轮与手动轮并发双计数；告警记录只在状态跃迁时产生（不重复轰炸）。
    """
    threshold = threshold or fail_threshold()
    now = human_datetime()
    for cell in cells:
        NetTargetState.objects.get_or_create(
            target=cell['target'], check_name=cell['check'])
        with transaction.atomic():
            st = NetTargetState.objects.select_for_update().get(
                target=cell['target'], check_name=cell['check'])
            if cell['status'] == 'ok':
                if st.alarm_active:
                    seconds = _duration_since(st.first_fail_at, now)
                    Alarm.objects.create(
                        name=_alarm_name(cell), type=ALARM_TYPE,
                        target=cell['target'], status='2',
                        duration=f'{seconds}秒', notify_mode='[]', notify_grp='[]')
                st.alarm_active = False
                st.consecutive_failures = 0
                st.first_fail_at = None
            else:
                st.consecutive_failures += 1
                if st.first_fail_at is None:
                    st.first_fail_at = now
                if st.consecutive_failures >= threshold and not st.alarm_active:
                    Alarm.objects.create(
                        name=_alarm_name(cell), type=ALARM_TYPE,
                        target=cell['target'], status='1',
                        duration=f'连续{st.consecutive_failures}次失败',
                        notify_mode='[]', notify_grp='[]')
                    st.alarm_active = True
            st.updated_at = now
            st.save()
