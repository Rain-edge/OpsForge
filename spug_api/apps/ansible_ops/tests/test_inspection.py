# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：巡检单元测试——协议注册、采集解析、检查阈值、diff、
# 产物完整性（xlsx 重开）、视图与轮次编排（ansible 采集层 mock，不真跑）。
# 7 节点真实巡检与故障注入由验收流程实测，不在单测范围。
import base64
import json

import pytest
from django.core.management import call_command
from django.test import RequestFactory
from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone
from django_redis import get_redis_connection

from apps.account.models import User
from apps.host.models import Host
from apps.ansible_ops.inspection import collect as collect_mod
from apps.ansible_ops.inspection import service as service_mod
from apps.ansible_ops.executor import RUNNING_KEY
from apps.ansible_ops.inspection import CHECKS, CheckResult, STATUS_CRIT, STATUS_OK, STATUS_WARN
from apps.ansible_ops.inspection import checks as checks_mod
from apps.ansible_ops.inspection import report as report_mod
from apps.ansible_ops.models import InspectionReport


@pytest.fixture(autouse=True)
def clean_concurrency_slot(db):
    """巡检轮次会占用 redis 并发名额，用例后清零防跨用例累积。"""
    yield
    get_redis_connection().delete(RUNNING_KEY)


class _SyncPool:
    """测试用同步"线程池"：submit 直接就地执行。

    真线程池里 ORM 用独立连接，在 pytest-django 的事务（django_db 默认包裹）
    之外，看不到夹具数据也回写不进主线程可见状态——与 test_views mock 掉
    executor 线程同理，编排链路测同步语义，真实异步由验收流程承担。
    """

    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture(autouse=True)
def sync_inspection_pool(monkeypatch):
    monkeypatch.setattr(service_mod, '_pool', _SyncPool())


@pytest.fixture
def user(db):
    return User.objects.create(username='tester', nickname='Tester', password_hash='x', is_supper=True)


@pytest.fixture
def hosts_2(user, db):
    """两台模拟主机（协议/编排验证不依赖 7 台规模）。"""
    return [
        Host.objects.create(name='web-01', hostname='10.0.0.1', port=22, username='ops', created_by=user),
        Host.objects.create(name='db-01', hostname='10.0.0.2', port=22, username='ops', created_by=user),
    ]


def make_runner(payload, name='web-01'):
    return collect_mod.HostRunner(SimpleNamespace(name=name), payload)


def b64(text):
    return base64.b64encode(text.encode()).decode()


# ---- 协议与注册表 ----

@pytest.mark.django_db
def test_all_six_checks_registered():
    assert set(CHECKS) == {'cpu', 'memory', 'disk', 'process', 'port', 'cert'}


def test_duplicate_register_rejected():
    from apps.ansible_ops.inspection import register
    with pytest.raises(ValueError):
        register('cpu')(lambda runner, host: None)


# ---- 采集层 ----

@pytest.mark.django_db
def test_alias_map_matches_inventory(hosts_2):
    """别名映射与引擎 inventory 生成规则对拍（约定"复用引擎"的落点）。"""
    from apps.ansible_ops.inventory import generate_inventory
    inv = generate_inventory(hosts_2, None)
    for alias, host in collect_mod.build_alias_map(hosts_2).items():
        assert f'{alias} ansible_host={host.hostname}' in inv


def test_parse_collect_output_segments():
    text = (
        'paramiko/pkey.py:100: SomeWarning: ignore me\n'
        'web-01 | CHANGED | rc=0 >>\n'
        '{"cpu":"e30="}\n'
        'db-01 | UNREACHABLE! => {"msg": "ssh error"}\n'
    )
    segments = collect_mod.parse_collect_output(text)
    assert segments['web-01']['status'] == 'CHANGED'
    assert segments['web-01']['stdout'] == '{"cpu":"e30="}'
    assert segments['db-01']['status'] == 'UNREACHABLE'
    # db-01 段落不被 web-01 的 JSON 污染
    assert 'cpu' not in segments['db-01']['stdout']


