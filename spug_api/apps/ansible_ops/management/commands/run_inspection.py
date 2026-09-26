# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：巡检入口。
#
# 两种形态（定时方案经用户拍板：supervisor 常驻调度，见踩坑记录1）：
#   默认     单轮执行（CLI 调试 / 实验脚本用），trigger=manual
#   --loop   常驻调度（容器 supervisor program 用），按 OPSFORGE_INSPECTION_INTERVAL
#            秒（默认 300）触发，trigger=scheduled；启动即先跑一轮
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from libs import human_datetime

from apps.ansible_ops.inspection.service import submit_round
from apps.ansible_ops.models import InspectionReport
import logging
import os


class Command(BaseCommand):
    help = '批量巡检：默认单轮；--loop 常驻定时调度'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='常驻定时调度模式')
        parser.add_argument(
            '--interval', type=int, default=None,
            help='调度间隔秒数（默认取 OPSFORGE_INSPECTION_INTERVAL 或 300）')

    def handle(self, *args, **options):
        if options['loop']:
            self._loop(options['interval'])
        else:
            report_id = submit_round(InspectionReport.TRIGGER_MANUAL)
            self.stdout.write(f'巡检轮次已提交: report_id={report_id}')
            self._wait(report_id)

    def _loop(self, interval):
        interval = interval or int(os.environ.get('OPSFORGE_INSPECTION_INTERVAL', 300))
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        log = logging.getLogger('opsforge.inspection')
        log.info('巡检调度启动，间隔 %ss', interval)
        self._recover_ghosts()
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler()
        # 启动即先同步跑一轮再进入调度循环；max_instances=1：单轮未结束时跳过
        # 后续触发，防轮次堆积；轮次异常由 APScheduler 记日志继续，进程不退出
        scheduler.add_job(
            self._scheduled_round, 'interval', seconds=interval,
            max_instances=1, misfire_grace_time=60)
        self._scheduled_round()
        scheduler.start()

    def _recover_ghosts(self):
        """进程重启后把遗留的 running 轮次落 failed（前端不至于无限轮询）。
        极小概率误杀恰在执行中的手动轮：其完成时会以真实结果覆盖回写。"""
        ghosts = InspectionReport.objects.filter(status=InspectionReport.STATUS_RUNNING)
        count = ghosts.update(
            status=InspectionReport.STATUS_FAILED,
            summary='{"error": "调度进程重启，轮次中断"}',
            finished_at=human_datetime())
        if count:
            self.stdout.write(f'已将 {count} 条遗留 running 轮次标记为失败')

    def _scheduled_round(self):
        close_old_connections()
        log = logging.getLogger('opsforge.inspection')
        try:
            report_id = submit_round(InspectionReport.TRIGGER_SCHEDULED)
            log.info('定时巡检已提交: report_id=%s', report_id)
            self._wait(report_id)
        except Exception:
            log.exception('定时巡检提交失败')
        finally:
            close_old_connections()

    def _wait(self, report_id):
        """阻塞等待轮次终态（CLI 单轮要看结果；调度轮次等到完成再让出，
        配合 max_instances=1 形成天然互斥）。"""
        import time
        while True:
            report = InspectionReport.objects.filter(pk=report_id).first()
            if report and report.status != InspectionReport.STATUS_RUNNING:
                self.stdout.write(
                    f'巡检结束: status={report.status} '
                    f'ok={report.total_ok} warn={report.total_warn} crit={report.total_crit}')
                return
            time.sleep(2)
