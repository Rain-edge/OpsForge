# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎；Playbook 管理 增加 Playbook 模型与 job 外键升级
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
    # Playbook 管理 升级真外键（T1 占位整型）：SET_NULL 保留任务历史，删除 Playbook 不受阻
    playbook = models.ForeignKey('Playbook', models.SET_NULL, null=True, related_name='+')
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


class Playbook(models.Model, ModelMixin):
    """Playbook 库：平台内增删改查 + git 版本化。

    content 与版本库文件（REPOS_DIR/ansible_ops/playbooks/{id}.yml）互为镜像，
    写路径统一经 repo.write_and_commit，保证 git 提交与字段更新同拍完成。
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, default='')
    content = models.TextField(default='')
    updated_by = models.ForeignKey(User, models.PROTECT, related_name='+')
    updated_at = models.CharField(max_length=20, default=human_datetime)

    def __repr__(self):
        return '<Playbook %r>' % self.id

    class Meta:
        db_table = 'ansible_playbooks'
        ordering = ('-id',)
