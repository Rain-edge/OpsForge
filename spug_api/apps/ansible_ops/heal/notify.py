# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：自愈结果 webhook 外发（钉钉/企微机器人）。
#
# URL 从环境变量读取，不硬编码（约定）：
#   OPSFORGE_WEBHOOK_DING = https://oapi.dingtalk.com/robot/send?access_token=xxx[|加签密钥]
#   OPSFORGE_WEBHOOK_WXY  = https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
# 读取顺序：进程环境变量优先，其次配置文件 HEAL_WEBHOOK_FILE（默认
# /data/spug/spug_api/heal_webhook.env，KEY=VALUE 行格式）——supervisor 常驻
# 进程的 env 取自容器创建时，运行期改 env 必须重建容器，配置文件供不重建
# 容器的实验/临时场景覆盖。文件不入库（口令敏感）。
# 钉钉 URL 支持「|加签密钥」后缀，有则按钉钉加签协议附 timestamp+sign。
# 未配置的渠道静默跳过；发送失败只记日志，不影响闭环状态落库。
#
# 与上游 libs/spug.py Notification 的分工：上游通知在告警时刻发出（配置存
# 联系人/报警组），消息格式固定不含自愈结果；本模块在闭环结束时发出，消息
# 含主机/指标/触发动作/自愈结果（约定字段），凭据走环境变量而非报警组配置。
import logging
import os
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 10
HEAL_WEBHOOK_FILE = os.environ.get(
    'HEAL_WEBHOOK_FILE', '/data/spug/spug_api/heal_webhook.env')


def _load_webhook_env():
    """合并进程 env 与配置文件的 webhook 配置；env 优先。"""
    values = {}
    try:
        with open(HEAL_WEBHOOK_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    values[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning('heal webhook 配置文件读取失败: %s', e)
    for key in ('OPSFORGE_WEBHOOK_DING', 'OPSFORGE_WEBHOOK_WXY'):
        env_value = os.environ.get(key)
        if env_value:
            values[key] = env_value
    return values


def _ding_url(url):
    """拆分「URL|加签密钥」，有密钥时按钉钉自定义机器人加签协议补参数。"""
    url, _, secret = url.partition('|')
    if not secret:
        return url
    from libs.webhook import gen_dd_sign
    timestamp, sign = gen_dd_sign(secret)
    return f'{url}&timestamp={timestamp}&sign={quote_plus(sign)}'


def _post(url, payload, channel):
    try:
        res = requests.post(url, json=payload, timeout=TIMEOUT)
        # 钉钉/企微正常时返回 {"errcode":0}，业务失败也只记日志不上抛
        body = res.json() if res.headers.get('Content-Type', '').startswith('application/json') else {}
        if res.status_code != 200 or body.get('errcode'):
            logger.warning('heal webhook %s 发送失败: %s %s', channel, res.status_code, body)
    except Exception as e:
        logger.warning('heal webhook %s 发送异常: %s', channel, e)


def _markdown(payload):
    icon = {'recovered': '✅ 自愈成功', 'failed': '⚠️ 自愈失败，需人工介入',
            'suppressed': '⏸️ 冷却抑制（未触发自愈）', 'running': '🔄 闭环执行中'}
    title = f"OpsForge 自愈报告：{icon.get(payload['status'], payload['status'])}"
    lines = [
        f"- **主机**：{payload.get('host') or payload.get('alert_target') or '-'}",
        f"- **指标**：{payload.get('alert') or '-'}",
        f"- **触发动作**：规则 {payload.get('rule')} → Playbook {payload.get('playbook')}"
        + (f"（job #{payload['job_id']}）" if payload.get('job_id') else '（未执行）'),
    ]
    if payload.get('before') or payload.get('after'):
        lines.append(f"- **复检**：{payload.get('before') or '-'} → {payload.get('after') or '-'}")
    if payload.get('duration_ms') is not None:
        lines.append(f"- **耗时**：{payload['duration_ms']} ms")
    if payload.get('error'):
        lines.append(f"- **失败原因**：{payload['error']}")
    return title, '\n'.join(lines)


def notify_heal_report(payload):
    """把闭环结果推送到所有已配置渠道。payload 字段见 _markdown。

    由 service 在闭环各终点调用（恢复/失败/抑制），本函数自身不抛异常。
    """
    values = _load_webhook_env()
    ding = values.get('OPSFORGE_WEBHOOK_DING', '')
    wxy = values.get('OPSFORGE_WEBHOOK_WXY', '')
    if not ding and not wxy:
        logger.info('heal webhook 未配置（OPSFORGE_WEBHOOK_DING/WXY），跳过外发')
        return
    title, text = _markdown(payload)
    if ding:
        _post(_ding_url(ding), {'msgtype': 'markdown', 'markdown': {'title': title, 'text': text}}, 'ding')
    if wxy:
        # 企微 markdown 消息标题由首行 # 渲染，content 上限 4096 字节，闭环消息远小于此
        _post(wxy, {'msgtype': 'markdown', 'markdown': {'content': f'### {title}\n' + text}}, 'wxy')