def test_decode_payload_roundtrip_and_garbage():
    payload = collect_mod.decode_payload('{"df":"%s"}' % b64('Filesystem 1K-blocks\n'))
    assert payload['df'].startswith('Filesystem')
    assert collect_mod.decode_payload('not json at all') is None
    assert collect_mod.decode_payload('{"df":"!!!not-b64!!!"}') is None


def test_collect_script_emits_valid_json():
    """端到端验证采集脚本产出合法 JSON（自审 P0-1 回归）：在本机 sh 中执行，
    采集命令缺失（非 Linux）时输出空值键，JSON 仍必须合法且键齐全。"""
    import subprocess
    proc = subprocess.run(
        ['sh', '-c', collect_mod.COLLECT_SCRIPT],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    payload = collect_mod.decode_payload(proc.stdout)
    assert payload is not None, f'采集脚本输出非法 JSON: {proc.stdout[:200]!r}'
    assert set(payload) == {key for key, _ in collect_mod.COLLECTORS}


def test_try_acquire_slot_and_release():
    rds = get_redis_connection()
    rds.delete(RUNNING_KEY)
    assert collect_mod.try_acquire_slot(rds, wait_seconds=0)
    assert int(rds.get(RUNNING_KEY)) == 1
    collect_mod.release_slot(rds)
    assert int(rds.get(RUNNING_KEY) or 0) == 0


@pytest.mark.django_db
def test_acquire_failure_leaves_counter_untouched():
    """名额占满时获取失败：计数器不得净减（自审 P0-5 回归——失败路径内部
    已自还，调用方不再释放）。"""
    rds = get_redis_connection()
    rds.set(RUNNING_KEY, 2)
    assert collect_mod.try_acquire_slot(rds, wait_seconds=0) is False
    assert int(rds.get(RUNNING_KEY)) == 2
    rds.delete(RUNNING_KEY)


# ---- 检查阈值 ----

def test_cpu_psi_thresholds():
    def psi(avg60):
        return {'cpupsi': f'some avg10=0.00 avg60={avg60} avg300=0.00 total=1', 'loadavg': 'x'}
    assert checks_mod.check_cpu(make_runner(psi('1.00')), SimpleNamespace(name='h')).status == STATUS_OK
    assert checks_mod.check_cpu(make_runner(psi('25.00')), SimpleNamespace(name='h')).status == STATUS_WARN
    assert checks_mod.check_cpu(make_runner(psi('55.00')), SimpleNamespace(name='h')).status == STATUS_CRIT


def test_cpu_fallback_loadavg():
    runner = make_runner({'loadavg': '3.60 2.00 1.00', 'nproc': '1'})
    result = checks_mod.check_cpu(runner, SimpleNamespace(name='h'))
    assert result.status == STATUS_WARN and 'load1' in result.value


def test_memory_cgroup_thresholds():
    def mem(usage):
        return {'memcgroup': f'268435456 {usage}'}
    assert checks_mod.check_memory(make_runner(mem('100000000')), SimpleNamespace(name='h')).status == STATUS_OK
    assert checks_mod.check_memory(make_runner(mem('250000000')), SimpleNamespace(name='h')).status == STATUS_WARN
    assert checks_mod.check_memory(make_runner(mem('265000000')), SimpleNamespace(name='h')).status == STATUS_CRIT


def test_memory_fallback_meminfo_when_unlimited():
    payload = {
        'memcgroup': 'max 123',
        'meminfo': 'MemTotal:       1000000 kB\nMemAvailable:    100000 kB\n',
    }
    result = checks_mod.check_memory(make_runner(payload), SimpleNamespace(name='h'))
    assert result.status == STATUS_WARN and '回退' in result.message


DF_SAMPLE = (
    'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
    '/dev/sda1 1000000 500000 500000 50% /\n'
    'tmpfs 100000 96000 4000 96% /mnt/fault\n'
)
DF_OK_SAMPLE = DF_SAMPLE.replace('96%', '50%')


def test_disk_thresholds():
    assert checks_mod.check_disk(make_runner({'df': DF_SAMPLE}), SimpleNamespace(name='h')).status == STATUS_CRIT
    df = DF_SAMPLE.replace('96%', '90%')
    assert checks_mod.check_disk(make_runner({'df': df}), SimpleNamespace(name='h')).status == STATUS_WARN
    df = DF_SAMPLE.replace('96%', '50%')
    result = checks_mod.check_disk(make_runner({'df': df}), SimpleNamespace(name='h'))
    assert result.status == STATUS_OK and '/mnt/fault' not in result.message


def test_process_installed_vs_missing():
    installed = {'procpkg': 'Status: install ok installed', 'procs': '1 nginx: master\n2 nginx: worker'}
    assert checks_mod.check_process(make_runner(installed), SimpleNamespace(name='h')).status == STATUS_OK
    assert checks_mod.check_process(
        make_runner({'procpkg': 'Status: install ok installed', 'procs': ''}),
        SimpleNamespace(name='h')).status == STATUS_CRIT
    # db/cache 节点未装 nginx：不适用而非故障
    assert checks_mod.check_process(make_runner({'procpkg': '', 'procs': ''}), SimpleNamespace(name='h')).status == STATUS_OK


SS_OK = (
    'State   Recv-Q  Send-Q Local Address:Port Peer Address:PortProcess\n'
    'LISTEN  0       128    0.0.0.0:22         0.0.0.0:*          users:(("sshd",pid=1,fd=3))\n'
    'LISTEN  0       511    0.0.0.0:80          0.0.0.0:*          users:(("nginx",pid=2,fd=6))\n'
)


def test_port_listeners():
    base = {'procpkg': 'Status: install ok installed'}
    assert checks_mod.check_port(make_runner({**base, 'ports': SS_OK}), SimpleNamespace(name='h')).status == STATUS_OK
    # 80 被非 nginx 占用（AC 故障 c）
    hijack = SS_OK.replace('("nginx"', '("python"')
    result = checks_mod.check_port(make_runner({**base, 'ports': hijack}), SimpleNamespace(name='h'))
    assert result.status == STATUS_CRIT and '占用' in result.message
    # 无人监听（AC 故障 b：停 nginx）
    gone = '\n'.join(l for l in SS_OK.splitlines() if ':80' not in l)
    result = checks_mod.check_port(make_runner({**base, 'ports': gone}), SimpleNamespace(name='h'))
    assert result.status == STATUS_CRIT and '无监听' in result.message
    # 未装 nginx 的节点不做 80 检查（db/cache 常态）
    assert checks_mod.check_port(make_runner({'procpkg': '', 'ports': SS_OK}), SimpleNamespace(name='h')).status == STATUS_OK


def make_cert(days):
    """现场自签一张 days 天后过期的证书，验证 notAfter 读取与阈值。"""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = timezone.now()
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'opsforge.test')]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'opsforge.test')]))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=days))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    payload = 'FILE:/etc/nginx/ssl/test.pem\n' + base64.b64encode(pem).decode()
    return payload


