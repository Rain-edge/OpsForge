# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎（subprocess 直跑，并发上限 2）；
# Playbook 管理 解锁 Playbook 执行（ansible-playbook，文件取自版本库）
from django.conf import settings
from django.db import close_old_connections
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from apps.ansible_ops.models import AnsibleJob
from apps.ansible_ops.inventory import generate_inventory
from apps.ansible_ops.repo import playbook_file
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from libs import human_datetime


# 平台总预算 4.5GB：同时最多 2 个 job（约定），单 job 内 fork 数
# 固定 10，7 节点一轮跑完（峰值约 150MB ssh 子进程）。
# 并发硬保证走 redis 计数（RUNNING_KEY）：gunicorn 多进程部署下，模块级线程池
# 的 max_workers 只能约束单进程，跨进程名额以 redis incr/decr 分配与回收
MAX_JOBS = 2
RUNNING_KEY = 'spug:ansible:running'
JOB_TIMEOUT = 1800
# kill 链路：pid 与终止标记走 redis——kill 视图与 executor 线程可能
# 位于不同 gunicorn worker 进程（start-api.sh -w 2），跨进程只能借 redis 传递
PID_KEY = 'spug:ansible:pid:{}'
KILL_KEY = 'spug:ansible:killed:{}'

_pool = ThreadPoolExecutor(max_workers=MAX_JOBS, thread_name_prefix='ansible_ops')
LOG_DIR = Path(settings.BASE_DIR) / 'storage' / 'ansible_logs'

# ansible default callback 的 recap 行，字段顺序固定：
#   web-01 : ok=2   changed=0   unreachable=0   failed=0   skipped=0 ...
RECAP_RE = re.compile(
    r'^(\S+)\s*:.*?ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)', re.M)

# ad-hoc（ansible 命令）不输出 recap，逐主机结果行形如：
#   web-01 | SUCCESS => {...}   /   db-01 | UNREACHABLE! =>
# 固有局限：按输出行匹配，远端命令回显恰好含 "xxx | SUCCESS" 样式行会误计数；
# ad-hoc 场景输出可控，playbook 场景走 recap 精确解析，不受影响。
ADHOC_RE = re.compile(
    r'^\S+\s*\|\s*(SUCCESS|CHANGED|FAILED|UNREACHABLE|SKIPPED)', re.M)


def submit_job(job_id):
    _pool.submit(run_job, job_id)


def group_name(job_id):
    """约定：Channels layer group `ansible-job-{job_id}`（consumer 与 executor 共用）。"""
    return f'ansible-job-{job_id}'


def _push_event(job_id, event):
    # 推送是旁路：无观众（daphne 未起/组内空）或 layer 异常都不能影响执行与落库
    try:
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(group_name(job_id), event)
    except Exception:
        pass


def _rds():
    try:
        from django_redis import get_redis_connection
        return get_redis_connection()
    except Exception:
        return None


def _save_final(job_id, status, summary_dict):
    """终态落库并广播 done 事件（约定：终态事件一次）。"""
    job = AnsibleJob.objects.filter(pk=job_id).first()
    if not job:
        return
    job.status = status
    job.summary = json.dumps(summary_dict)
    job.finished_at = human_datetime()
    job.save()
    _push_event(job_id, {'type': 'done', 'status': status, 'summary': summary_dict})


def _on_timeout(job_id, proc):
    # 看门狗：保留原 subprocess.run(timeout=JOB_TIMEOUT) 的强杀语义，理由记为 timeout
    rds = _rds()
    if rds:
        rds.set(KILL_KEY.format(job_id), 'timeout', ex=120)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass


def parse_recap(output):
    """从 ansible 输出解析 recap，返回各主机计数的累计 dict；无 recap 返回 None。"""
    total = {'ok': 0, 'changed': 0, 'unreachable': 0, 'failed': 0}
    matched = False
    for m in RECAP_RE.finditer(output):
        matched = True
        _, ok, changed, unreachable, failed = m.groups()
        total['ok'] += int(ok)
        total['changed'] += int(changed)
        total['unreachable'] += int(unreachable)
        total['failed'] += int(failed)
    return total if matched else None


def parse_adhoc(output):
    """解析 ad-hoc 逐主机结果行（无 recap 时使用），SKIPPED 计入 ok。"""
    stats = {'ok': 0, 'changed': 0, 'unreachable': 0, 'failed': 0}
    matched = False
    for m in ADHOC_RE.finditer(output):
        matched = True
        tag = m.group(1)
        if tag in ('SUCCESS', 'SKIPPED'):
            stats['ok'] += 1
        elif tag == 'CHANGED':
            stats['changed'] += 1
        elif tag == 'UNREACHABLE':
            stats['unreachable'] += 1
        elif tag == 'FAILED':
            stats['failed'] += 1
    return stats if matched else None


def parse_output(output):
    """统一出口：playbook 用 recap 精确解析，ad-hoc 回退结果行统计。"""
    return parse_recap(output) or parse_adhoc(output)


def build_command(job, inv_path, work_dir):
    extra_vars = json.loads(job.extra_vars)
    extra_args = []
    if extra_vars:
        vars_file = Path(work_dir) / 'extra_vars.json'
        vars_file.write_text(json.dumps(extra_vars), encoding='utf-8')
        extra_args = ['-e', f'@{vars_file}']
    if job.playbook_id:
        # Playbook 执行。文件即版本库内 {id}.yml（repo.py 单一事实源，
        # 平台编辑/回滚后内容随之变化）；缺失说明卷被清理或未走平台创建
        path = playbook_file(job.playbook_id)
        if not path.exists():
            raise Exception(f'Playbook 文件缺失：{path}')
        return ['ansible-playbook', '-i', str(inv_path), '-f', '10', str(path)] + extra_args
    if not job.raw_module:
        # playbook 任务在排队期间 Playbook 被删：外键 SET_NULL 置空 playbook_id，
        # raw_module 也是空，无法构造任何命令，给出可读错误落 failed
        raise Exception('Playbook 已被删除，任务无法执行')
    raw = json.loads(job.raw_module)
    args = ['ansible', '-i', str(inv_path), 'all', '-f', '10']
    args += ['-m', raw['name']]
    if raw.get('args'):
        args += ['-a', raw['args']]
    return args + extra_args


