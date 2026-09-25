# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎 API；实时回传增加任务终止；
# Playbook 管理 增加 Playbook 管理与版本化 API，并放开 exec 的 playbook 执行
from django.views.generic import View
from django.core.paginator import Paginator
from django_redis import get_redis_connection
from libs import json_response, JsonParser, Argument, auth, human_datetime
from apps.ansible_ops.models import AnsibleJob, Playbook
from apps.ansible_ops.executor import (
    submit_job, LOG_DIR, MAX_JOBS, RUNNING_KEY, PID_KEY, KILL_KEY)
from apps.ansible_ops.repo import (
    normalize_content, syntax_check, write_and_commit, delete_and_commit,
    get_content_at, list_versions, playbook_file)
from apps.account.utils import has_host_perm
from apps.host.models import Host
import json
import os
import re
import signal


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
            if form.playbook_id and not Playbook.objects.filter(pk=form.playbook_id).exists():
                return json_response(error='Playbook 不存在，请刷新后重试')
            raw = form.raw_module
            if raw:
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
                    playbook_id=form.playbook_id or None,
                    raw_module=json.dumps(raw) if raw else None,
                    extra_vars=json.dumps(form.extra_vars),
                )
                os.makedirs(LOG_DIR, exist_ok=True)
                submit_job(job.id)
            except Exception:
                rds.decr(RUNNING_KEY)
                raise
            # 约定原定 202 Accepted，实际返回 200：spug 前端统一 http 封装将非 200
            # 一律按失败弹出（libs/http.js handleResponse），202 会造成前端误报。
            # 偏差已记录待确认（问题 2）
            return json_response({'job_id': job.id})
        return json_response(error=error)


class JobKillView(View):
    """终止任务（约定：POST /api/ansible_ops/jobs/{id}/kill/ → job 置 failed(reason=killed)）。

    running 任务按 redis 中的 pid 终止进程组（跨 gunicorn worker 进程）；
    pending 任务在队列中尚未起进程，预置终止标记由 executor 启动时检查跳过。
    实际落终态的始终是 executor 线程（单点写库，避免双写竞态）。
    """
    @auth('ansible_ops.exec')
    def post(self, request, pk):
        job = AnsibleJob.objects.filter(pk=pk, user=request.user).first()
        if not job:
            return json_response(error='任务不存在')
        if job.status not in (AnsibleJob.STATUS_PENDING, AnsibleJob.STATUS_RUNNING):
            return json_response(error='任务已结束，无需终止')
        rds = get_redis_connection()
        if job.status == AnsibleJob.STATUS_PENDING:
            # 排队中无进程可杀：预置标记，executor 启动/开跑前检查并跳过。
            # TTL 需 ≥ 最坏在队等待（MAX_JOBS=2 × JOB_TIMEOUT）；executor 查标记
            # 与置 running 之间有竞窗，置标记后回读复核，已开跑则继续走进程组终止
            rds.set(KILL_KEY.format(pk), 'killed', ex=7200)
            job.refresh_from_db()
            if job.status != AnsibleJob.STATUS_RUNNING:
                return json_response()
        pid = rds.get(PID_KEY.format(pk))
        if not pid:
            return json_response(error='任务进程尚未就绪，请稍后重试')
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except ProcessLookupError:
            return json_response(error='任务进程已退出，请刷新查看结果')
        rds.set(KILL_KEY.format(pk), 'killed', ex=120)
        return json_response()


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


# ---- Playbook 管理与 git 版本化 ----
# 权限沿用 T1/T2 的两个字符串（view=读，exec=写），细粒度权限待权限体系范围
# 明确后再收口（范围边界：第一版不做权限改造，同实时回传 routes.js 注释）

# 模型 max_length=100，JsonParser 无长度限制，超长提交会 DataError 500
NAME_ARGUMENT = dict(help='请输入 Playbook 名称', filter=lambda x: len(x) <= 100)


def _serialize(pb):
    data = pb.to_dict()
    data['updated_by_name'] = pb.updated_by.nickname
    return data