def test_cert_expiry_thresholds():
    ok = checks_mod.check_cert(make_runner({'certs': make_cert(90)}), SimpleNamespace(name='h'))
    assert ok.status == STATUS_OK
    warn = checks_mod.check_cert(make_runner({'certs': make_cert(20)}), SimpleNamespace(name='h'))
    assert warn.status == STATUS_WARN
    crit = checks_mod.check_cert(make_runner({'certs': make_cert(3)}), SimpleNamespace(name='h'))
    assert crit.status == STATUS_CRIT
    assert checks_mod.check_cert(make_runner({'certs': ''}), SimpleNamespace(name='h')).status == STATUS_OK
    assert checks_mod.check_cert(
        make_runner({'certs': 'FILE:/x.pem\n!!!'}), SimpleNamespace(name='h')).status == STATUS_WARN


def test_run_checks_isolates_broken_check():
    def broken(runner, host):
        raise RuntimeError('boom')
    CHECKS['boom'] = broken
    try:
        results = checks_mod.run_checks(make_runner({}), SimpleNamespace(name='h'))
        boom = [r for r in results if r.check == 'boom'][0]
        assert boom.status == STATUS_WARN and 'boom' in boom.message
    finally:
        CHECKS.pop('boom')


# ---- diff 与产物 ----

