# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：内置检查项（CPU / 内存 / 磁盘 / 进程 / 端口 / 证书）。
#
# 数据源口径：被管节点是 256MB 限额的容器，/proc/loadavg 与
# /proc/meminfo 反映的是宿主（WSL2 6GB）而非节点自身——内存与 CPU 压力一律
# 优先读节点自己的 cgroup v2（memory.max/memory.current、cpu.pressure PSI），
# cgroup 不可用才回退 /proc。磁盘 df 与端口 ss 是内核命名空间隔离的，无此问题。
import base64
import re

from apps.ansible_ops.inspection import (
    STATUS_CRIT, STATUS_OK, STATUS_WARN, CheckResult, register)

# 内存使用率阈值（占比）；磁盘使用率阈值（%）
MEM_WARN, MEM_CRIT = 0.85, 0.95
DISK_WARN, DISK_CRIT = 85, 95
# PSI some avg60 阈值（%）：过去 60s 因等 CPU 停滞的时间占比
PSI_WARN, PSI_CRIT = 20, 50
# 证书剩余天数阈值
CERT_WARN_DAYS, CERT_CRIT_DAYS = 30, 7
# 端口检查：期望监听者（节点装了对应服务才检查，避免 db/cache 误报）
PORT_EXPECTATIONS = {80: 'nginx'}


def _fnum(text, default=None):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _result(check, host, status, value, message):
    return CheckResult(check=check, host=host.name, status=status, value=value, message=message)


@register('cpu')
def check_cpu(runner, host):
    """CPU 压力：优先 cgroup PSI（some avg60），回退 load1/nproc。"""
    psi = runner.get('cpupsi')
    if psi:
        m = re.search(r'avg60=([\d.]+)', psi)
        if m:
            avg60 = _fnum(m.group(1), 0.0)
            status = STATUS_CRIT if avg60 > PSI_CRIT else STATUS_WARN if avg60 > PSI_WARN else STATUS_OK
            return _result('cpu', host, status, f'{avg60:.1f}%', f'PSI some avg60={avg60:.2f}%')
    loadavg = runner.get('loadavg')
    if loadavg:
        nproc = _fnum(runner.get('nproc'), 1) or 1
        load1 = _fnum(loadavg.split()[0], 0.0)
        ratio = load1 / nproc
        status = STATUS_CRIT if ratio > 4 else STATUS_WARN if ratio > 2 else STATUS_OK
        return _result('cpu', host, status, f'load1={load1:.2f}', f'load1/nproc={ratio:.2f}（宿主回退口径）')
    return _result('cpu', host, STATUS_WARN, '', 'CPU 数据缺失（PSI 与 loadavg 均不可用）')


@register('memory')
def check_memory(runner, host):
    """内存使用率：优先 cgroup v2（节点真实 256MB 限额），回退 /proc/meminfo。"""
    cgroup = (runner.get('memcgroup') or '').split()
    if len(cgroup) == 2:
        limit, usage = _fnum(cgroup[0]), _fnum(cgroup[1])
        if limit and limit > 0 and usage is not None:
            ratio = usage / limit
            status = STATUS_CRIT if ratio > MEM_CRIT else STATUS_WARN if ratio > MEM_WARN else STATUS_OK
            return _result('memory', host, status, f'{ratio * 100:.1f}%',
                           f'cgroup {usage / 1048576:.0f}MiB / {limit / 1048576:.0f}MiB')
    meminfo = runner.get('meminfo') or ''
    total = avail = None
    for line in meminfo.splitlines():
        if line.startswith('MemTotal:'):
            total = _fnum(line.split()[1])
        elif line.startswith('MemAvailable:'):
            avail = _fnum(line.split()[1])
    if total and avail is not None and total > 0:
        ratio = 1 - avail / total
        status = STATUS_CRIT if ratio > MEM_CRIT else STATUS_WARN if ratio > MEM_WARN else STATUS_OK
        return _result('memory', host, status, f'{ratio * 100:.1f}%',
                       'cgroup 未设限，回退 /proc/meminfo（宿主口径）')
    return _result('memory', host, STATUS_WARN, '', '内存数据缺失')


@register('disk')
def check_disk(runner, host):
    """磁盘：df -kP 全挂载点取最高使用率（含故障注入的独立挂载，不做 fs 类型过滤）。"""
    df = runner.get('df')
    if not df:
        return _result('disk', host, STATUS_WARN, '', 'df 数据缺失')
    worst_pct, worst_mount = -1, ''
    for line in df.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 6:
            continue
        try:
            pct = int(cols[4].rstrip('%'))
        except ValueError:
            continue
        if pct > worst_pct:
            worst_pct, worst_mount = pct, cols[5]
    if worst_pct < 0:
        return _result('disk', host, STATUS_WARN, '', 'df 输出无可解析行')
    status = STATUS_CRIT if worst_pct > DISK_CRIT else STATUS_WARN if worst_pct > DISK_WARN else STATUS_OK
    return _result('disk', host, status, f'{worst_pct}%', f'最高使用率挂载点 {worst_mount}')


