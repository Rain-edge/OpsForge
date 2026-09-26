# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：告警闭环自愈服务（触发链主体，见包 __init__ 的链路图）。
#
# 挂点（读源码后选定，零侵入上游）：Alarm.objects.create 是监控主链路"告警
# 发生"的必经点（monitor.utils.handle_notify），本模块经 post_save signal
# 挂在其上，上游文件零改动（Dockerfile 里上游代码是构建期 git clone 的
# v4.0.1 原件，工作区改 monitor/ 既不生效也不该改——基线 lineage 见 RUNBOOK §1）。
# 已知耦合：若上游未来改掉 handle_notify 的落库行为，signal 失效——已记录待改进。
# Alarm 无检测输出字段，匹配材料为 告警名/目标/类型；恢复告警（status='2'）
# 不触发闭环。host 对象从 target 反查（进程/脚本检测的 target 形如
# 'web-01(172.18.0.11)'），站点/端口/Ping 告警反查不到主机则不触发。
#
# 线程模型：signal 同步段只做匹配与去重（毫秒级）；闭环主体（playbook +
# 复检，动辄数十秒）放独立 daemon 线程，不占用 monitor worker 的线程池配额。
# 全链路异常吞噬或落库，绝不外抛进 monitor 主链路（红线：
# 自愈故障不能连坐监控告警）。
import json
import logging
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from django.db import close_old_connections
from libs import human_datetime

from apps.ansible_ops.heal import notify

logger = logging.getLogger(__name__)

# 闭环时长预算：playbook 终态等待与复检重试都要有上限，防闭环线程无限挂起；
# 上限必须远宽于端到端 ≤60s 的验收指标，给偶发慢节点留余量
PLAYBOOK_WAIT_TIMEOUT = 300
RECHECK_RETRIES = 3     # 自愈后复检重试次数（服务重启到就绪有延迟）
RECHECK_INTERVAL = 5    # 复检重试间隔秒
SLOT_WAIT_TIMEOUT = 120  # 引擎并发名额排队上限（同巡检轮次语义）


def handle_alert(sender, instance, created, **kwargs):
    """post_save(Alarm) handler（注册见 signals.py）：告警落库即触发匹配。

    全函数不抛异常：匹配失败、规则缺失、任何内部错误都只记日志——
    signal 若抛异常会炸掉 monitor 的告警主链路（handle_notify），这里必须自守。
    """
    try:
        if not created or instance.status != '1':  # '1'=报警发生，恢复告警不触发
            return
        _handle_alert(instance)
    except Exception:
        close_old_connections()
        logger.exception('heal: handle_alert 异常（alert=%s target=%s）',
                         getattr(instance, 'id', None), getattr(instance, 'target', ''))


def _handle_alert(alarm):
    from apps.ansible_ops.models import HealRule, HealEvent
    from apps.ansible_ops.inspection import CHECKS
    from apps.host.models import Host

    # 网络模块网络断链告警不参与自愈：断链（节点停机/链路不通）不是 heal playbook
    # 可修复的故障类型；且其 target 即节点名，易与进程类规则的关键字撞车造成误触发
    if alarm.type == '网络探测':
        return
    # 进程/脚本检测的 target 形如 'web-01(172.18.0.11)'（monitor 拼装），
    # 取括号前的主机名反查 Host；其余类型（站点/端口/Ping）反查不到则不触发
    host = Host.objects.filter(name=(alarm.target or '').split('(')[0]).first()
    host_name = host.name if host else ''
    haystack = f'{alarm.name} {alarm.target} {alarm.type}'.lower()

    rule = None
    for r in HealRule.objects.filter(is_active=True):
        kw = (r.match_keyword or '').lower()
        if not kw or kw not in haystack:
            continue
        if r.match_host and r.match_host != host_name:
            continue
        if r.check_name not in CHECKS:
            logger.warning('heal: 规则 %s 的复检插件 %s 未注册，跳过', r.name, r.check_name)
            continue
        rule = r
        break
    if rule is None:
        return
    if host is None:
        # 自愈必须针对具体主机执行 playbook；无主机对象的告警当前规则体系
        # 不支持（不建事件，避免无法闭环的僵尸记录）
        logger.warning('heal: 规则 %s 命中无主机告警（target=%s），不触发',
                       rule.name, alarm.target)
        return

    def _event_fields(status):
        return dict(
            rule=rule, host=host_name, alert_id=alarm.id, alert_name=alarm.name,
            alert_target=str(alarm.target or ''),
            alert_message=f'{alarm.type} {alarm.name}',
            status=status)

    # 防自愈风暴两道闸：同规则同主机的闭环进行中，或最近事件在冷却窗口内。
    # created_at 是 human_datetime 定长字符串（字典序=时间序），字符串比较安全
    threshold = (datetime.now() - timedelta(minutes=rule.cooldown)).strftime('%Y-%m-%d %H:%M:%S')
    qs = HealEvent.objects.filter(rule=rule, host=host_name)
    in_progress = qs.filter(status=HealEvent.STATUS_RUNNING).exists()
    cooled = (not in_progress
              and qs.exclude(status=HealEvent.STATUS_SUPPRESSED)
              .filter(created_at__gte=threshold).exists())
    if in_progress or cooled:
        reason = '闭环进行中' if in_progress else f'冷却窗口内（{rule.cooldown} 分钟）'
        logger.warning('heal: 规则 %s 主机 %s 告警被抑制：%s', rule.name, host_name, reason)
        HealEvent.objects.create(
            detail=json.dumps({'reason': reason}, ensure_ascii=False),
            finished_at=human_datetime(), duration_ms=0,
            **_event_fields(HealEvent.STATUS_SUPPRESSED))
        _notify_event('suppressed', rule=rule, host=host_name,
                      alert=alarm.name, target=alarm.target, error=reason)
        return

    event = HealEvent.objects.create(**_event_fields(HealEvent.STATUS_RUNNING))
    threading.Thread(target=_run_heal, args=(event.id,), daemon=True,
                     name=f'heal-{event.id}').start()


