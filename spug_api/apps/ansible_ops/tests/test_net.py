# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：网络探测与设备备份单元测试——矩阵加载、TCP 探测、
# 断链状态机（告警只在状态跃迁时产生）、备份 diff 落库、API 与路径防穿越。
# 真 ping 子进程与真设备 SSH 采集由验收流程实测（7 节点 + r1/r2），不在单测范围。
import asyncio
import json
import socket
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import RequestFactory

from apps.account.models import User
from apps.alarm.models import Alarm
from apps.ansible_ops import views as ansible_views
from apps.ansible_ops.models import (
    HealEvent, HealRule, NetConfigChange, NetTargetState, Playbook)
from apps.ansible_ops.net import backup as backup_mod
from apps.ansible_ops.net import probe as probe_mod
from apps.ansible_ops.views import NetBackupFileView, NetProbeRunView


def _payload(response):
    return json.loads(response.content)


def _cell(target, check, status, latency=None, detail=None):
    return {'target': target, 'check': check, 'status': status,
            'latency_ms': latency, 'detail': detail}


@pytest.fixture
def user(db):
    return User.objects.create(username='net-tester', nickname='NetTester',
                               password_hash='x', is_supper=True)


# ---- 矩阵加载 ----

@pytest.mark.django_db
def test_load_matrix_default_parses():
    targets = probe_mod.load_matrix()
    names = [t['name'] for t in targets]
    assert 'web-01' in names and 'r1' in names
    web = next(t for t in targets if t['name'] == 'web-01')
    assert web['checks'] == ['icmp', 'tcp:22', 'tcp:80']


@pytest.mark.django_db
def test_load_matrix_rejects_bad_port(tmp_path):
    p = tmp_path / 'matrix.yaml'
    p.write_text('targets:\n  - name: x\n    tcp: [70000]\n', encoding='utf-8')
    with pytest.raises(ValueError):
        probe_mod.load_matrix(str(p))


@pytest.mark.django_db
def test_load_matrix_rejects_missing_name(tmp_path):
    p = tmp_path / 'matrix.yaml'
    p.write_text('targets:\n  - icmp: true\n', encoding='utf-8')
    with pytest.raises(ValueError):
        probe_mod.load_matrix(str(p))


@pytest.mark.django_db
def test_load_matrix_rejects_no_checks(tmp_path):
    p = tmp_path / 'matrix.yaml'
    p.write_text('targets:\n  - name: x\n', encoding='utf-8')
    with pytest.raises(ValueError):
        probe_mod.load_matrix(str(p))


@pytest.mark.django_db
def test_load_matrix_rejects_duplicate_target_check(tmp_path):
    """重复 (name, check) 拒绝：否则 apply_state 同轮双计数、断链阈值静默减半。"""
    p = tmp_path / 'matrix.yaml'
    p.write_text(
        'targets:\n  - name: x\n    tcp: [22]\n  - name: x\n    icmp: true\n    tcp: [22]\n',
        encoding='utf-8')
    with pytest.raises(ValueError, match='重复'):
        probe_mod.load_matrix(str(p))


@pytest.mark.django_db
def test_load_matrix_rejects_overlong_name(tmp_path):
    """name 上限 35：保证告警名 name+check+后缀 不撑爆 Alarm.name(50)。"""
    p = tmp_path / 'matrix.yaml'
    p.write_text(f'targets:\n  - name: {"x" * 36}\n    icmp: true\n', encoding='utf-8')
    with pytest.raises(ValueError, match='过长'):
        probe_mod.load_matrix(str(p))


@pytest.mark.django_db
def test_load_matrix_scalar_tcp_normalized(tmp_path):
    p = tmp_path / 'matrix.yaml'
    p.write_text('targets:\n  - name: x\n    tcp: 22\n', encoding='utf-8')
    targets = probe_mod.load_matrix(str(p))
    assert targets[0]['checks'] == ['tcp:22']


# ---- 探测引擎（TCP 真连本机；ICMP mock 子进程）----

@pytest.mark.django_db
def test_probe_tcp_ok_on_listening_port():
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    try:
        port = srv.getsockname()[1]
        cell = asyncio.run(probe_mod._probe_tcp('127.0.0.1', port))
    finally:
        srv.close()
    assert cell['status'] == 'ok'


@pytest.mark.django_db
def test_probe_tcp_fail_on_closed_port():
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    port = srv.getsockname()[1]
    srv.close()  # 立即释放：后续连接大概率被拒（内核无监听）
    cell = asyncio.run(probe_mod._probe_tcp('127.0.0.1', port))
    assert cell['status'] == 'fail'
    assert cell['detail']


class _FakeProc:
    def __init__(self, returncode, stdout=b''):
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self):
        return self._stdout, b''


@pytest.mark.django_db
def test_probe_icmp_ok_parses_latency(monkeypatch):
    out = (b'64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=12.9 ms\n')

    def fake_exec(*args, **kwargs):
        async def _coro():
            return _FakeProc(0, out)
        return _coro()

    monkeypatch.setattr(probe_mod.asyncio, 'create_subprocess_exec', fake_exec)
    cell = asyncio.run(probe_mod._probe_icmp('web-01'))
    assert cell['status'] == 'ok'
    assert cell['latency_ms'] == 12.9


