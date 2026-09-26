# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：自愈单元测试——规则 CRUD、告警匹配、冷却抑制、
# 闭环编排（playbook 执行与复检 mock，不真跑 ansible）、signal 挂点。
# 7 节点真实端到端闭环（注入故障 ≤60s 恢复）由验收流程实测，不在单测范围。
import json
from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from apps.account.models import User
from apps.alarm.models import Alarm
from apps.host.models import Host
from apps.ansible_ops.heal import service as service_mod
from apps.ansible_ops.models import AnsibleJob, HealEvent, HealRule, Playbook


def _payload(response):
    """spug 的 json_response 是普通 HttpResponse（AttrDict 序列化），统一解码。"""
    return json.loads(response.content)


@pytest.fixture
def user(db):
    return User.objects.create(username='heal-tester', nickname='HealTester',
                               password_hash='x', is_supper=True)


@pytest.fixture
def host(user, db):
    return Host.objects.create(name='web-01', hostname='10.0.0.1', port=22,
                               username='ops', created_by=user)


@pytest.fixture
def playbook(user, db):
    return Playbook.objects.create(name='heal-test-pb', content='- hosts: all',
                                   updated_by=user)


@pytest.fixture
def rule(user, playbook, db):
    return HealRule.objects.create(
        name='web-nginx-restart', match_keyword='nginx', match_host='web-01',
        playbook=playbook, check_name='process', cooldown=10, created_by=user)


def make_alarm(**kwargs):
    """模拟 monitor 落库的告警记录（进程检测 web-01）。"""
    fields = dict(name='nginx 进程检测', type='进程检测', target='web-01(10.0.0.1)',
                  status='1', duration='1分钟', notify_mode='[]', notify_grp='[]')
    fields.update(kwargs)
    return Alarm.objects.create(**fields)


class _SyncThread:
    """测试用假 Thread：start() 就地执行 target，闭环同步可断言。

    真实闭环线程内 ORM 用独立连接（同 test_inspection._SyncPool 的注释），
    pytest 事务外不可见；编排链路测同步语义，真实异步由验收流程承担。
    """

    def __init__(self, target=None, args=(), **kwargs):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


@pytest.fixture(autouse=True)
def sync_heal_thread(monkeypatch):
    monkeypatch.setattr(service_mod.threading, 'Thread', _SyncThread)


@pytest.fixture(autouse=True)
def mute_notify(monkeypatch):
    """默认静音外发：heal_webhook.env 若配置了真实机器人，测试会发垃圾消息
    （实测曾向 mock 漏出 20 条）。个别用例要验证外发 payload 时自行覆盖本 fixture。"""
    monkeypatch.setattr(service_mod.notify, 'notify_heal_report', lambda p: None)


# ---- 规则 CRUD ----

@pytest.mark.django_db
def test_rule_create_and_list(user, playbook):
    from apps.ansible_ops.views import HealRuleListView
    rf = RequestFactory()
    request = rf.post('/api/ansible_ops/heal-rules/', data=json.dumps({
        'name': 'r1', 'match_keyword': 'nginx', 'playbook_id': playbook.id,
        'check_name': 'process', 'cooldown': 5}).encode(),
        content_type='application/json')
    request.user = user
    response = HealRuleListView().post(request)
    assert _payload(response)['error'] is None
    assert HealRule.objects.filter(name='r1', check_name='process').exists()
    response = HealRuleListView().get(request)
    assert any(x['name'] == 'r1' for x in _payload(response)['data'])


@pytest.mark.django_db
def test_rule_create_rejects_bad_check(user):
    from apps.ansible_ops.views import HealRuleListView
    rf = RequestFactory()
    request = rf.post('/api/ansible_ops/heal-rules/', data=json.dumps({
        'name': 'r2', 'match_keyword': 'nginx', 'playbook_id': 1,
        'check_name': 'nope'}).encode(), content_type='application/json')
    request.user = user
    response = HealRuleListView().post(request)
    assert _payload(response)['error'] == '复检检查项无效'