def _run_heal(event_id):
    """闭环线程入口：任何未捕获异常落 failed（不留悬挂 running 记录）。"""
    try:
        _run_heal_inner(event_id)
    except Exception as exc:
        close_old_connections()
        logger.exception('heal: 闭环 %s 执行异常', event_id)
        try:
            from apps.ansible_ops.models import HealEvent
            event = HealEvent.objects.filter(pk=event_id).first()
            if event and event.status == HealEvent.STATUS_RUNNING:
                _finish_event(event, HealEvent.STATUS_FAILED, after=None,
                              error=f'闭环执行异常：{type(exc).__name__}（详见 worker 日志）')
                _notify_event('failed', event=event, error=f'闭环执行异常：{type(exc).__name__}')
        except Exception:
            logger.exception('heal: 闭环 %s 异常落库失败', event_id)
    finally:
        close_old_connections()


def _run_heal_inner(event_id):
    from apps.ansible_ops.models import HealEvent
    from apps.host.models import Host

    close_old_connections()
    t0 = time.monotonic()
    event = HealEvent.objects.select_related('rule').get(pk=event_id)
    rule = event.rule
    host = Host.objects.filter(name=event.host).first() if event.host else None

    # ① 自愈前复检：告警到闭环启动有延迟（检测间隔 + 队列），故障可能已自愈；
    #    已恢复则不白跑 playbook（"误报/先愈"路径，闭环照样成立）
    before = _recheck_with_retries(rule, host, retries=1) if host else None
    before_status = before.status if before else 'unknown'
    HealEvent.objects.filter(pk=event.id).update(before_status=before_status)
    if before is not None and before.status == 'ok':
        duration = int((time.monotonic() - t0) * 1000)
        HealEvent.objects.filter(pk=event.id).update(
            status=HealEvent.STATUS_RECOVERED, after_status=before.status,
            detail=json.dumps({'note': '自愈前复检已恢复，未执行 Playbook'},
                              ensure_ascii=False),
            finished_at=human_datetime(), duration_ms=duration)
        _notify_event('recovered', event=event, before=before_status,
                      after=before.status, duration_ms=duration,
                      error='告警时故障已不存在（自愈前复检确认），未执行 Playbook')
        return

    # ② 执行修复 Playbook（同步等待终态）
    job, job_error = _run_playbook(rule, host)
    if job is not None:
        HealEvent.objects.filter(pk=event.id).update(job=job)
        event.job_id = job.id  # 快照同步：外发 payload 要带 job 号（演示与排障关键证据）
    job_ok = job is not None and job.status in (job.STATUS_SUCCESS, job.STATUS_PARTIAL)
    if not job_ok:
        duration = int((time.monotonic() - t0) * 1000)
        _finish_event(event, HealEvent.STATUS_FAILED, after=None,
                      error=job_error or f'自愈 job 状态异常：{job.status if job else "未创建"}',
                      duration_ms=duration)
        _notify_event('failed', event=event, before=before_status,
                      duration_ms=duration, error=job_error)
        return

    # ③ 自愈后复检（重试等就绪）；无法确认恢复 ≠ 恢复，标 failed 升级人工
    after = _recheck_with_retries(rule, host)
    after_status = after.status if after else 'unknown'
    recovered = after is not None and after.status == 'ok'
    duration = int((time.monotonic() - t0) * 1000)
    _finish_event(event,
                  HealEvent.STATUS_RECOVERED if recovered else HealEvent.STATUS_FAILED,
                  after=after_status,
                  error=None if recovered else (f'自愈后复检仍为 {after_status}' if after else '自愈后复检采集失败'),
                  duration_ms=duration)
    _notify_event('recovered' if recovered else 'failed', event=event,
                  before=before_status, after=after_status, duration_ms=duration,
                  error=None if recovered else '自愈后复检未恢复，需人工介入')