@pytest.mark.django_db
def test_probe_icmp_fail_on_nonzero_exit(monkeypatch):
    def fake_exec(*args, **kwargs):
        async def _coro():
            return _FakeProc(1)
        return _coro()

    monkeypatch.setattr(probe_mod.asyncio, 'create_subprocess_exec', fake_exec)
    cell = asyncio.run(probe_mod._probe_icmp('web-01'))
    assert cell['status'] == 'fail'
    assert '退出码' in cell['detail']


@pytest.mark.django_db
def test_probe_icmp_oserror_degrades_to_fail_cell(monkeypatch):
    """ping 二进制缺失等 OSError 环境故障：转 fail cell，不穿透炸掉整轮。"""
    def fake_exec(*args, **kwargs):
        raise FileNotFoundError('no such file: ping')

    monkeypatch.setattr(probe_mod.asyncio, 'create_subprocess_exec', fake_exec)
    cell = asyncio.run(probe_mod._probe_icmp('web-01'))
    assert cell['status'] == 'fail'
    assert 'ping 启动失败' in cell['detail']


# ---- 断链状态机 ----

FAIL = [_cell('web-01', 'tcp:22', 'fail', detail='连接被拒绝')]
OK = [_cell('web-01', 'tcp:22', 'ok', latency=5)]


@pytest.mark.django_db
def test_state_machine_alarm_only_at_threshold_and_no_duplicate():
    probe_mod.apply_state(FAIL)
    probe_mod.apply_state(FAIL)
    assert Alarm.objects.count() == 0
    assert NetTargetState.objects.get().consecutive_failures == 2
    probe_mod.apply_state(FAIL)  # 第 3 次 → 断链告警
    assert Alarm.objects.count() == 1
    alarm = Alarm.objects.first()
    assert alarm.status == '1' and alarm.type == '网络探测' and alarm.target == 'web-01'
    probe_mod.apply_state(FAIL)  # 第 4 次：已告警，不重复轰炸
    assert Alarm.objects.count() == 1


@pytest.mark.django_db
def test_state_machine_recovery_alarm_then_reset():
    for _ in range(3):
        probe_mod.apply_state(FAIL)
    st = NetTargetState.objects.get()
    assert st.alarm_active and st.first_fail_at
    probe_mod.apply_state(OK)
    # 断链 + 恢复各一条，同名可对照；恢复记录 duration 为秒数
    assert Alarm.objects.count() == 2
    down = Alarm.objects.filter(status='1').first()
    recovery = Alarm.objects.filter(status='2').first()
    assert down and recovery and down.name == recovery.name
    assert recovery.duration.endswith('秒')
    st.refresh_from_db()
    assert not st.alarm_active and st.consecutive_failures == 0 and st.first_fail_at is None
    probe_mod.apply_state(OK)  # 恢复后连续 ok 不再产生告警
    assert Alarm.objects.count() == 2


@pytest.mark.django_db
def test_state_machine_counter_resets_after_single_ok():
    probe_mod.apply_state(FAIL)
    probe_mod.apply_state(FAIL)
    probe_mod.apply_state(OK)  # 中间恢复一次，计数清零
    probe_mod.apply_state(FAIL)
    probe_mod.apply_state(FAIL)
    assert Alarm.objects.count() == 0  # 未达连续 3 次
    assert NetTargetState.objects.get().consecutive_failures == 2


@pytest.mark.django_db
def test_heal_never_triggered_by_net_alarm(user):
    """断链告警 target=节点名，关键字极易与进程类 heal 规则撞车——
    heal.service 对 type='网络探测' 显式旁路（追加），断链不触发闭环。"""
    pb = Playbook.objects.create(name='net-test-pb', content='- hosts: all',
                                 updated_by=user)
    HealRule.objects.create(name='bad-keyword-rule', match_keyword='cache-03',
                            playbook=pb, check_name='process', created_by=user)
    for _ in range(3):
        probe_mod.apply_state(FAIL)
    assert Alarm.objects.count() == 1   # 断链告警正常产生
    assert HealEvent.objects.count() == 0  # 但不触发任何自愈闭环


# ---- 设备备份 ----

V1 = '!\nhostname r1\n!\nip route 10.10.0.0/16 Null0\n!\nend\n'
V2 = '!\nhostname r1\n!\nip route 10.10.0.0/16 Null0\nip route 10.99.0.0/24 Null0\n!\nend\n'


@pytest.fixture
def backup_env(monkeypatch, tmp_path):
    """备份目录指向 tmp + 单设备清单 + _collect 按调用次序返回预设内容。"""
    monkeypatch.setattr(backup_mod, 'netbackup_dir', lambda: tmp_path)
    dev = {'name': 'r1', 'host': 'r1', 'port': 22, 'username': 'ops',
           'password_env': 'OPS_PASSWORD', 'device_type': 'linux',
           'command': "vtysh -c 'show running-config'"}
    return dev, tmp_path