class PlaybookListView(View):
    @auth('ansible_ops.view')
    def get(self, request):
        items = Playbook.objects.select_related('updated_by')
        return json_response([_serialize(x) for x in items])

    @auth('ansible_ops.exec')
    def post(self, request):
        form, error = JsonParser(
            Argument('name', **NAME_ARGUMENT),
            Argument('description', required=False, default=''),
            Argument('content', help='请输入 Playbook 内容'),
        ).parse(request.body)
        if error is None:
            if Playbook.objects.filter(name=form.name).exists():
                return json_response(error='该名称已存在')
            content = normalize_content(form.content)
            ok, output = syntax_check(content)
            if not ok:
                return json_response(error='语法校验未通过：\n' + (output or 'ansible 无输出'))
            pb = Playbook.objects.create(
                name=form.name, description=form.description,
                content=content, updated_by=request.user)
            try:
                write_and_commit(pb.id, content, f'opsforge: update {form.name}', request.user)
            except Exception as e:
                # 行已建但提交失败：清行并清文件，不留孤儿（多 worker 下 index.lock
                # 冲突等 git 错误在此显式回传，不打 500）
                pb.delete()
                playbook_file(pb.id).unlink(missing_ok=True)
                return json_response(error=f'git 提交失败：{e}')
            return json_response({'id': pb.id})
        return json_response(error=error)


class PlaybookDetailView(View):
    @auth('ansible_ops.view')
    def get(self, request, pk):
        pb = Playbook.objects.filter(pk=pk).first()
        if not pb:
            return json_response(error='Playbook 不存在')
        return json_response(_serialize(pb))

    @auth('ansible_ops.exec')
    def put(self, request, pk):
        pb = Playbook.objects.filter(pk=pk).first()
        if not pb:
            return json_response(error='Playbook 不存在')
        form, error = JsonParser(
            Argument('name', **NAME_ARGUMENT),
            Argument('description', required=False, default=''),
            Argument('content', help='请输入 Playbook 内容'),
        ).parse(request.body)
        if error is None:
            if form.name != pb.name and Playbook.objects.filter(name=form.name).exists():
                return json_response(error='该名称已存在')
            content = normalize_content(form.content)
            ok, output = syntax_check(content)
            if not ok:
                return json_response(error='语法校验未通过：\n' + (output or 'ansible 无输出'))
            # 内容无变化（仅改名称/描述）不产生空提交污染版本历史
            if content != pb.content:
                # 先提交 git 再更新字段：文件内容以版本库为准，失败时数据库不产生漂移
                try:
                    write_and_commit(pb.id, content, f'opsforge: update {form.name}', request.user)
                except Exception as e:
                    return json_response(error=f'git 提交失败：{e}')
            pb.name = form.name
            pb.description = form.description
            pb.content = content
            pb.updated_by = request.user
            pb.updated_at = human_datetime()
            pb.save()
            return json_response()
        return json_response(error=error)

    @auth('ansible_ops.exec')
    def delete(self, request, pk):
        pb = Playbook.objects.filter(pk=pk).first()
        if not pb:
            return json_response(error='Playbook 不存在')
        # 先落 git 再删行：版本库是事实源，行删除失败（外键保护等）可重试不损数据；
        # 文件与历史都不存在时 delete_and_commit 跳过 git，删除总能自愈
        delete_and_commit(pb.id, f'opsforge: delete {pb.name}', request.user)
        pb.delete()
        return json_response()


class PlaybookVersionsView(View):
    @auth('ansible_ops.view')
    def get(self, request, pk):
        if not Playbook.objects.filter(pk=pk).exists():
            return json_response(error='Playbook 不存在')
        return json_response(list_versions(pk))


class PlaybookRollbackView(View):
    """回滚（约定：POST body {commit}）：取目标 commit 内容重新入库为新提交，
    历史保持线性；content 字段同步更新。"""
    @auth('ansible_ops.exec')
    def post(self, request, pk):
        pb = Playbook.objects.filter(pk=pk).first()
        if not pb:
            return json_response(error='Playbook 不存在')
        form, error = JsonParser(
            Argument('commit', help='请指定回滚目标 commit')
        ).parse(request.body)
        if error is None:
            try:
                content = get_content_at(pb.id, form.commit)
            except ValueError as e:
                return json_response(error=str(e))
            ok, output = syntax_check(content)
            if not ok:
                # 历史版本入库前均过校验，此为防御（防仓库被绕过平台改动）
                return json_response(error='目标版本语法校验未通过，已放弃回滚：\n' + output)
            try:
                write_and_commit(
                    pb.id, content, f'opsforge: rollback {pb.name} to {str(form.commit)[:7]}',
                    request.user)
            except Exception as e:
                return json_response(error=f'git 提交失败：{e}')
            pb.content = content
            pb.updated_by = request.user
            pb.updated_at = human_datetime()
            pb.save()
            return json_response()
        return json_response(error=error)
