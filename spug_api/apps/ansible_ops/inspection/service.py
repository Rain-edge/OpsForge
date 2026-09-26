# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：巡检轮次编排——采集 → 检查 → diff → 产物 → 落库。
#
# 执行形态：手动触发（API）与定时轮次（management command --loop）共用一条
# 编排路径，差异仅在 trigger 与 user；巡检采集走内部 subprocess（复用引擎的
# inventory 与 raw/shell 通道），不创建 AnsibleJob——执行历史保持用户任务纯净，
# 定时场景也无 request.user 可绑（该取舍已记录）。
import json
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from libs import human_datetime

from apps.ansible_ops.inspection import STATUS_CRIT, STATUS_OK, STATUS_WARN
from apps.ansible_ops.inspection import collect
from apps.ansible_ops.inspection.checks import run_checks
from apps.ansible_ops.inspection.report import (
    REPORTS_DIR, diff_results, render_html, write_manifest, write_xlsx)
from apps.ansible_ops.models import InspectionReport
from apps.host.models import Host

# 巡检轮次本身串行（同一时刻至多一轮；名额约束在采集阶段与手动执行共享）
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='inspection')

TS_FORMAT = '%Y%m%d-%H%M%S'


def submit_round(trigger, user=None):
    """创建 running 状态的轮次记录并异步执行，返回 report id（供前端轮询）。"""
    report = InspectionReport.objects.create(
        trigger=trigger, user=user, status=InspectionReport.STATUS_RUNNING)
    _pool.submit(_execute, report.id)
    return report.id


def _save_failed(report_id, message):
    report = InspectionReport.objects.filter(pk=report_id).first()
    if not report:
        return
    report.status = InspectionReport.STATUS_FAILED
    report.summary = json.dumps({'error': message})
    report.finished_at = human_datetime()
    report.save()


def _execute(report_id):
    from django.db import close_old_connections
    close_old_connections()
    try:
        _run_round(report_id)
    except Exception as e:
        # 编排层兜底：任何未预期异常都落到 report 终态，不留 running 幽灵行
        _save_failed(report_id, f'巡检执行异常: {e}')
    finally:
        close_old_connections()


def _run_round(report_id):
    report = InspectionReport.objects.get(pk=report_id)
    rds = None
    try:
        from django_redis import get_redis_connection
        rds = get_redis_connection()
    except Exception:
        rds = None
    acquired = False
    try:
        acquired = collect.try_acquire_slot(rds)
        if not acquired:
            _save_failed(report_id, '执行并发名额不足（等待超时），请稍后重试')
            return
        _collect_and_report(report)
    finally:
        # 只在真正拿到名额时释放：try_acquire_slot 失败路径内部已自还（净 0），
        # 无条件释放会把共享计数器净减造成名额泄漏式虚增
        if acquired:
            collect.release_slot(rds)


