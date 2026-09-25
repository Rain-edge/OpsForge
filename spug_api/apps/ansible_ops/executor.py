# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎（subprocess 直跑，并发上限 2）
from django.conf import settings
from django.db import close_old_connections
from apps.ansible_ops.models import AnsibleJob
from apps.ansible_ops.inventory import generate_inventory
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import shutil
import subprocess
import tempfile
from libs import human_datetime


# 平台总预算 4.5GB：同时最多 2 个 job（约定），单 job 内 fork 数
# 固定 10，7 节点一轮跑完（峰值约 150MB ssh 子进程）。
# 并发硬保证走 redis 计数（RUNNING_KEY）：gunicorn 多进程部署下，模块级线程池
# 的 max_workers 只能约束单进程，跨进程名额以 redis incr/decr 分配与回收
MAX_JOBS = 2
RUNNING_KEY = 'spug:ansible:running'
JOB_TIMEOUT = 1800

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
    args = ['ansible', '-i', str(inv_path), 'all', '-f', '10']
    if job.playbook_id:
        # views 层已拦截，executor 兜底防御（Playbook 库随 Playbook 管理 落地）
        raise Exception('Playbook 库未上线（提供），当前仅支持 raw 模块')
    raw = json.loads(job.raw_module)
    args += ['-m', raw['name']]
    if raw.get('args'):
        args += ['-a', raw['args']]
    extra_vars = json.loads(job.extra_vars)
    if extra_vars:
        vars_file = Path(work_dir) / 'extra_vars.json'
        vars_file.write_text(json.dumps(extra_vars), encoding='utf-8')
        args += ['-e', f'@{vars_file}']
    return args


def run_job(job_id):
    close_old_connections()
    log_path = LOG_DIR / f'{job_id}.log'
    work_dir = Path(tempfile.mkdtemp(prefix=f'ansible_ops_{job_id}_'))
    log = None
    try:
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
        with log_path.open('a', encoding='utf-8') as output:
            proc = subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT,
                timeout=JOB_TIMEOUT, env=env)
        text = log_path.read_text(encoding='utf-8', errors='replace')
        recap = parse_output(text)
        if recap is None:
            job.status = AnsibleJob.STATUS_FAILED
            job.summary = json.dumps({'error': f'ansible 退出码 {proc.returncode}，无 recap 输出'})
        elif recap['failed'] == 0 and recap['unreachable'] == 0:
            job.status = AnsibleJob.STATUS_SUCCESS
            job.summary = json.dumps(recap)
        elif recap['ok'] > 0:
            job.status = AnsibleJob.STATUS_PARTIAL
            job.summary = json.dumps(recap)
        else:
            job.status = AnsibleJob.STATUS_FAILED
            job.summary = json.dumps(recap)
        job.finished_at = human_datetime()
        job.save()
    except Exception as e:
        # 头部句柄偏移停在写头部处，异常必须追加在 ansible 输出之后而非覆盖
        if log is not None:
            log.seek(0, os.SEEK_END)
        if log is None:
            log = log_path.open('a', encoding='utf-8')
        log.write(f'\n# exception: {e}\n')
        try:
            job = AnsibleJob.objects.get(pk=job_id)
        except AnsibleJob.DoesNotExist:
            job = None
        if job:
            job.status = AnsibleJob.STATUS_FAILED
            job.summary = json.dumps({'error': str(e)})
            job.finished_at = human_datetime()
            job.save()
    finally:
        if log is not None:
            log.close()
        shutil.rmtree(work_dir, ignore_errors=True)
        try:
            from django_redis import get_redis_connection
            get_redis_connection().decr(RUNNING_KEY)
        except Exception:
            pass  # 名额释放失败宁可少算不可泄漏阻塞，redis 不可用时本就无并发可言
        close_old_connections()
