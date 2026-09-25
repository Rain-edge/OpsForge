# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：Ansible 执行引擎 API
from django.urls import path

from apps.ansible_ops.views import ExecView, JobListView, JobDetailView

urlpatterns = [
    path('exec/', ExecView.as_view()),
    path('jobs/', JobListView.as_view()),
    path('jobs/<int:pk>/', JobDetailView.as_view()),
]
