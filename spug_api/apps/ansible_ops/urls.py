# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎 API；实时回传增加任务终止；
# Playbook 管理 增加 Playbook 管理与版本化路由；巡检报表增加巡检报告路由
from django.urls import path

from apps.ansible_ops.views import (
    ExecView, JobListView, JobDetailView, JobKillView,
    PlaybookListView, PlaybookDetailView, PlaybookVersionsView, PlaybookRollbackView,
    InspectionListView, InspectionRunView, InspectionDetailView, InspectionFileView)

urlpatterns = [
    path('exec/', ExecView.as_view()),
    path('jobs/', JobListView.as_view()),
    path('jobs/<int:pk>/kill/', JobKillView.as_view()),
    path('jobs/<int:pk>/', JobDetailView.as_view()),
    path('playbooks/', PlaybookListView.as_view()),
    path('playbooks/<int:pk>/versions/', PlaybookVersionsView.as_view()),
    path('playbooks/<int:pk>/rollback/', PlaybookRollbackView.as_view()),
    path('playbooks/<int:pk>/', PlaybookDetailView.as_view()),
    # 约定路由：手动触发 POST /api/ansible_ops/inspection/run/（单数）；列表/详情复数
    path('inspection/run/', InspectionRunView.as_view()),
    path('inspections/', InspectionListView.as_view()),
    path('inspections/<int:pk>/', InspectionDetailView.as_view()),
    path('inspections/<int:pk>/files/<str:kind>/', InspectionFileView.as_view()),
]
