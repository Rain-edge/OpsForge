# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎；Playbook 管理 增加 Playbook 模型与 job 外键升级；
# 巡检报表增加巡检报告模型；告警自愈增加自愈规则与闭环事件模型
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


class InspectionReport(models.Model, ModelMixin):
    """一轮批量巡检的记录。

    产物三件（manifest/html/xlsx）落 storage/reports/，字段只存路径——
    与 executor.log_path 同款"记录指向文件"的惯例，报告内容不进数据库。
    summary 存轮次摘要 JSON（计数 + 与上轮 diff），列表页无需读文件。
    """
    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_PARTIAL = 'partial'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = (
        (STATUS_RUNNING, '巡检中'),
        (STATUS_SUCCESS, '完成'),
        (STATUS_PARTIAL, '部分完成（有主机不可达）'),
        (STATUS_FAILED, '失败'),
    )

    TRIGGER_MANUAL = 'manual'
    TRIGGER_SCHEDULED = 'scheduled'
    TRIGGER_CHOICES = (
        (TRIGGER_MANUAL, '手动触发'),
        (TRIGGER_SCHEDULED, '定时巡检'),
    )

    trigger = models.CharField(max_length=20, choices=TRIGGER_CHOICES, default=TRIGGER_MANUAL)
    # 定时轮次无操作人，允许为空；PROTECT 防用户删除后记录悬空（同 AnsibleJob.user 口径）
    user = models.ForeignKey(User, models.PROTECT, null=True, related_name='+')
    status = models.CharField(max_length=20, default=STATUS_RUNNING, choices=STATUS_CHOICES)
    total_ok = models.IntegerField(default=0)
    total_warn = models.IntegerField(default=0)
    total_crit = models.IntegerField(default=0)
    hosts_total = models.IntegerField(default=0)
    hosts_unreachable = models.IntegerField(default=0)
    html_path = models.CharField(max_length=255, default='')
    xlsx_path = models.CharField(max_length=255, default='')
    manifest_path = models.CharField(max_length=255, default='')
    summary = models.TextField(default='{}')
    created_at = models.CharField(max_length=20, default=human_datetime)
    finished_at = models.CharField(max_length=20, null=True)

    def to_view(self):
        tmp = self.to_dict()
        tmp['summary'] = json.loads(self.summary)
        tmp['user'] = self.user.nickname if self.user else '-'
        return tmp

    def __repr__(self):
        return '<InspectionReport %r>' % self.id

    class Meta:
        db_table = 'inspection_reports'
        ordering = ('-id',)


class HealRule(models.Model, ModelMixin):
    """告警自愈规则：告警匹配条件 → 修复 Playbook + 冷却窗口。

    匹配口径（heal.service.handle_alert）：match_keyword 对「告警目标 + 消息」
    做不区分大小写的子串匹配；match_host 非空时再限定告警主机名。
    check_name 指定复检调用的 INSP 单项插件（inspection.CHECKS 注册名）。
    """
    name = models.CharField(max_length=100, unique=True)
    match_keyword = models.CharField(max_length=255, default='')
    match_host = models.CharField(max_length=100, default='')
    # PROTECT：闭环中/历史事件引用的 playbook 与规则创建人不可删
    playbook = models.ForeignKey('Playbook', models.PROTECT, related_name='+')
    extra_vars = models.TextField(default='{}')
    check_name = models.CharField(max_length=20, default='process')
    cooldown = models.IntegerField(default=10)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, models.PROTECT, related_name='+')
    created_at = models.CharField(max_length=20, default=human_datetime)
    updated_at = models.CharField(max_length=20, null=True)

    def to_view(self):
        tmp = self.to_dict()
        tmp['playbook_name'] = self.playbook.name if self.playbook_id else '-'
        tmp['created_by'] = self.created_by.nickname if self.created_by_id else '-'
        return tmp

    def __repr__(self):
        return '<HealRule %r>' % self.name

    class Meta:
        db_table = 'heal_rules'
        ordering = ('-id',)


class HealEvent(models.Model, ModelMixin):
    """一次自愈闭环全链路记录（约定：alert 来源、rule、job、
    before/after 状态、耗时 ms）。

    status 语义：
      running    闭环执行中
      recovered  复检恢复（含自愈前复检已恢复的"误报/先愈"路径）
      failed     自愈失败或复检确认未恢复（触发人工介入升级消息）
      suppressed 命中冷却窗口或同规则同主机闭环进行中，未触发自愈（防自愈风暴）
    """
    STATUS_RUNNING = 'running'
    STATUS_RECOVERED = 'recovered'
    STATUS_FAILED = 'failed'
    STATUS_SUPPRESSED = 'suppressed'

    STATUS_CHOICES = (
        (STATUS_RUNNING, '闭环执行中'),
        (STATUS_RECOVERED, '自愈成功'),
        (STATUS_FAILED, '自愈失败'),
        (STATUS_SUPPRESSED, '冷却抑制'),
    )

    rule = models.ForeignKey(HealRule, models.PROTECT, related_name='events')
    host = models.CharField(max_length=100, default='')
    # 告警来源：alarm.Alarm 记录 id（post_save signal 挂点）与检测任务名。
    # 用 IntegerField 而非跨 app 外键：Alarm 表可能被运维清空，置空引用不悬空
    alert_id = models.IntegerField(null=True)
    alert_name = models.CharField(max_length=100, default='')
    alert_target = models.CharField(max_length=255, default='')
    alert_message = models.TextField(null=True)
    before_status = models.CharField(max_length=20, default='')
    after_status = models.CharField(max_length=20, null=True)
    job = models.ForeignKey(AnsibleJob, models.SET_NULL, null=True, related_name='+')
    status = models.CharField(max_length=20, default=STATUS_RUNNING, choices=STATUS_CHOICES)
    detail = models.TextField(default='{}')
    duration_ms = models.IntegerField(null=True)
    created_at = models.CharField(max_length=20, default=human_datetime)
    finished_at = models.CharField(max_length=20, null=True)

    def to_view(self):
        tmp = self.to_dict()
        tmp['detail'] = json.loads(self.detail)
        tmp['rule_name'] = self.rule.name if self.rule_id else '-'
        tmp['job_id'] = self.job_id
        return tmp

    def __repr__(self):
        return '<HealEvent %r>' % self.id

    class Meta:
        db_table = 'heal_events'
        ordering = ('-id',)