def _collect_and_report(report):
    # 宿主平台 v4.0.1 的 Host 为真删除（无软删字段），直接全量取纳管主机；
    # 工作区 spug_api 是 2.3.18 lineage 带 deleted_by，勿据此过滤（既定口径）
    hosts = list(Host.objects.all())
    if not hosts:
        _save_failed(report.id, '未找到任何被管主机')
        return
    work_dir = Path(tempfile.mkdtemp(prefix=f'inspection_{report.id}_'))
    try:
        collected = collect.run_collection(hosts, work_dir)
        alias_map = collect.build_alias_map(hosts)
        results, hosts_unreachable = {}, 0
        for alias, host in alias_map.items():
            seg = collected.get(alias) or {}
            payload = seg.get('payload')
            seg_status = seg.get('status') or 'UNREACHABLE'
            if seg_status in ('UNREACHABLE', 'FAILED') or payload is None:
                hosts_unreachable += 1
                # 区分连接层故障（不可达）与命令层故障（可达但采集失败），便于排障；
                # 两者同为 crit：平台无法确认节点健康本身就是异常
                if seg_status == 'FAILED' or (seg and payload is None):
                    reason = f'采集命令失败（{seg_status}），无法巡检'
                else:
                    reason = f'主机不可达（{seg_status}），无法巡检'
                results[host.name] = {
                    name: {'status': STATUS_CRIT, 'value': '-', 'message': reason}
                    for name in _check_names()
                }
                continue
            runner = collect.HostRunner(host, payload)
            results[host.name] = {
                r.check: {'status': r.status, 'value': r.value, 'message': r.message}
                for r in run_checks(runner, host)
            }

        # 容器 TZ=Asia/Shanghai（镜像 ENV），naive now 与 human_datetime 同口径；
        # 运行时 settings USE_TZ=False，timezone.localtime() 对 naive 会抛异常
        ts = datetime.now().strftime(TS_FORMAT)
        prev = (InspectionReport.objects
                .filter(status__in=[InspectionReport.STATUS_SUCCESS,
                                    InspectionReport.STATUS_PARTIAL])
                .exclude(pk=report.id).first())
        prev_results = _load_prev_results(prev)
        changes = diff_results(prev_results, results)

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        # 文件名带 report.id：手动轮与定时轮可并发（名额上限 2），同秒完成时
        # 纯时间戳文件名会互相覆盖，id 保证唯一
        base = f'inspection-{report.id}-{ts}'
        manifest_path = REPORTS_DIR / f'{base}.manifest.json'
        html_path = REPORTS_DIR / f'{base}.html'
        xlsx_path = REPORTS_DIR / f'{base}.xlsx'
        write_manifest(manifest_path, ts, results, changes)

        counts = {'ok': 0, 'warn': 0, 'crit': 0}
        for checks in results.values():
            for info in checks.values():
                counts[info['status']] += 1
        status = (InspectionReport.STATUS_PARTIAL if hosts_unreachable
                  else InspectionReport.STATUS_SUCCESS)
        trigger_label = dict(InspectionReport.TRIGGER_CHOICES)[report.trigger]
        render_html(html_path, {
            'ts': ts, 'report_id': report.id, 'trigger': trigger_label,
            'counts': counts, 'host_total': len(results),
            'changes': _template_changes(changes), 'host_rows': _template_rows(results),
        })
        write_xlsx(xlsx_path, ts, trigger_label, results, changes)

        report.status = status
        report.total_ok, report.total_warn, report.total_crit = (
            counts['ok'], counts['warn'], counts['crit'])
        report.hosts_total = len(results)
        report.hosts_unreachable = hosts_unreachable
        report.html_path = str(html_path)
        report.xlsx_path = str(xlsx_path)
        report.manifest_path = str(manifest_path)
        report.summary = json.dumps({
            'counts': counts, 'changes': changes, 'has_prev': prev is not None,
        }, ensure_ascii=False)
        report.finished_at = human_datetime()
        report.save()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _load_prev_results(prev_report):
    """读上一轮 manifest 的 results 供 diff；文件缺失/损坏视为无上轮（首轮语义）。"""
    if not prev_report or not prev_report.manifest_path:
        return None
    try:
        with open(prev_report.manifest_path, encoding='utf-8') as f:
            return json.load(f).get('results')
    except Exception:
        return None


def _check_names():
    from apps.ansible_ops.inspection import CHECKS
    return list(CHECKS)


STATUS_LABELS = {'ok': '正常', 'warn': '警告', 'crit': '异常'}


def _template_changes(changes):
    """diff 行翻译成模板友好的中文标签。"""
    from apps.ansible_ops.inspection.report import CHECK_LABEL
    out = []
    for c in changes:
        c = dict(c)
        c['check_label'] = CHECK_LABEL.get(c['check'], c['check'])
        c['from_label'] = STATUS_LABELS.get(c['from'], c['from'])
        c['to_label'] = STATUS_LABELS.get(c['to'], c['to'])
        out.append(c)
    return out


def _template_rows(results):
    """逐主机明细翻译成模板友好的行结构（保持注册顺序）。"""
    from apps.ansible_ops.inspection.report import CHECK_LABEL
    return [
        {'host': host, 'rows': [
            {'check': name, 'check_label': CHECK_LABEL.get(name, name),
             'status': info['status'],
             'status_label': STATUS_LABELS.get(info['status'], info['status']),
             'value': info.get('value') or '-', 'message': info.get('message') or ''}
            for name, info in checks.items()]
         }
        for host, checks in results.items()]
