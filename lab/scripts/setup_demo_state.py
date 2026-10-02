#!/usr/bin/env python3
# setup_demo_state.py — 把从零重建的实验室带到「演示态」（在 spug 容器内运行）。
#
# 背景（2026-10-02 从零重建实测）：验收套件与手工演示隐含两类环境状态，重建后
# 无人恢复 → 4 条用例失败：
#   ① 发布演示的源仓库 /data/repos/demo-app.git（bare）是手工建的，重建即丢；
#   ② e2e 幂等用例要求四册 Playbook 至少部署过一轮（否则首跑 changed≠0）。
# 本脚本把这两件事固化，幂等可重跑（playbook 本身幂等，重跑即 changed=0）。
#
# 做四件事：
#   1. 建 demo-app.git（bare，静态页 v1.0；已存在则跳过）
#   2. 部署四册 Playbook：baseline→全部 / nginx→web / mysql-master-slave→db /
#      redis-sentinel→cache（逐个等终态并打印 recap）
#   3. 触发一轮巡检 + 一轮网络探测，等完成并打印计数
#   4. 打印「演示态就绪」清单
#
# 用法：bash lab/scripts/setup_demo_state.sh [--skip-deploy]
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1'
DEMO_REPO = '/data/repos/demo-app.git'
WEBHOOK_HINT = 'lab/scripts/demo_release.sh'

PLAYBOOKS = [
    ('baseline', ['web-01', 'web-02', 'db-01', 'db-02', 'cache-01', 'cache-02', 'cache-03']),
    ('nginx', ['web-01', 'web-02']),
    ('mysql-master-slave', ['db-01', 'db-02']),
    ('redis-sentinel', ['cache-01', 'cache-02', 'cache-03']),
]

DEMO_INDEX = '''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>OpsForge Demo App v1.0</title></head>
<body><h1>OpsForge Demo App</h1><p>version: 1.0</p></body>
</html>
'''


def api(path, method='GET', data=None, token=None, timeout=240):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('x-token', token)
    body = json.dumps(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(req, body, timeout=timeout) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f'HTTP {e.code} {path}: {e.read().decode()[:300]}')


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f'命令失败: {cmd}\n{r.stderr[:300]}')
    return r.stdout


def ensure_demo_repo():
    if os.path.exists(DEMO_REPO):
        print(f'[SKIP ] {DEMO_REPO} 已存在')
        return
    sh('rm -rf /tmp/demo-app && mkdir -p /tmp/demo-app && cd /tmp/demo-app && git init -q -b main')
    with open('/tmp/demo-app/index.html', 'w', encoding='utf-8') as f:
        f.write(DEMO_INDEX)
    with open('/tmp/demo-app/build.sh', 'w', encoding='utf-8') as f:
        f.write('#!/bin/bash\n')
    os.chmod('/tmp/demo-app/build.sh', 0o755)
    sh('cd /tmp/demo-app && git add -A && '
       'git -c user.email=opsforge@lab.local -c user.name=opsforge commit -qm "feat: demo static page v1.0" && '
       f'git clone -q --bare /tmp/demo-app {DEMO_REPO} && rm -rf /tmp/demo-app')
    print(f'[ADD  ] {DEMO_REPO}（静态页 v1.0，main 分支）')


def deploy(token, host_ids, pb_id, name):
    r = api('/api/ansible_ops/exec/', 'POST',
            {'host_ids': host_ids, 'playbook_id': pb_id}, token=token)
    jid = (r.get('data') or {}).get('job_id')
    if not jid:
        raise SystemExit(f'{name} 提交失败：{r}')
    t0 = time.time()
    while time.time() - t0 < 900:
        d = api(f'/api/ansible_ops/jobs/{jid}/', token=token).get('data') or {}
        if d.get('finished_at'):
            print(f'[DEPLOY] {name}: job {jid} {d.get("status")} {d.get("summary")} '
                  f'（{time.time()-t0:.0f}s）')
            return d
        time.sleep(2)
    raise SystemExit(f'{name} job {jid} 超时')


def main():
    skip_deploy = '--skip-deploy' in sys.argv
    user, password = os.environ.get('SPUG_USER'), os.environ.get('SPUG_PASS')
    if not user or not password:
        raise SystemExit('缺少 SPUG_USER / SPUG_PASS（由包装脚本注入）')
    token = (api('/api/account/login/', 'POST',
                 {'username': user, 'password': password, 'type': 'default'}).get('data') or {})['access_token']
    print('[LOGIN] ok')

    ensure_demo_repo()

    host_ids = {h['name']: h['id'] for h in (api('/api/host/', token=token).get('data') or [])}
    pbs = {p['name']: p['id'] for p in (api('/api/ansible_ops/playbooks/', token=token).get('data') or [])}
    missing = [n for n, _ in PLAYBOOKS if n not in pbs]
    if missing:
        raise SystemExit(f'Playbook 缺失 {missing}，请先运行 lab/scripts/import_playbooks.sh')

    if skip_deploy:
        print('[SKIP ] --skip-deploy：跳过四册部署')
    else:
        for name, hosts in PLAYBOOKS:
            deploy(token, [host_ids[h] for h in hosts], pbs[name], name)

    # 一轮巡检 + 一轮探测（让报告/Dashboard 有数据）
    rid = (api('/api/ansible_ops/inspection/run/', 'POST', {}, token=token).get('data') or {}).get('report_id')
    for _ in range(120):
        rep = api(f'/api/ansible_ops/inspections/{rid}/', token=token).get('data') or {}
        if rep.get('finished_at'):
            print(f'[INSP  ] report {rid}: ok={rep.get("total_ok")} warn={rep.get("total_warn")} '
                  f'crit={rep.get("total_crit")} hosts={rep.get("hosts_total")}')
            break
        time.sleep(2)
    api('/api/ansible_ops/net-probe/run/', 'POST', {}, token=token)
    time.sleep(3)
    run = (api('/api/ansible_ops/net-probe/latest/', token=token).get('data') or {}).get('run') or {}
    print(f'[PROBE ] 探测矩阵: 通 {run.get("total_ok")} / 断 {run.get("total_fail")}')

    print('== 演示态就绪 ==')
    print(f'   发布演示：bash {WEBHOOK_HINT}')
    print('   验收套件：cd tests/acceptance && npx playwright test')


if __name__ == '__main__':
    main()