@pytest.mark.django_db
def test_rule_delete_blocked_when_events(user, rule, db):
    from apps.ansible_ops.views import HealRuleDetailView
    from libs.utils import human_datetime
    HealEvent.objects.create(rule=rule, host='web-01', created_at=human_datetime())
    rf = RequestFactory()
    request = rf.delete(f'/api/ansible_ops/heal-rules/{rule.id}/')
    request.user = user
    response = HealRuleDetailView().delete(request, rule.id)
    assert '无法删除' in _payload(response)['error']
    assert HealRule.objects.filter(pk=rule.id).exists()


# ---- 匹配与抑制（post_save(Alarm) signal 入口）----

@pytest.mark.django_db
def test_no_rule_no_event(db):
    make_alarm()
    assert HealEvent.objects.count() == 0


@pytest.mark.django_db
def test_keyword_miss_no_event(rule, db):
    make_alarm(name='磁盘使用率检测', type='自定义脚本', target='db-01(10.0.0.2)')
    assert HealEvent.objects.count() == 0


@pytest.mark.django_db
def test_host_mismatch_no_event(rule, db):
    make_alarm(target='db-01(10.0.0.2)')
    assert HealEvent.objects.count() == 0


@pytest.mark.django_db
def test_recovery_alarm_not_triggered(rule, db):
    make_alarm(status='2')  # 故障恢复告警不触发闭环
    assert HealEvent.objects.count() == 0


@pytest.mark.django_db
def test_site_alarm_without_host_skipped(user, playbook, db):
    HealRule.objects.create(name='site', match_keyword='http', playbook=playbook,
                            check_name='process', created_by=user)
    make_alarm(name='站点检测', type='站点检测', target='https://example.com')
    assert HealEvent.objects.count() == 0


@pytest.mark.django_db
def test_matched_alarm_creates_running_event(rule, host, db, monkeypatch):
    monkeypatch.setattr(service_mod, '_run_heal', lambda event_id: None)
    alarm = make_alarm()
    event = HealEvent.objects.get()
    assert event.status == HealEvent.STATUS_RUNNING
    assert event.host == 'web-01'
    assert event.rule == rule
    assert event.alert_id == alarm.id
    assert event.alert_name == alarm.name


@pytest.mark.django_db
def test_cooldown_suppresses_second_alert(rule, host, db, monkeypatch):
    monkeypatch.setattr(service_mod, '_run_heal', lambda event_id: None)
    make_alarm()
    make_alarm()  # 冷却期内重复告警
    # HealEvent Meta ordering='-id'（新记录在前）：首次 running，第二次 suppressed
    statuses = list(HealEvent.objects.values_list('status', flat=True))
    assert statuses == [HealEvent.STATUS_SUPPRESSED, HealEvent.STATUS_RUNNING]


@pytest.mark.django_db
def test_cooldown_window_expires(rule, host, db, monkeypatch):
    monkeypatch.setattr(service_mod, '_run_heal', lambda event_id: None)
    make_alarm()
    event = HealEvent.objects.get()
    # 首事件置终态并挪出冷却窗口（cooldown=10 分钟），下次告警应重新触发
    HealEvent.objects.filter(pk=event.pk).update(
        status=HealEvent.STATUS_RECOVERED, created_at='2026-01-01 00:00:00',
        finished_at='2026-01-01 00:00:00')
    make_alarm()
    assert HealEvent.objects.count() == 2
    assert not HealEvent.objects.filter(status=HealEvent.STATUS_SUPPRESSED).exists()


@pytest.mark.django_db
def test_in_progress_blocks_second(rule, host, db, monkeypatch):
    """闭环进行中（running 未终态）同样抑制，与冷却窗口互为两道闸。"""
    monkeypatch.setattr(service_mod, '_run_heal', lambda event_id: None)
    make_alarm()
    make_alarm()
    assert HealEvent.objects.filter(status=HealEvent.STATUS_SUPPRESSED).exists()


