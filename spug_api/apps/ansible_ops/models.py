# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎
from django.db import models
from libs import ModelMixin, human_datetime
from apps.account.models import User
import json


class AnsibleJob(models.Model, ModelMixin):
    """一次 Ansible 执行（ad-hoc 或 Playbook）的记录与结果。

    JSON 字段沿用 spug 既有惯例：TextField 存储，视图层 json.loads/dumps
    （同 apps/exec/models.py 的 host_ids）。
    """
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_PARTIAL = 'partial'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = (
        (STATUS_PENDING, '等待执行'),
        (STATUS_RUNNING, '执行中'),
        (STATUS_SUCCESS, '成功'),
        (STATUS_PARTIAL, '部分成功'),
        (STATUS_FAILED, '失败'),
    )

    user = models.ForeignKey(User, models.PROTECT, related_name='+')
    host_ids = models.TextField(default='[]')
    # Playbook 管理 落 Playbook 模型后升级为真外键，本次任务仅占位
    playbook_id = models.IntegerField(null=True)
    raw_module = models.TextField(null=True)
    extra_vars = models.TextField(default='{}')
    status = models.CharField(max_length=20, default=STATUS_PENDING, choices=STATUS_CHOICES)
    summary = models.TextField(default='{}')
    log_path = models.CharField(max_length=255, default='')
    created_at = models.CharField(max_length=20, default=human_datetime)
    finished_at = models.CharField(max_length=20, null=True)

    def to_view(self):
        tmp = self.to_dict()
        tmp['host_ids'] = json.loads(self.host_ids)
        tmp['raw_module'] = json.loads(self.raw_module) if self.raw_module else None
        tmp['extra_vars'] = json.loads(self.extra_vars)
        tmp['summary'] = json.loads(self.summary)
        return tmp

    def __repr__(self):
        return '<AnsibleJob %r>' % self.id

    class Meta:
        db_table = 'ansible_jobs'
        ordering = ('-id',)
