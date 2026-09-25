# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：job 实时输出 WebSocket 消费者（路由 ws/ansible_ops/jobs/<id>/，
# nginx 侧为 /api/ws/ansible_ops/jobs/<id>/，x-token 鉴权沿用 consumer.utils.BaseConsumer）
import json

from asgiref.sync import async_to_sync
from django.db import close_old_connections
from pathlib import Path

from apps.ansible_ops.executor import group_name
from apps.ansible_ops.models import AnsibleJob
from consumer.utils import BaseConsumer


class JobConsumer(BaseConsumer):
    """连接即入组 ansible-job-{id}。先补发日志文件已有内容（中途进页面可看全量），
    再接收 executor 的实时推送；对已终态任务补发 done 后即关闭连接。

    已知可接受偏差：①入组与读文件之间存在毫秒级竞窗，极端时序下某行可能
    既出现在补发又出现在实时流（重复一次），不影响可读性，不做去重；
    ②同理终态 done 可能与补发尾部的 done 双发一次，前端按状态幂等渲染。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # BaseConsumer 鉴权失败路径不会调 init，disconnect 仍会被回调，
        # _joined 必须在此预置，否则拒连场景抛 AttributeError
        self._joined = False

    def init(self):
        self.job_id = self.scope['url_route']['kwargs']['id']
        close_old_connections()
        job = AnsibleJob.objects.filter(pk=self.job_id, user=self.user).first()
        if not job:
            return self.close_with_message('任务不存在或无权查看')
        async_to_sync(self.channel_layer.group_add)(group_name(self.job_id), self.channel_name)
        self._joined = True
        self._replay(job)

    def _replay(self, job):
        if job.log_path and Path(job.log_path).exists():
            with Path(job.log_path).open(encoding='utf-8', errors='replace') as f:
                for line in f:
                    self.send(text_data=json.dumps(
                        {'type': 'log', 'line': line.rstrip('\n'), 'ts': None}))
        # 补发期间任务可能刚好结束，终态以再次查库为准
        job.refresh_from_db()
        if job.finished_at:
            self.send(text_data=json.dumps(
                {'type': 'done', 'status': job.status, 'summary': json.loads(job.summary)}))
            self.close()

    # group_send 事件按 type 分派：log/done 原样转发（即约定线上格式）
    def log(self, event):
        self.send(text_data=json.dumps(event))

    def done(self, event):
        self.send(text_data=json.dumps(event))

    def disconnect(self, code):
        if self._joined:
            async_to_sync(self.channel_layer.group_discard)(
                group_name(self.job_id), self.channel_name)
