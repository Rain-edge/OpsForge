# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎 API
from django.views.generic import View
from django.core.paginator import Paginator
from django_redis import get_redis_connection
from libs import json_response, JsonParser, Argument, auth
from apps.ansible_ops.models import AnsibleJob
from apps.ansible_ops.executor import submit_job, LOG_DIR, MAX_JOBS, RUNNING_KEY
from apps.account.utils import has_host_perm
from apps.host.models import Host
import json
import os
import re


class ExecView(View):
    @auth('ansible_ops.exec')
    def post(self, request):
        form, error = JsonParser(
            Argument('host_ids', type=list, filter=lambda x: len(x), help='请选择执行主机'),
            Argument('playbook_id', type=int, required=False),
            Argument('raw_module', type=dict, required=False),
            Argument('extra_vars', type=dict, default={}),
        ).parse(request.body)
        if error is None:
            # 先校验元素类型再去重：dict.fromkeys 对不可哈希元素会 TypeError
            if not all(type(x) is int for x in form.host_ids):
                return json_response(error='host_ids 必须是整数数组')
            host_ids = list(dict.fromkeys(form.host_ids))
            if form.playbook_id and form.raw_module:
                return json_response(error='playbook_id 与 raw_module 只能二选一')
            if not form.playbook_id and not form.raw_module:
                return json_response(error='playbook_id 与 raw_module 必须提供其一')
            if form.playbook_id:
                return json_response(error='Playbook 库未上线（提供），当前仅支持 raw 模块')
            raw = form.raw_module
            if not raw.get('name') or not isinstance(raw.get('name'), str):
                return json_response(error='raw_module.name 不能为空')
            if not re.fullmatch(r'[A-Za-z0-9_.]+', raw['name']):
                return json_response(error='raw_module.name 仅允许字母数字下划线与点')
            if raw.get('args') is not None and not isinstance(raw.get('args'), str):
                return json_response(error='raw_module.args 必须是字符串')
            if not has_host_perm(request.user, host_ids):
                return json_response(error='无权访问主机，请联系管理员')
            if Host.objects.filter(id__in=host_ids).count() != len(host_ids):
                return json_response(error='存在无效主机，请刷新后重试')
            # 跨进程并发名额：incr 占用，提交链路任何失败即释放；成功后由
            # executor.run_job 的 finally 释放（见 executor.RUNNING_KEY 注释）
            rds = get_redis_connection()
            if rds.incr(RUNNING_KEY) > MAX_JOBS:
                rds.decr(RUNNING_KEY)
                return json_response(error=f'执行并发已满（上限 {MAX_JOBS}），请稍后再试')
            try:
                job = AnsibleJob.objects.create(
                    user=request.user,
                    host_ids=json.dumps(host_ids),
                    raw_module=json.dumps(raw),
                    extra_vars=json.dumps(form.extra_vars),
                )
                os.makedirs(LOG_DIR, exist_ok=True)
                submit_job(job.id)
            except Exception:
                rds.decr(RUNNING_KEY)
                raise
            response = json_response({'job_id': job.id})
            response.status_code = 202
            return response
        return json_response(error=error)


class JobListView(View):
    @auth('ansible_ops.view')
    def get(self, request):
        queryset = AnsibleJob.objects.filter(user=request.user)
        try:
            page_size = max(int(request.GET.get('page_size', 20)), 1)
            page_no = max(int(request.GET.get('page', 1)), 1)
        except (TypeError, ValueError):
            page_size, page_no = 20, 1
        page = Paginator(queryset, page_size)
        records = page.get_page(page_no)
        return json_response({
            'total': page.count,
            'jobs': [x.to_view() for x in records.object_list],
        })


class JobDetailView(View):
    @auth('ansible_ops.view')
    def get(self, request, pk):
        job = AnsibleJob.objects.filter(pk=pk, user=request.user).first()
        if not job:
            return json_response(error='任务不存在')
        data = job.to_view()
        data['user'] = job.user.nickname
        data['hosts'] = [
            {'id': x.id, 'name': x.name, 'hostname': x.hostname}
            for x in Host.objects.filter(id__in=json.loads(job.host_ids))
        ]
        return json_response(data)
