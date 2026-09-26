# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：巡检采集——复用执行引擎引擎的 raw/shell 通道批量收集主机数据。
#
# 设计：全部内置检查的采集命令合并成一条 shell 脚本（每项输出 base64 后拼一行
# JSON），一轮巡检对全部主机只发一次 `ansible all -m shell`，不另起 SSH 连接
# （约定）。base64 中转原因：df/ss 等输出含引号、竖线、换行，直接嵌 JSON 要做
# shell 层与 JSON 层双重转义，base64 字符集天然是合法 JSON 字符串内容，零转义坑。
# 命令用 subprocess 列表传参（同 executor），脚本中的双引号不经本地 shell。
from apps.ansible_ops.executor import MAX_JOBS, RUNNING_KEY
from apps.ansible_ops.inventory import generate_inventory, safe_token
from pathlib import Path
import base64
import json
import os
import re
import signal
import subprocess
import threading

# ansible 执行超时（采集是轻量读取，与 executor.JOB_TIMEOUT=1800 区分；
# TimeoutExpired 由 run_round 捕获落 failed）
COLLECT_TIMEOUT = 300

# 证书到期检查扫描路径（glob 交给远端 shell 展开；文件不存在自动跳过）。
# 节点为按需自签场景：往这些路径放 PEM 即纳入巡检；无证书节点检查返回 ok。
CERT_PATHS = '/etc/nginx/ssl/*.pem /etc/ssl/local/*.pem /etc/ssl/certs/ssl-cert-snakeoil.pem'

# 采集项 → shell 片段（stdout 经 base64 -w0 压成单行再进 JSON；顺序即 JSON 键序）
COLLECTORS = (
    # 负载回退项：/proc/loadavg 在容器里是宿主值，仅当 PSI 不可用时使用
    ('loadavg', 'cut -d" " -f1-3 /proc/loadavg'),
    ('nproc', 'nproc'),
    # cgroup v2 CPU 压力（PSI）：容器内真实值，不随宿主失真
    ('cpupsi', 'grep "^some" /sys/fs/cgroup/cpu.pressure'),
    # cgroup v2 内存：memory.max（"max"=未设限）+ memory.current；两值同行空格分隔
    ('memcgroup', 'echo "$(cat /sys/fs/cgroup/memory.max) $(cat /sys/fs/cgroup/memory.current)"'),
    # /proc/meminfo 回退项：cgroup 未设限时计算使用率用
    ('meminfo', 'grep -E "^(MemTotal|MemAvailable)" /proc/meminfo'),
    ('df', 'df -kP'),
    # ss -ltnp 需 root 才能看到监听进程名，采集命令统一 -b（become root）
    ('ports', 'ss -ltnp'),
    # nginx 进程列表 + 安装状态（区分"未安装"与"装了但挂了"）
    ('procs', 'pgrep -ax nginx || true'),
    ('procpkg', 'dpkg -s nginx 2>/dev/null | grep "^Status: install ok installed" || true'),
    # 证书文件内容（PEM），FILE: 行分隔文件名；平台侧用 cryptography 解析 notAfter
    ('certs', f'for f in {CERT_PATHS}; do [ -f "$f" ] && {{ echo "FILE:$f"; base64 "$f"; }}; done || true'),
)


def _build_collect_script():
    """把采集项拼成一条产出单行合法 JSON 的 shell 脚本。

    逐段 printf 输出键名与引号、采集输出 base64 -w0 后 tr 去尾换行（GNU base64
    的 -w0 仍带换行，换行落进 JSON 字符串会使其非法）。引号配对：后续项 printf
    以 `","` 开头，首引号同时充当**前一个值的闭合引号**，再接逗号与新键名；
    首项以 `{"` 开头，尾部 `"}"` 收最后值的闭合引号与 JSON 结束。
    """
    parts = []
    for i, (key, cmd) in enumerate(COLLECTORS):
        # 首项输出 {"key":"；后续项以 "," 开头（首引号同时闭合前一个值）
        head = '{"' if i == 0 else '","'
        parts.append(f'printf \'{head}{key}":"\'')
        parts.append(f'{{ {cmd}; }} | base64 -w0 | tr -d \'\\n\'')
    parts.append('printf \'"}\'')
    return '; '.join(parts)


COLLECT_SCRIPT = _build_collect_script()

# 主机结果行：`alias | CHANGED | rc=0 >> <stdout>`（多行）或 `alias | FAILED! => {...}`
# 段落式解析：以结果行切分，stdout 归属到下一个结果行之前（与 executor.ADHOC_RE
# 同一套状态词口径；paramiko warning 等噪音行不匹配主机行模式，自然落入上一段）。
# [^\n]* 把行内剩余部分（`| rc=0 >>` / `=> {...}`）一并吃进匹配——m.end() 必须
# 落在行尾，否则管道与 rc 残片会混进段落开头破坏 JSON 解析
RESULT_LINE_RE = re.compile(
    r'^(\S+)\s*\|\s*(SUCCESS|CHANGED|FAILED|UNREACHABLE|SKIPPED)[^\n]*\n?', re.M)