def _finish_event(event, status, after, error, duration_ms=None):
    """闭环终点统一落库（status/detail/after/duration/finished_at）。"""
    from apps.ansible_ops.models import HealEvent
    HealEvent.objects.filter(pk=event.id).update(
        status=status, after_status=after,
        detail=json.dumps({'error': error} if error else {}, ensure_ascii=False),
        finished_at=human_datetime(), duration_ms=duration_ms)


def _notify_event(status, event=None, rule=None, host='', alert='', target='',
                  before=None, after=None, duration_ms=None, error=None):
    """组装外发 payload 并推送；外发失败不影响闭环（notify 内部已吞）。"""
    if rule is None:
        rule = event.rule
    payload = {
        'status': status,
        'host': (event.host if event else host) or '',
        'alert': (event.alert_message if event else alert) or '',
        'alert_target': (event.alert_target if event else target) or '',
        'rule': rule.name,
        'playbook': rule.playbook.name if rule.playbook_id else '-',
        'job_id': event.job_id if event else None,
        'before': before, 'after': after,
        'duration_ms': duration_ms, 'error': error,
    }
    notify.notify_heal_report(payload)


def _recheck_with_retries(rule, host, retries=RECHECK_RETRIES):
    """复检带重试（服务重启到端口就绪有延迟）；全失败返回最后一次结果或 None。"""
    result = None
    for i in range(retries):
        result = _recheck(rule, host)
        if result is not None and result.status == 'ok':
            return result
        if i < retries - 1:
            time.sleep(RECHECK_INTERVAL)
    return result


def _recheck(rule, host):
    """单主机单检查项复检（约定：调用 INSP 单项插件）。

    复用 INSP 的采集通道（run_collection 对单主机跑同一条采集命令）与检查
    插件（CHECKS 注册表），与巡检报告同一口径。返回 CheckResult 或 None
    （主机不可达/采集失败）。
    """
    from apps.ansible_ops.inspection import CHECKS
    from apps.ansible_ops.inspection.collect import HostRunner, run_collection

    work_dir = Path(tempfile.mkdtemp(prefix='heal_recheck_'))
    try:
        collected = run_collection([host], work_dir, timeout=60)
        seg = next(iter(collected.values()), None)
        if not seg or seg.get('payload') is None:
            logger.warning('heal: 复检采集失败（host=%s status=%s）',
                           host.name, seg.get('status') if seg else 'no-segment')
            return None
        runner = HostRunner(host, seg['payload'])
        return CHECKS[rule.check_name](runner, host)
    except Exception:
        logger.exception('heal: 复检异常（host=%s check=%s）', host.name, rule.check_name)
        return None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_playbook(rule, host):
    """进程内创建并等待自愈 job（复用执行引擎引擎）。

    并发名额沿用 collect.try_acquire_slot（与手动执行/巡检共享 MAX_JOBS=2，
    排队而非立即失败）；名额由 executor.run_job 的 finally 统一释放，本函数
    只在「占用后未能提交」的窗口自放。返回 (job, None) 或 (None, error)。
    """
    from apps.ansible_ops.executor import LOG_DIR, submit_job
    from apps.ansible_ops.inspection.collect import release_slot, try_acquire_slot
    from apps.ansible_ops.models import AnsibleJob
    from django_redis import get_redis_connection
    import os

    try:
        rds = get_redis_connection()
    except Exception:
        rds = None
    if not try_acquire_slot(rds, wait_seconds=SLOT_WAIT_TIMEOUT):
        return None, f'执行并发名额已满（等待 {SLOT_WAIT_TIMEOUT}s 未获取）'
    committed = False
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        job = AnsibleJob.objects.create(
            user=rule.created_by, host_ids=json.dumps([host.id]),
            playbook_id=rule.playbook_id, extra_vars=rule.extra_vars or '{}')
        submit_job(job.id)
        committed = True
    except Exception as e:
        logger.exception('heal: 自愈 job 提交失败')
        return None, f'自愈 job 提交失败：{e}'
    finally:
        if not committed:
            release_slot(rds)  # 提交失败 run_job 不会执行，名额需自放

    deadline = time.monotonic() + PLAYBOOK_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        job.refresh_from_db(fields=['status'])
        if job.status not in (AnsibleJob.STATUS_PENDING, AnsibleJob.STATUS_RUNNING):
            return job, None
        time.sleep(1)
    # 超时只判定闭环失败，job 本身继续运行（executor 看门狗 JOB_TIMEOUT 兜底）
    return job, f'自愈 job 超时（{PLAYBOOK_WAIT_TIMEOUT}s）未完成'