def test_diff_results_status_changes():
    prev = {'web-01': {'process': {'status': 'ok'}, 'disk': {'status': 'ok'}}}
    cur = {'web-01': {'process': {'status': 'crit'}, 'disk': {'status': 'ok'}}}
    changes = report_mod.diff_results(prev, cur)
    assert changes == [{'host': 'web-01', 'check': 'process', 'from': 'ok', 'to': 'crit'}]
    # 首轮（无基线）：全部视为新增变化
    assert len(report_mod.diff_results(None, cur)) == 2


@pytest.mark.django_db
def test_round_pipeline_and_artifacts(user, hosts_2, tmp_path, monkeypatch):
    """编排全链路（采集 mock）：落库 + 三件产物 + xlsx 可重开（AC3 自动化自证）。

    注意 mock 形态：run_collection 返回的 payload 是**已解码文本**（decode 在
    其内部完成），mock 必须返回明文 dict 而非 base64 串。
    """
    monkeypatch.setattr(service_mod, 'REPORTS_DIR', tmp_path)
    good_payload = {
        'loadavg': '0.10 0.20 0.30', 'nproc': '4',
        'cpupsi': 'some avg10=0.00 avg60=0.50 avg300=0.00 total=1',
        'memcgroup': '268435456 50000000',
        'meminfo': 'MemTotal: 1000000 kB\nMemAvailable: 900000 kB\n',
        'df': DF_OK_SAMPLE, 'ports': SS_OK,
        'procs': '1 nginx: master process', 'procpkg': 'Status: install ok installed',
        'certs': '',
    }
    alias_map = collect_mod.build_alias_map(hosts_2)

    def fake_collection(hosts, work_dir, timeout=300):
        return {alias: {'status': 'CHANGED', 'payload': dict(good_payload)} for alias in alias_map}

    monkeypatch.setattr(collect_mod, 'run_collection', fake_collection)
    call_command('run_inspection')
    report = InspectionReport.objects.first()
    assert report.status == InspectionReport.STATUS_SUCCESS
    assert report.trigger == InspectionReport.TRIGGER_MANUAL
    assert report.total_crit == 0 and report.hosts_total == 2
    summary = json.loads(report.summary)
    assert summary['counts']['ok'] >= 12 and summary['has_prev'] is False

    from openpyxl import load_workbook
    wb = load_workbook(report.xlsx_path)
    assert wb.active.title == '巡检报表'
    rows = list(wb.active.values)
    assert rows[0][0].startswith('OpsForge 批量巡检报告')
    with open(report.html_path, encoding='utf-8') as f:
        assert '巡检报告' in f.read()
    manifest = json.loads(open(report.manifest_path, encoding='utf-8').read())
    assert set(manifest['results']) == {'web-01', 'db-01'}

    # 第二轮：web-01 停 nginx → process/port 转 crit，diff 记录两条变化
    def fake_collection_round2(hosts, work_dir, timeout=300):
        broken = dict(good_payload, procs='', ports='\n'.join(
            l for l in SS_OK.splitlines() if ':80' not in l))
        return {alias: {'status': 'CHANGED', 'payload': dict(broken if host.name == 'web-01' else good_payload)}
                for alias, host in alias_map.items()}

    monkeypatch.setattr(collect_mod, 'run_collection', fake_collection_round2)
    call_command('run_inspection')
    report2 = InspectionReport.objects.order_by('-id').first()
    assert report2.total_crit == 2
    changes = json.loads(report2.summary)['changes']
    assert {c['check'] for c in changes} == {'process', 'port'}
    assert all(c['host'] == 'web-01' and c['from'] == 'ok' and c['to'] == 'crit' for c in changes)