def build_alias_map(hosts):
    """构建 别名→Host 映射，规则与 inventory.generate_inventory 完全一致
    （safe_token + 冲突追加 _{id}），保证能按输出行首别名反查回 Host 对象。"""
    alias_map, alias_seen = {}, {}
    for host in hosts:
        alias = safe_token(host.name)
        while alias in alias_seen:
            alias = f'{alias}_{host.id}'
        alias_seen[alias] = True
        alias_map[alias] = host
    return alias_map


def parse_collect_output(text):
    """按结果行段落切分 ansible 输出 → {别名: {'status': ..., 'stdout': ...}}。

    stdout 可能含目标机回显的伪造结果行（executor 注释已知局限），这里取每段
    以结果行开头的首个匹配为准，段落内容不再二次切分：采集 JSON 单行输出，
    主机别名互不相同，首匹配已足够定位。
    """
    matches = list(RESULT_LINE_RE.finditer(text))
    segments = {}
    for i, m in enumerate(matches):
        stdout = text[m.end():matches[i + 1].start()] if i + 1 < len(matches) else text[m.end():]
        segments[m.group(1)] = {'status': m.group(2), 'stdout': stdout.strip()}
    return segments


def decode_payload(stdout):
    """把采集脚本输出的单行 JSON 解码为 {采集项: 文本}（base64 还原）。

    任一环节损坏（非 JSON / 键缺失）返回 None，调用方按主机采集失败处理，
    不让单台坏输出炸整轮。
    """
    try:
        raw = json.loads(stdout.strip())
        return {k: base64.b64decode(v).decode('utf-8', errors='replace') for k, v in raw.items()}
    except (ValueError, TypeError):
        return None


def run_collection(hosts, work_dir, timeout=COLLECT_TIMEOUT):
    """一轮采集：生成 inventory → 对全部主机发一条 shell 采集 → 解析逐主机数据。

    返回 {别名: {'status', 'payload'}}；payload 为 None 表示该主机采集失败
    （命令失败或输出损坏）。become root 依据：节点统一 ops 用户 + sudo 免密
    （节点镜像约定），ss -ltnp/证书读取需要 root 视角。
    """
    work_dir = Path(work_dir)
    inv_path = work_dir / 'inspection_inventory.ini'
    inv_path.write_text(generate_inventory(hosts, work_dir / 'keys'), encoding='utf-8')
    command = [
        'ansible', 'all', '-i', str(inv_path), '-f', '10',
        '-m', 'shell', '-a', COLLECT_SCRIPT, '-b', '-T', '30',
    ]
    env = dict(os.environ, ANSIBLE_HOST_KEY_CHECKING='False')
    # Popen + 看门狗杀进程组（executor._on_timeout 同款）：subprocess.run(timeout=)
    # 只杀 ansible 父进程，同组的 ssh/fork 子进程会成孤儿继续占内存
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors='replace', env=env, start_new_session=True)
    watchdog = threading.Timer(timeout, _kill_group, args=(proc,))
    watchdog.start()
    try:
        stdout, _ = proc.communicate()
    finally:
        watchdog.cancel()
    segments = parse_collect_output(stdout or '')
    # 反查表必须基于全量主机的别名分配（同名冲突时后续主机会带 _{id} 后缀，
    # 对单台临时建 map 会得到与 inventory 不一致的别名）
    alias_map = build_alias_map(hosts)
    alias_by_host_id = {h.id: a for a, h in alias_map.items()}
    results = {}
    for host in hosts:
        alias = alias_by_host_id[host.id]
        seg = segments.get(alias)
        results[alias] = {
            'status': seg['status'] if seg else 'UNREACHABLE',
            'payload': decode_payload(seg['stdout']) if seg else None,
        }
    return results


def _kill_group(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass  # 进程组已退出即为期望结果


class HostRunner:
    """检查函数的数据入口（约定协议中的 runner）。

    get(key) 返回采集项解码后的文本；run(cmd) 为自定义检查的逃生舱——对
    绑定主机现场补跑一条命令（复用同一 inventory，仍走 ansible 通道）。
    """

    def __init__(self, host, payload, exec_fn=None):
        self.host = host
        self._payload = payload or {}
        self._exec_fn = exec_fn

    def get(self, key, default=None):
        return self._payload.get(key, default)

    def run(self, cmd, timeout=60):
        if self._exec_fn is None:
            raise RuntimeError('runner 未配置现场执行通道')
        return self._exec_fn(self.host, cmd, timeout)


def try_acquire_slot(rds, wait_seconds=120, poll_interval=5):
    """占用引擎并发名额（与手动执行共享 MAX_JOBS=2，见 executor.RUNNING_KEY 注释）。

    最多等待 wait_seconds（定时轮次撞上手动高峰时排队而非整轮丢弃），
    占不到返回 False。redis 不可用时 rds 为 None：无并发控制可言，直接放行。
    """
    if rds is None:
        return True
    import time
    deadline = wait_seconds
    while deadline >= 0:
        if rds.incr(RUNNING_KEY) <= MAX_JOBS:
            return True
        rds.decr(RUNNING_KEY)
        if deadline == 0:
            return False
        time.sleep(poll_interval)
        deadline -= poll_interval
    return False


def release_slot(rds):
    if rds is not None:
        try:
            rds.decr(RUNNING_KEY)
        except Exception:
            pass  # 与 executor 同口径：释放失败宁可少算，不阻塞后续轮次
