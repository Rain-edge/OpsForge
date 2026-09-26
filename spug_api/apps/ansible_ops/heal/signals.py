# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：自愈触发挂点——post_save(Alarm) signal 注册。
#
# 上游 monitor 告警链路的落库点（monitor.utils.handle_notify → Alarm.objects.
# create）即"告警发生"事件源，此处注册 signal 即完成挂钩，上游文件零改动。
# 注册放 apps.py ready()（Django signal 标准姿势），handler 在 heal.service。
from django.db.models.signals import post_save

from apps.ansible_ops.heal.service import handle_alert


def register_heal_signals():
    # sender 用 app_label.Model（spug 的 INSTALLED_APPS 是 apps.alarm，label 为 alarm）
    post_save.connect(
        handle_alert, sender='alarm.Alarm', dispatch_uid='opsforge-heal-alarm')
