#!/usr/bin/env python3
# setup_heal_demo.py — 告警自愈从零重建后的自愈演示环境初始化（在 spug 容器内运行）。
#
# 背景：原实验链路里「检测任务 + 自愈规则」是手工在 UI/API 建的，down -v 重建后
# 无法一键恢复自愈演示（RUNBOOK §14 只写了手工核对清单）。本脚本把这三步固化：
#   1. 建检测任务「nginx 进程检测」（进程检测 web-01 的 nginx：rate=1/threshold=1/quiet=1）
#   2. 建自愈规则「web-nginx-restart」（keyword=nginx / host=web-01 / playbook=heal-nginx
#      / check_name=process / cooldown=1 分钟）
#   3. 写 heal_webhook.env 指向本机 mock 接收端（钉钉/企微双渠道，文件不存在才写，--force 覆盖）
#
# 幂等：已存在的对象跳过（打印 SKIP），可重复执行；全部经平台 API，不直连 DB。
# 用法：bash lab/scripts/setup_heal_demo.sh [--force]
# 前置：平台已初始化、节点已纳管（register_hosts.sh）、Playbook 已导入（import_playbooks.sh）
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get('OPSFORGE_API', 'http://127.0.0.1')
WEBHOOK_FILE = '/data/spug/spug_api/heal_webhook.env'
DEVICES_ENV_FILE = '/data/spug/spug_api/storage/net_devices.env'
MOCK_URL = 'http://127.0.0.1:9999'
DET_NAME = 'nginx 进程检测'
RULE_NAME = 'web-nginx-restart'
TARGET_HOST = 'web-01'
PLAYBOOK_NAME = 'heal-nginx'


def api(path, method='GET', data=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('x-token', token)
    body = json.dumps(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(req, body, timeout=30) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f'HTTP {e.code} {path}: {e.read().decode()[:300]}')


def main():
    force = '--force' in sys.argv
    user, password = os.environ.get('SPUG_USER'), os.environ.get('SPUG_PASS')
    if not user or not password:
        raise SystemExit('缺少 SPUG_USER / SPUG_PASS（由包装脚本从 lab/nodes/.env 注入）')

    login = api('/api/account/login/', 'POST',
                {'username': user, 'password': password, 'type': 'default'})
    token = (login.get('data') or {}).get('access_token')
    if not token:
        raise SystemExit(f'登录失败：{login}')
    print('[LOGIN] ok')

    # 主机 web-01（检测任务 target 与规则 match_host 都用它）
    hosts = api('/api/host/', token=token).get('data') or []
    host = next((h for h in hosts if h.get('name') == TARGET_HOST), None)
    if not host:
        raise SystemExit(f'未找到主机 {TARGET_HOST}，请先运行 lab/scripts/register_hosts.sh')
    print(f'[HOST ] {TARGET_HOST} id={host["id"]}')

    # 1) 检测任务
    dets = api('/api/monitor/', token=token).get('data', {}).get('detections', [])
    if any(d.get('name') == DET_NAME for d in dets):
        print(f'[SKIP ] 检测任务已存在：{DET_NAME}')
    else:
        r = api('/api/monitor/', 'POST', {
            'name': DET_NAME, 'group': 'opsforge', 'targets': [host['id']],
            'type': '3', 'extra': 'nginx', 'desc': 'OpsForge 自愈演示：nginx 进程检测',
            'rate': 1, 'threshold': 1, 'quiet': 1,
            'notify_grp': [], 'notify_mode': [],
        }, token=token)
        if r.get('error'):
            raise SystemExit(f'建检测任务失败：{r["error"]}')
        print(f'[ADD  ] 检测任务：{DET_NAME}（进程 nginx @ {TARGET_HOST}，rate=1min）')

    # 2) 自愈规则（playbook 按名字反查 id，避免硬编码 id 随重建变化）
    pbs = api('/api/ansible_ops/playbooks/', token=token).get('data') or []
    pb = next((p for p in pbs if p.get('name') == PLAYBOOK_NAME), None)
    if not pb:
        raise SystemExit(f'未找到 Playbook {PLAYBOOK_NAME}，请先运行 lab/scripts/import_playbooks.sh')
    rules = api('/api/ansible_ops/heal-rules/', token=token).get('data') or []
    if any(x.get('name') == RULE_NAME for x in rules):
        print(f'[SKIP ] 自愈规则已存在：{RULE_NAME}')
    else:
        r = api('/api/ansible_ops/heal-rules/', 'POST', {
            'name': RULE_NAME, 'match_keyword': 'nginx', 'match_host': TARGET_HOST,
            'playbook_id': pb['id'], 'check_name': 'process', 'cooldown': 1,
            'is_active': True,
        }, token=token)
        if r.get('error'):
            raise SystemExit(f'建自愈规则失败：{r["error"]}')
        print(f'[ADD  ] 自愈规则：{RULE_NAME}（nginx @ {TARGET_HOST} → playbook {pb["name"]}#{pb["id"]}）')

    # 3) webhook 配置（指向 mock；已存在则保留，--force 覆盖）
    if os.path.exists(WEBHOOK_FILE) and not force:
        print(f'[SKIP ] {WEBHOOK_FILE} 已存在（--force 可覆盖）')
    else:
        with open(WEBHOOK_FILE, 'w', encoding='utf-8') as f:
            f.write('# OpsForge 自愈结果外发配置（实验环境：指向本机 mock 接收端）\n')
            f.write('# 生产切换：改为真实机器人地址；钉钉支持 URL|加签密钥 后缀\n')
            f.write(f'OPSFORGE_WEBHOOK_DING={MOCK_URL}/ding\n')
            f.write(f'OPSFORGE_WEBHOOK_WXY={MOCK_URL}/wxy\n')
        print(f'[WRITE] {WEBHOOK_FILE} → mock（{MOCK_URL}）')

    # 4) 网络设备口令文件：storage 卷为空时备份/探测会静默失败
    #    （net/backup._device_password 抛"口令未配置"，调度器只记 WARNING）——
    #    2026-10-02 从零重建实测 e2e 备份 diff 用例因此失败，此处补齐
    ops_password = os.environ.get('OPS_PASSWORD')
    if not ops_password:
        print('[WARN ] OPS_PASSWORD 未注入，跳过 net_devices.env（网络备份将不可用）')
    elif os.path.exists(DEVICES_ENV_FILE) and not force:
        print(f'[SKIP ] {DEVICES_ENV_FILE} 已存在（--force 可覆盖）')
    else:
        with open(DEVICES_ENV_FILE, 'w', encoding='utf-8') as f:
            f.write('# 网络设备口令兜底文件（env OPS_PASSWORD 优先；本文件不入库不入镜像，\n')
            f.write('# storage 卷持久；由 lab/scripts/setup_heal_demo.sh 生成）\n')
            f.write(f'OPS_PASSWORD={ops_password}\n')
        os.chmod(DEVICES_ENV_FILE, 0o600)
        print(f'[WRITE] {DEVICES_ENV_FILE}（0600）')

    print('== 自愈演示环境就绪 ==')
    print('   注入：docker exec web-01 systemctl stop nginx')
    print('   观察：docker exec spug python3 /tmp/heal_watch.py --timeout 120'
          '   # MSYS 下需 MSYS2_ARG_CONV_EXCL="/tmp;/data"')


if __name__ == '__main__':
    main()
