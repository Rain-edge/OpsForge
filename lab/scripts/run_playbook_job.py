#!/usr/bin/env python3
"""Playbook 库临时工具：经平台 API 触发 playbook job 并轮询到终态。
用法：python3 run_job.py <playbook_id> <host_ids 逗号分隔> [extra_vars JSON]
凭据读 lab/nodes/.env（SPUG_USER/SPUG_PASS）。"""
import json
import sys
import time
import urllib.request

BASE = 'http://localhost/api'


def api(path, method='GET', payload=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['x-token'] = token
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode())
    if result.get('error'):
        raise RuntimeError(result['error'])
    return result.get('data')


def main():
    pb_id = int(sys.argv[1])
    host_ids = [int(x) for x in sys.argv[2].split(',')]
    extra_vars = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}

    env = {}
    with open('lab/nodes/.env', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k] = v

    token = api('/account/login/', 'POST',
                {'username': env['SPUG_USER'], 'password': env['SPUG_PASS'], 'type': 'default'}
                )['access_token']
    job = api('/ansible_ops/exec/', 'POST',
              {'host_ids': host_ids, 'playbook_id': pb_id, 'extra_vars': extra_vars}, token)
    job_id = job['job_id']
    print(f'job_id={job_id} playbook={pb_id} hosts={host_ids} extra_vars={extra_vars or "{}"}')

    deadline = time.time() + 1200
    while time.time() < deadline:
        time.sleep(2)
        detail = api(f'/ansible_ops/jobs/{job_id}/', token=token)
        if detail['status'] not in ('pending', 'running'):
            print(f"status={detail['status']} finished_at={detail['finished_at']}")
            print('summary=' + json.dumps(detail['summary'], ensure_ascii=False))
            return 0 if detail['status'] == 'success' else 2
    print('TIMEOUT')
    return 3


if __name__ == '__main__':
    sys.exit(main())
