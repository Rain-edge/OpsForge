#!/usr/bin/env python3
# Playbook 库导入器（在 spug 容器内执行）：把 /tmp/playbooks-src/*.yml 灌入平台 Playbook 库。
#
# 双轨机制说明（接口约定「存放位置」二选一的落地方案）：
#   仓库 lab/playbooks/ 是源（git 可追溯）；平台库是运行时形态（可编辑、git 版本化）。
#   主 playbook 文件走平台 API 入库（自带 syntax-check 与 git 提交）；
#   group_vars/host_vars/templates 由 import_playbooks.sh docker cp 进 REPOS_DIR
#   （ansible-playbook 自动加载 playbook 同目录的这些子目录，已容器内实测）。
#
# 用法：python3 import.py [--force]   # --force 时已存在的同名条目会被更新覆盖
# 环境变量：SPUG_USER / SPUG_PASS（登录凭据，由 import_playbooks.sh 经 docker exec -e 注入，
#           不落容器文件系统；局限：执行期可被宿主 ps / docker inspect 观测，实验环境已接受）
import json
import os
import sys
import urllib.request

BASE_URL = 'http://localhost'
API = BASE_URL + '/api'
SRC_DIR = '/tmp/playbooks-src'
DESCRIPTION = 'Playbook 库 playbook 库（源：lab/playbooks/，经 import.py 导入）'


def api(path, method='GET', payload=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['x-token'] = token
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode())
    if result.get('error'):
        raise RuntimeError(result['error'])
    return result.get('data')


def main():
    force = '--force' in sys.argv
    user = os.environ['SPUG_USER']
    password = os.environ['SPUG_PASS']

    token = api(API + '/account/login/', 'POST',
                {'username': user, 'password': password, 'type': 'default'})['access_token']

    existing = {item['name']: item['id'] for item in api(API + '/ansible_ops/playbooks/', token=token)}

    sources = sorted(f for f in os.listdir(SRC_DIR)
                     if f.endswith('.yml') and os.path.isfile(os.path.join(SRC_DIR, f)))
    print(f'源文件 {len(sources)} 个：{sources}')
    results = []
    for fname in sources:
        name = fname[:-4]
        with open(os.path.join(SRC_DIR, fname), encoding='utf-8') as f:
            content = f.read()
        payload = {'name': name, 'description': DESCRIPTION, 'content': content}
        try:
            if name in existing:
                if not force:
                    print(f'[SKIP] {name} 已存在 (id={existing[name]})，--force 可覆盖更新')
                    results.append((name, existing[name], 'skip'))
                    continue
                api(f'{API}/ansible_ops/playbooks/{existing[name]}/', 'PUT', payload, token)
                print(f'[UPD ] {name} 已更新 (id={existing[name]})')
                results.append((name, existing[name], 'updated'))
            else:
                data = api(API + '/ansible_ops/playbooks/', 'POST', payload, token)
                print(f'[OK  ] {name} 已创建 (id={data["id"]})')
                results.append((name, data['id'], 'created'))
        except Exception as e:
            print(f'[FAIL] {name}: {e}')
            sys.exit(1)
    print('== 导入完成：' + ', '.join(f'{n}={i}' for n, i, _ in results) + ' ==')


if __name__ == '__main__':
    main()
