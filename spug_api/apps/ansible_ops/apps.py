# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎；告警自愈注册自愈触发 signal（post_save Alarm）
from django.apps import AppConfig


class AnsibleOpsConfig(AppConfig):
    name = 'apps.ansible_ops'

    def ready(self):
        from apps.ansible_ops.heal.signals import register_heal_signals
        register_heal_signals()