def run_job(job_id):
    close_old_connections()
    log_path = LOG_DIR / f'{job_id}.log'
    work_dir = Path(tempfile.mkdtemp(prefix=f'ansible_ops_{job_id}_'))
    log = None
    proc = None
    rds = _rds()
    try:
        # pending 排队期间被终止（kill 视图对排队任务预置标记）：不执行直接落终态；
        # 理由取标记值（killed/timeout），不硬编码，防误记
        killed = rds.get(KILL_KEY.format(job_id)) if rds else None
        if killed:
            rds.delete(KILL_KEY.format(job_id))
            _save_final(job_id, AnsibleJob.STATUS_FAILED, {'reason': killed.decode()})
            return
        # views 层提交前已在 redis 占用并发名额，本函数 finally 释放；
        # get 放入 try：job 被并发删除等异常也走统一落库，不裸抛进线程池
        job = AnsibleJob.objects.get(pk=job_id)
        host_ids = json.loads(job.host_ids)
        log = log_path.open('w', encoding='utf-8')
        job.status = AnsibleJob.STATUS_RUNNING
        job.log_path = str(log_path)
        job.save()

        from apps.host.models import Host
        hosts = Host.objects.filter(id__in=host_ids)
        if not hosts:
            raise Exception('未找到任何有效主机')
        inv_path = work_dir / 'inventory.ini'
        inv_path.write_text(generate_inventory(hosts, work_dir / 'keys'), encoding='utf-8')
        command = build_command(job, inv_path, work_dir)
        log.write(f'# AnsibleJob {job.id} | hosts={host_ids}\n')
        log.write(f'# command: {command}\n')
        log.write(f'# started: {human_datetime()}\n\n')
        log.flush()
        env = dict(os.environ, ANSIBLE_HOST_KEY_CHECKING='False')
        # 入口检查与置 running 之间存在竞窗（kill 视图按 pending 预置标记），
        # Popen 前最后复核一次，防止标记刚置上就让 ansible 跑起来
        if rds and rds.get(KILL_KEY.format(job.id)):
            rds.delete(KILL_KEY.format(job.id))
            _save_final(job.id, AnsibleJob.STATUS_FAILED, {'reason': 'killed'})
            return
        # 逐行流式：Popen 代替 run，行进途中同步写日志与 WS 推送；
        # start_new_session 让 ansible 及其 ssh 子进程独立成组，kill 按组终止
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors='replace', env=env, start_new_session=True)
        if rds:
            rds.set(PID_KEY.format(job.id), proc.pid, ex=JOB_TIMEOUT + 120)
        timer = threading.Timer(JOB_TIMEOUT, _on_timeout, args=(job.id, proc))
        timer.start()
        try:
            # readline 迭代而非 `for line in stdout`：后者受缓冲影响做不到逐行实时
            for line in iter(proc.stdout.readline, ''):
                log.write(line)
                log.flush()
                _push_event(job.id, {'type': 'log', 'line': line.rstrip('\n'), 'ts': human_datetime()})
            returncode = proc.wait()
        finally:
            timer.cancel()
        killed = rds.get(KILL_KEY.format(job.id)) if rds else None
        if rds:
            rds.delete(KILL_KEY.format(job.id))
        text = log_path.read_text(encoding='utf-8', errors='replace')
        recap = parse_output(text)
        if killed and returncode < 0:
            # 仅死于信号（kill SIGTERM=-15 / 看门狗 SIGKILL=-9）才采信标记；
            # 自然退出时的迟到标记按常规结果落库，防止把真实结果改写为 killed
            _save_final(job.id, AnsibleJob.STATUS_FAILED, {'reason': killed.decode()})
        elif recap is None:
            _save_final(
                job.id, AnsibleJob.STATUS_FAILED,
                {'error': f'ansible 退出码 {returncode}，无 recap 输出'})
        elif recap['failed'] == 0 and recap['unreachable'] == 0:
            _save_final(job.id, AnsibleJob.STATUS_SUCCESS, recap)
        elif recap['ok'] > 0:
            _save_final(job.id, AnsibleJob.STATUS_PARTIAL, recap)
        else:
            _save_final(job.id, AnsibleJob.STATUS_FAILED, recap)
    except Exception as e:
        # Popen 之后的任何异常：防御性终止子进程组，防孤儿 ansible 继续对真实
        # 主机执行并占用 fork/内存配额（PID_KEY 随 finally 清除，孤儿将不可 kill）
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
        # 单句柄顺序写：异常说明自然追加在 ansible 输出之后（原双句柄 seek 追加随流式化消除）
        if log is None:
            log = log_path.open('a', encoding='utf-8')
        log.write(f'\n# exception: {e}\n')
        _save_final(job_id, AnsibleJob.STATUS_FAILED, {'error': str(e)})
    finally:
        if log is not None:
            log.close()
        shutil.rmtree(work_dir, ignore_errors=True)
        if rds:
            try:
                rds.delete(PID_KEY.format(job_id))
            except Exception:
                pass
        try:
            from django_redis import get_redis_connection
            get_redis_connection().decr(RUNNING_KEY)
        except Exception:
            pass  # 名额释放失败宁可少算不可泄漏阻塞，redis 不可用时本就无并发可言
        close_old_connections()
