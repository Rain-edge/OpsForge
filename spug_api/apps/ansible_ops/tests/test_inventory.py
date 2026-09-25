# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：generate_inventory 用例（约定验收 1：分组/端口/特殊字符转义）
import pytest

from apps.account.models import User
from apps.host.models import Host, Group
from apps.ansible_ops.inventory import generate_inventory, safe_token, write_key_file


TEST_KEY = '-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----\n'


@pytest.fixture
def user(db):
    return User.objects.create(username='tester', nickname='Tester', password_hash='x')


def make_host(user, name, hostname, port=22, username='ops', pkey=TEST_KEY):
    return Host.objects.create(
        name=name, hostname=hostname, port=port, username=username,
        pkey=pkey, created_by=user)


@pytest.mark.django_db
def test_inventory_grouping(user):
    web1 = make_host(user, 'web-01', 'web-01', 22)
    web2 = make_host(user, 'web-02', 'web-02', 22)
    db1 = make_host(user, 'db-01', 'db-01', 22)
    web_group = Group.objects.create(name='web')
    db_group = Group.objects.create(name='db')
    web_group.hosts.add(web1, web2)
    db_group.hosts.add(db1)
    text = generate_inventory(Host.objects.all())
    assert '[web]' in text and '[db]' in text
    web_section = text.split('[web]')[1].split('[')[0]
    assert 'ansible_host=web-01' in web_section
    assert 'ansible_host=db-01' not in web_section


@pytest.mark.django_db
def test_inventory_ungrouped(user):
    make_host(user, 'lone', 'lone-host')
    text = generate_inventory(Host.objects.all())
    assert '[ungrouped]' in text
    assert 'ansible_host=lone-host' in text


@pytest.mark.django_db
def test_inventory_port_and_user(user):
    make_host(user, 'node', '10.0.0.5', port=2222, username='deployer')
    text = generate_inventory(Host.objects.all())
    assert 'ansible_port=2222' in text
    assert 'ansible_user=deployer' in text


@pytest.mark.django_db
def test_inventory_special_chars(user):
    make_host(user, 'web 01 (旧)', 'web-01', 22)
    group = Group.objects.create(name='web/组 1!')
    group.hosts.add(Host.objects.get(name='web 01 (旧)'))
    text = generate_inventory(Host.objects.all())
    # 组名与主机别名中的非法字符被替换为下划线，INI 结构不被破坏
    assert '[web___1_]' in text
    assert 'web_01____' in text
    assert '(旧)' not in text


def test_safe_token():
    assert safe_token('web-01') == 'web-01'
    assert safe_token('a b/c.d') == 'a_b_c_d'
    assert safe_token(None) == ''


@pytest.mark.django_db
def test_inventory_key_file(user, tmp_path):
    make_host(user, 'node-a', 'node-a', 22, pkey=TEST_KEY)
    make_host(user, 'node-b', 'node-b', 22, pkey=TEST_KEY)
    make_host(user, 'node-c', 'node-c', 22, pkey=None)
    key_dir = tmp_path / 'keys'
    text = generate_inventory(Host.objects.all(), key_dir)
    # 相同私钥复用同一文件，缺失私钥的主机不写密钥变量
    assert 'ansible_ssh_private_key_file=' in text
    assert text.count('.pem') == 2  # node-a/node-b 共用一个文件，node-c 无
    key_files = list(key_dir.glob('*.pem'))
    assert len(key_files) == 1
    assert key_files[0].stat().st_mode & 0o777 == 0o600


def test_write_key_file_reuse(tmp_path):
    f1 = write_key_file(tmp_path, TEST_KEY)
    f2 = write_key_file(tmp_path, TEST_KEY)
    assert f1 == f2
    different = write_key_file(tmp_path, TEST_KEY + 'x')
    assert different != f1
