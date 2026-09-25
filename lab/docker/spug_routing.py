# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge note: 基于 v4.0.1 原件（consumer/routing.py），仅新增 ansible_ops job
# 推送路由，随镜像 COPY 覆盖，对比原件 diff 仅注册行（同 spug_urls.py 先例）。
from django.urls import path
from channels.routing import URLRouter
from consumer.consumers import *
from apps.ansible_ops.consumer import JobConsumer  # OpsForge: 实时回传

ws_router = URLRouter([
    path('ws/ssh/<int:id>/', SSHConsumer.as_asgi()),
    path('ws/subscribe/<str:token>/', PubSubConsumer.as_asgi()),
    path('ws/<str:module>/<str:token>/', ComConsumer.as_asgi()),
    path('ws/notify/', NotifyConsumer.as_asgi()),
    path('ws/ansible_ops/jobs/<int:id>/', JobConsumer.as_asgi()),  # OpsForge: 实时回传
])
