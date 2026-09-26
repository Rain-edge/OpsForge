# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：网络探测与设备备份入口。
#
# 形态同 run_inspection（定时方案：supervisor 常驻调度，crontab 不
# 继承容器 env）：
#   默认        单轮（探测一轮 + 备份一轮，CLI 调试 / 实验脚本用）
#   --loop      常驻调度：探测间隔 OPSFORGE_NET_PROBE_INTERVAL（默认 30s）、
#               备份间隔 OPSFORGE_NET_BACKUP_INTERVAL（默认 120s），启动即各先跑一轮
#   --probe-only / --backup-only   单轮模式只跑其中一类
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.ansible_ops.models import NetProbeRun
from apps.ansible_ops.net import backup, probe
import logging
import os


class Command(BaseCommand):
    help = '网络探测与设备备份：默认单轮；--loop 常驻定时调度'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='常驻定时调度模式')
        parser.add_argument('--probe-only', action='store_true', help='单轮模式只跑探测')
        parser.add_argument('--backup-only', action='store_true', help='单轮模式只跑备份')
        parser.add_argument('--probe-interval', type=int, default=None,
                            help='探测间隔秒数（默认取 OPSFORGE_NET_PROBE_INTERVAL 或 30）')
        parser.add_argument('--backup-interval', type=int, default=None,
                            help='备份间隔秒数（默认取 OPSFORGE_NET_BACKUP_INTERVAL 或 120）')

    def handle(self, *args, **options):
        if options['loop']:
            self._loop(options)
        else:
            if not options['backup_only']:
                self._probe_round(NetProbeRun.TRIGGER_MANUAL)
            if not options['probe_only']:
                self._backup_round()

    def _env_int(self, name, default):
        """env 解析坏值回退默认（与 probe.fail_threshold 口径一致），
        非 supervisor 反复拉起崩溃循环。"""
        try:
            return int(os.environ[name]) if os.environ.get(name) else default
        except ValueError:
            return default

    def _loop(self, options):
        probe_interval = options['probe_interval'] or self._env_int(
            'OPSFORGE_NET_PROBE_INTERVAL', 30)
        backup_interval = options['backup_interval'] or self._env_int(
            'OPSFORGE_NET_BACKUP_INTERVAL', 120)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        log = logging.getLogger('opsforge.net')
        log.info('网络调度启动：探测 %ss / 备份 %ss', probe_interval, backup_interval)
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler()
        # max_instances=1：同类轮次未结束跳过后续触发，防堆积（同 run_inspection 口径）
        scheduler.add_job(self._scheduled_probe, 'interval',
                          seconds=probe_interval, max_instances=1, misfire_grace_time=60)
        scheduler.add_job(self._scheduled_backup, 'interval',
                          seconds=backup_interval, max_instances=1, misfire_grace_time=60)
        # 启动即先各跑一轮再进入调度循环（同 run_inspection 口径）
        self._probe_round(NetProbeRun.TRIGGER_SCHEDULED)
        self._backup_round()
        scheduler.start()

    def _probe_round(self, trigger):
        close_old_connections()
        log = logging.getLogger('opsforge.net')
        try:
            run = probe.run_round(trigger)
            self.stdout.write(
                f'探测轮完成: id={run.id} ok={run.total_ok} fail={run.total_fail}')
        except Exception:
            log.exception('探测轮失败（矩阵配置或探测引擎异常）')
        finally:
            close_old_connections()

    def _backup_round(self):
        close_old_connections()
        log = logging.getLogger('opsforge.net')
        try:
            results = backup.run_backup_round()
            for r in results:
                if r['error']:
                    log.warning('备份 %s 失败: %s', r['name'], r['error'])
                else:
                    self.stdout.write(
                        f"备份 {r['name']}: ts={r['ts']} changed={r['changed']}")
        except Exception:
            log.exception('备份轮失败（设备清单配置异常）')
        finally:
            close_old_connections()

    def _scheduled_probe(self):
        self._probe_round(NetProbeRun.TRIGGER_SCHEDULED)

    def _scheduled_backup(self):
        self._backup_round()