@pytest.mark.django_db
def test_backup_baseline_then_no_change_then_diff(backup_env):
    dev, tmp_path = backup_env
    outputs = iter([V1, V1, V2])
    with mock.patch.object(backup_mod, '_collect', lambda d: next(outputs)):
        r1 = backup_mod.backup_device(dev, ts='20260926-100000')
        assert r1['changed'] and r1['error'] is None
        assert NetConfigChange.objects.count() == 0  # 基线：只落文件不落变更
        r2 = backup_mod.backup_device(dev, ts='20260926-100100')
        assert not r2['changed']
        assert len(list((tmp_path / 'r1').glob('*.cfg'))) == 1  # 内容相同不写新文件
        r3 = backup_mod.backup_device(dev, ts='20260926-100200')
        assert r3['changed']
    change = NetConfigChange.objects.get()
    assert change.device == 'r1' and change.from_ts == '20260926-100000'
    assert change.to_ts == '20260926-100200'
    assert '+ip route 10.99.0.0/24 Null0' in change.diff
    assert change.detected_at


@pytest.mark.django_db
def test_backup_device_error_no_file(backup_env):
    dev, tmp_path = backup_env

    def boom(d):
        raise OSError('connection refused')

    with mock.patch.object(backup_mod, '_collect', boom):
        r = backup_mod.backup_device(dev)
    assert 'connection refused' in r['error']
    assert not (tmp_path / 'r1').exists()


@pytest.mark.django_db
def test_clean_config_strips_vtysh_warning_and_blank_edges():
    raw = "% Can't open configuration file /etc/frr/vtysh.conf due to 'No such file or directory'.\r\n" \
          'Building configuration...\n\nCurrent configuration:\n!\nhostname r1\n!\nend\n\n'
    cleaned = backup_mod._clean_config(raw)
    assert '%' not in cleaned
    assert cleaned.startswith('Building configuration')
    assert cleaned.endswith('end\n')


@pytest.mark.django_db
def test_load_devices_rejects_missing_username(tmp_path):
    p = tmp_path / 'devices.yaml'
    p.write_text('devices:\n  - name: r1\n    host: r1\n', encoding='utf-8')
    with pytest.raises(ValueError):
        backup_mod.load_devices(str(p))


# ---- API ----

@pytest.mark.django_db
def test_net_probe_run_api(user, monkeypatch):
    fake_run = SimpleNamespace(to_view=lambda: {'id': 1, 'cells': [_cell('r1', 'icmp', 'ok')]})
    monkeypatch.setattr(ansible_views, 'run_round', lambda trigger: fake_run)
    request = RequestFactory().post('/api/ansible_ops/net-probe/run/')
    request.user = user
    response = NetProbeRunView.as_view()(request)
    # spug json_response 信封：成功载荷在 data 键（同既有测试惯例）
    body = _payload(response)['data']
    assert body['cells'][0]['target'] == 'r1'


@pytest.mark.django_db
def test_netbackup_file_view_roundtrip(user, monkeypatch, tmp_path):
    monkeypatch.setattr(ansible_views, 'netbackup_dir', lambda: tmp_path)
    target = tmp_path / 'r1'
    target.mkdir()
    (target / '20260926-100000.cfg').write_text(V1, encoding='utf-8')
    request = RequestFactory().get('/api/ansible_ops/netbackup/r1/files/20260926-100000/')
    request.user = user
    response = NetBackupFileView.as_view()(request, 'r1', '20260926-100000')
    body = _payload(response)['data']
    assert body['content'] == V1


@pytest.mark.django_db
def test_netbackup_file_view_rejects_bad_identifiers(user, monkeypatch, tmp_path):
    monkeypatch.setattr(ansible_views, 'netbackup_dir', lambda: tmp_path)
    for device, name in [('r1', 'evil'), ('r1', '../../../etc/passwd'.replace('/', '-')), ('r1..', '20260926-100000')]:
        request = RequestFactory().get(f'/api/ansible_ops/netbackup/{device}/files/{name}/')
        request.user = user
        response = NetBackupFileView.as_view()(request, device, name)
        assert _payload(response).get('error'), f'{device}/{name} 应被拒绝'


@pytest.mark.django_db
def test_netbackup_file_view_missing_file(user, monkeypatch, tmp_path):
    monkeypatch.setattr(ansible_views, 'netbackup_dir', lambda: tmp_path)
    request = RequestFactory().get('/api/ansible_ops/netbackup/r1/files/20260926-100000/')
    request.user = user
    response = NetBackupFileView.as_view()(request, 'r1', '20260926-100000')
    assert '不存在' in _payload(response)['error']


@pytest.mark.django_db
def test_netbackup_file_view_allows_dotted_device(user, monkeypatch, tmp_path):
    """设备名允许点号（r1.core 形态）：白名单放行，文件缺失报"不存在"而非"非法"。"""
    monkeypatch.setattr(ansible_views, 'netbackup_dir', lambda: tmp_path)
    request = RequestFactory().get('/api/ansible_ops/netbackup/r1.core/files/20260926-100000/')
    request.user = user
    response = NetBackupFileView.as_view()(request, 'r1.core', '20260926-100000')
    assert '非法' not in _payload(response)['error']
