#!/usr/bin/env python3
# mock_webhook.py — 告警自愈验收辅助：本机模拟钉钉/企微机器人接收端。
#
# 用途：无真实机器人 webhook 时验证消息外发（验收标准 3 的截图来源）。
# 在 spug 容器内运行，收 heal/notify.py 的 POST（返回 errcode=0 模拟成功），
# 消息落盘并可经 GET / 渲染成 HTML 预览页（供浏览器截图）。
#
# 用法：docker exec -d spug python3 /tmp/mock_webhook.py
#       消息预览：curl http://127.0.0.1:9999/（容器内）或宿主浏览器经端口映射
# 依 lab/docker/Dockerfile 端口映射：宿主访问 http://127.0.0.1:8002/
from http.server import BaseHTTPRequestHandler, HTTPServer
from html import escape
import json
import os
import re
import threading

STORE = '/tmp/heal-mock'
os.makedirs(STORE, exist_ok=True)
_lock = threading.Lock()


def _list_msgs():
    files = sorted(f for f in os.listdir(STORE) if f.endswith('.json'))
    return [(f, json.load(open(f'{STORE}/{f}', encoding='utf-8'))) for f in files]


def _md_to_html(text):
    """极简 markdown 渲染（标题/粗体/换行），仅够消息预览。"""
    text = escape(text)
    text = re.sub(r'### (.*)', r'<h3>\1</h3>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    return text.replace('\n', '<br>')


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8')
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {'raw': body}
        with _lock:
            n = len([f for f in os.listdir(STORE) if f.endswith('.json')]) + 1
            with open(f'{STORE}/msg-{n:03d}.json', 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        out = json.dumps({'errcode': 0, 'errmsg': 'ok(mock)'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        if self.path == '/clear':
            with _lock:
                for f in os.listdir(STORE):
                    os.remove(f'{STORE}/{f}')
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'cleared')
            return
        msgs = _list_msgs()
        rows = []
        for name, payload in msgs:
            # 钉钉渠道是 markdown.text，企微渠道是 markdown.content——两种都要取，
            # 否则企微消息在预览页退化成裸 JSON（2026-10-02 修复）
            md = payload.get('markdown') or {}
            text = md.get('text') or md.get('content') or json.dumps(payload, ensure_ascii=False)
            rows.append(f'<div class="msg"><h2>{name}</h2>{_md_to_html(text)}</div>')
        html = ('<!doctype html><meta charset="utf-8"><title>OpsForge mock webhook</title>'
                '<style>body{font-family:sans-serif;max-width:680px;margin:24px auto;background:#f5f6f7}'
                '.msg{background:#fff;border-radius:8px;padding:12px 20px;margin-bottom:16px;'
                'box-shadow:0 1px 3px rgba(0,0,0,.12)}</style>'
                f'<h1>OpsForge mock webhook（{len(msgs)} 条）</h1>' + '\n'.join(rows))
        out = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass  # 静默访问日志，验收时保持容器输出干净


if __name__ == '__main__':
    print('mock webhook listening on :9999, store=' + STORE)
    HTTPServer(('0.0.0.0', 9999), Handler).serve_forever()