@register('process')
def check_process(runner, host):
    """进程：nginx 装了必须在跑；未安装视为不适用（db/cache 节点常态）。"""
    installed = bool((runner.get('procpkg') or '').strip())
    if not installed:
        return _result('process', host, STATUS_OK, 'not installed', 'nginx 未安装，跳过')
    procs = (runner.get('procs') or '').strip()
    if procs:
        count = len([x for x in procs.splitlines() if x.strip()])
        return _result('process', host, STATUS_OK, f'{count} procs', 'nginx 运行中')
    return _result('process', host, STATUS_CRIT, 'stopped', 'nginx 已安装但进程不存在')


@register('port')
def check_port(runner, host):
    """端口：期望监听者与实际不符（含无人监听/被其他进程占用）→ crit。"""
    installed = bool((runner.get('procpkg') or '').strip())
    ss = runner.get('ports')
    if not ss:
        return _result('port', host, STATUS_WARN, '', 'ss 数据缺失')
    listeners = {}
    for line in ss.splitlines():
        m = re.search(r'[:.](\d+)\s', line + ' ')
        if not m:
            continue
        pm = re.search(r'users:\(\("([\w\-]+)"', line)
        listeners.setdefault(int(m.group(1)), set()).add(pm.group(1) if pm else '?')
    messages = []
    status = STATUS_OK
    for port, expected in PORT_EXPECTATIONS.items():
        if not installed and expected == 'nginx':
            continue  # 与 process 检查同口径：未装 nginx 的节点不做 80 端口检查
        names = listeners.get(port)
        if names is None:
            status = STATUS_CRIT
            messages.append(f'端口 {port} 无监听（预期 {expected}）')
        elif expected not in names:
            status = STATUS_CRIT
            messages.append(f'端口 {port} 被 {"、".join(sorted(names))} 占用（预期 {expected}）')
        else:
            messages.append(f'端口 {port} 由 {expected} 正常监听')
    if not messages:
        return _result('port', host, STATUS_OK, '-', '无适用端口检查（服务未安装）')
    return _result('port', host, status, '', '；'.join(messages))


@register('cert')
def check_cert(runner, host):
    """证书到期：读 notAfter，剩余天数 <7 crit / <30 warn；无证书节点 ok。

    时间口径：expire 与 now 统一 aware UTC（不受运行时 settings.USE_TZ 影响，
    运行容器 USE_TZ=False，timezone.now() 是 naive 不能与 aware 相减）。
    """
    payload = runner.get('certs')
    if not payload or not payload.strip():
        return _result('cert', host, STATUS_OK, '-', '节点未发现证书')
    import datetime as dt
    from cryptography import x509
    worst_status, worst_days, worst_subject = STATUS_OK, None, ''
    found = bad = False
    bad_path = ''
    for block in payload.split('FILE:'):
        if not block.strip():
            continue
        path, _, body = block.partition('\n')
        try:
            cert = x509.load_pem_x509_certificate(base64.b64decode(body.strip()))
            # cryptography≥42 用 not_valid_after_utc；旧版仅 naive 的 not_valid_after
            expire = getattr(cert, 'not_valid_after_utc', None)
            if expire is None:
                expire = cert.not_valid_after.replace(tzinfo=dt.timezone.utc)
            days = (expire - dt.datetime.now(dt.timezone.utc)).days
            found = True
        except Exception:
            bad = True
            bad_path = path.strip()
            continue  # 单张坏证书不丢弃同节点其余证书的检查
        if worst_days is None or days < worst_days:
            worst_days = days
            worst_subject = path.strip()
            worst_status = (
                STATUS_CRIT if days < CERT_CRIT_DAYS
                else STATUS_WARN if days < CERT_WARN_DAYS else STATUS_OK)
    if not found:
        return _result('cert', host, STATUS_WARN, '', f'证书解析失败 {bad_path}')
    return _result(
        'cert', host, worst_status, f'{worst_days}d',
        f'最早到期 {worst_subject}（剩余 {worst_days} 天）'
        + (f'；{bad_path} 解析失败' if bad else ''))


def run_checks(runner, host):
    """对单主机跑全部注册检查；检查函数自身异常记 warn（无法确认健康≠健康，
    但也不宜与真实故障混同 crit），不让单个检查的 bug 炸整轮。"""
    from apps.ansible_ops.inspection import CHECKS
    results = []
    for name, fn in CHECKS.items():
        try:
            results.append(fn(runner, host))
        except Exception as e:
            results.append(CheckResult(
                check=name, host=host.name, status=STATUS_WARN,
                value='', message=f'检查执行异常: {e}'))
    return results