@pytest.mark.django_db
def test_round_marks_unreachable_hosts(user, hosts_2, tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, 'REPORTS_DIR', tmp_path)
    alias_map = collect_mod.build_alias_map(hosts_2)

    def fake_collection(hosts, work_dir, timeout=300):
        return {alias: {'status': 'UNREACHABLE', 'payload': None} for alias in alias_map}

    monkeypatch.setattr(collect_mod, 'run_collection', fake_collection)
    call_command('run_inspection')
    report = InspectionReport.objects.first()
    assert report.status == InspectionReport.STATUS_PARTIAL
    assert report.hosts_unreachable == 2 and report.total_crit == 2 * len(CHECKS)


# ---- 视图 ----

def call_view(view, user, method='post', body=None, pk=None, kind=None, params=''):
    url = '/api/ansible_ops/inspection/'
    request = getattr(RequestFactory(), method)(
        url + params, data=json.dumps(body) if body is not None else None,
        content_type='application/json')
    request.user = user
    kwargs = {}
    if pk is not None:
        kwargs['pk'] = pk
    if kind is not None:
        kwargs['kind'] = kind
    return view.as_view()(request, **kwargs)


@pytest.mark.django_db
def test_run_view_rejects_when_busy(user):
    from apps.ansible_ops.views import InspectionRunView
    rds = get_redis_connection()
    rds.set(RUNNING_KEY, 99)
    assert json.loads(call_view(InspectionRunView, user).content)['error']


@pytest.mark.django_db
def test_run_view_submits_round(user, monkeypatch):
    captured = {}

    def fake_submit(trigger, user=None):
        captured['trigger'] = trigger
        return 42

    from apps.ansible_ops import views as views_mod
    monkeypatch.setattr(views_mod, 'submit_round', fake_submit)
    response = call_view(views_mod.InspectionRunView, user)
    # spug json_response 顶层为 {data, error} 结构（test_views 同款口径）
    assert json.loads(response.content)['data'] == {'report_id': 42}
    assert captured['trigger'] == InspectionReport.TRIGGER_MANUAL


@pytest.mark.django_db
def test_file_view_serves_and_downloads(user, hosts_2, tmp_path, monkeypatch):
    from apps.ansible_ops import views as views_mod
    InspectionFileView = views_mod.InspectionFileView
    # realpath 防线校验产物必须在报告目录内，测试文件放 tmp 并同步指向
    monkeypatch.setattr(views_mod, 'REPORTS_DIR', tmp_path)
    from apps.ansible_ops.inspection import report as report_mod
    monkeypatch.setattr(report_mod, 'REPORTS_DIR', tmp_path)
    path = tmp_path / 'inspection-test.html'
    path.write_text('<html>ok</html>', encoding='utf-8')
    report = InspectionReport.objects.create(
        trigger=InspectionReport.TRIGGER_MANUAL, user=user,
        status=InspectionReport.STATUS_SUCCESS, html_path=str(path))
    response = call_view(InspectionFileView, user, method='get', pk=report.id, kind='html')
    assert response.status_code == 200
    response = call_view(InspectionFileView, user, method='get', pk=report.id, kind='html', params='?dl=1')
    assert 'attachment' in response['Content-Disposition']
    # 未知 kind 与缺失文件走错误分支
    assert json.loads(call_view(InspectionFileView, user, method='get', pk=report.id, kind='nope').content)['error']
    empty = InspectionReport.objects.create(trigger=InspectionReport.TRIGGER_MANUAL, user=user)
    assert json.loads(call_view(InspectionFileView, user, method='get', pk=empty.id, kind='html').content)['error']
