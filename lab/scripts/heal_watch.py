#!/usr/bin/env python3
# heal_watch.py — 告警自愈验收观察器（在 spug 容器内运行）。
#
# 用途：注入故障后轮询 HealEvent，把"本次注入触发的闭环"等待到终态并输出
# 一行 JSON 供宿主采集。比每秒 docker exec shell 查询快一个量级。
#
# 用法（宿主）：
#   docker exec spug python3 /tmp/heal_watch.py --since-sec 30 --timeout 90
# 逻辑：取 created_at >= (now - since-sec) 的最新非 suppressed 事件（或任意
# 最新事件，--allow-suppressed），等到终态（recovered/failed）或超时，
# 输出 {"event_id","status","duration_ms","before","after","created_at","finished_at"}。
import argparse
import json
import os
import sys
import time

sys.path.insert(0, '/data/spug/spug_api')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spug.settings')

import django  # noqa: E402

django.setup()

from datetime import datetime  # noqa: E402

from apps.ansible_ops.models import HealEvent  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout', type=int, default=120)
    ap.add_argument('--allow-suppressed', action='store_true')
    args = ap.parse_args()

    # 只认本进程启动后创建的事件：注入在宿主先发生、watch 随后启动（间隔 <1s），
    # 事件创建（告警产生）必然晚于启动时刻——首轮查询就不会被上一轮的旧终态
    # 事件污染（实测教训：按 created_at>=now-90s 过滤会捞到上轮事件假返回）
    started = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        qs = HealEvent.objects.filter(created_at__gte=started)
        if not args.allow_suppressed:
            qs = qs.exclude(status=HealEvent.STATUS_SUPPRESSED)
        event = qs.order_by('-id').first()
        if event and event.status in (HealEvent.STATUS_RECOVERED, HealEvent.STATUS_FAILED):
            print(json.dumps({
                'event_id': event.id, 'status': event.status,
                'duration_ms': event.duration_ms,
                'before': event.before_status, 'after': event.after_status,
                'created_at': event.created_at, 'finished_at': event.finished_at,
                'alert_name': event.alert_name, 'host': event.host,
            }, ensure_ascii=False))
            return
        time.sleep(0.5)
    print(json.dumps({'status': 'timeout', 'started_after': started}, ensure_ascii=False))


if __name__ == '__main__':
    main()