# ---- 闭环编排（playbook/复检 mock）----

@pytest.fixture
def job_ok(user, host, db):
    return AnsibleJob.objects.create(user=user, host_ids=f'[{host.id}]',
                                     status=AnsibleJob.STATUS_SUCCESS)


@pytest.fixture
def check_crit():
    return SimpleNamespace(check='process', host='web-01', status='crit',
                           value='stopped', message='nginx 已安装但进程不存在')


@pytest.fixture
def check_ok():
    return SimpleNamespace(check='process', host='web-01', status='ok',
                           value='1 procs', message='nginx 运行中')


@pytest.mark.django_db
def test_heal_recovers(rule, host, job_ok, check_crit, check_ok, monkeypatch, mute_notify):
    """故障确认 → playbook 成功 → 复检恢复 → recovered。"""
    checks = iter([check_crit, check_ok])
    monkeypatch.setattr(service_mod, '_recheck_with_retries',
                        lambda rule, host, retries=3: next(checks))
    monkeypatch.setattr(service_mod, '_run_playbook',
                        lambda rule, host: (job_ok, None))
    make_alarm()
    event = HealEvent.objects.get()
    assert event.status == HealEvent.STATUS_RECOVERED
    assert event.before_status == 'crit' and event.after_status == 'ok'
    assert event.job == job_ok
    assert event.duration_ms is not None


@pytest.mark.django_db
def test_heal_failed_when_job_fails(rule, host, check_crit, user, monkeypatch, mute_notify):
    failed = AnsibleJob.objects.create(user=user, host_ids='[1]',
                                       status=AnsibleJob.STATUS_FAILED)
    monkeypatch.setattr(service_mod, '_recheck_with_retries',
                        lambda rule, host, retries=3: check_crit)
    monkeypatch.setattr(service_mod, '_run_playbook',
                        lambda rule, host: (failed, None))
    make_alarm()
    event = HealEvent.objects.get()
    assert event.status == HealEvent.STATUS_FAILED
    assert event.after_status is None
    assert event.job == failed


@pytest.mark.django_db
def test_heal_failed_when_recheck_still_crit(rule, host, job_ok, check_crit,
                                             monkeypatch, mute_notify):
    monkeypatch.setattr(service_mod, '_recheck_with_retries',
                        lambda rule, host, retries=3: check_crit)
    monkeypatch.setattr(service_mod, '_run_playbook',
                        lambda rule, host: (job_ok, None))
    make_alarm()
    event = HealEvent.objects.get()
    assert event.status == HealEvent.STATUS_FAILED
    assert event.after_status == 'crit'


@pytest.mark.django_db
def test_heal_skips_playbook_when_already_recovered(rule, host, check_ok,
                                                    monkeypatch, mute_notify):
    """自愈前复检已恢复（误报/先愈）：不跑 playbook，直接 recovered。"""
    monkeypatch.setattr(service_mod, '_recheck_with_retries',
                        lambda rule, host, retries=1: check_ok)
    monkeypatch.setattr(service_mod, '_run_playbook',
                        lambda rule, host: pytest.fail('不应执行 playbook'))
    make_alarm()
    event = HealEvent.objects.get()
    assert event.status == HealEvent.STATUS_RECOVERED
    assert event.job_id is None
    assert '未执行 Playbook' in json.loads(event.detail).get('note', '')


@pytest.mark.django_db
def test_heal_aborts_when_slot_full(rule, host, check_crit, monkeypatch, mute_notify):
    monkeypatch.setattr(service_mod, '_recheck_with_retries',
                        lambda rule, host, retries=1: check_crit)
    monkeypatch.setattr(service_mod, '_run_playbook',
                        lambda rule, host: (None, '执行并发名额已满（等待 120s 未获取）'))
    make_alarm()
    event = HealEvent.objects.get()
    assert event.status == HealEvent.STATUS_FAILED
    assert '名额已满' in json.loads(event.detail).get('error', '')
