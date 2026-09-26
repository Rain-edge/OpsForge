#!/bin/sh
# OpsForge：先备好 SSH 再启动 FRR。
# 口令从 env 注入（compose env_file ../nodes/.env，口令不进仓库），
# 缺失直接拒绝启动（同节点编排的 OPS_PASSWORD:? 口径）。
set -e

echo "ops:${OPS_PASSWORD:?OPS_PASSWORD is required}" | chpasswd
ssh-keygen -A
mkdir -p /run/sshd
/usr/sbin/sshd

# 回到官方入口（tini 信号转发 + watchfrr 守护 zebra/staticd 等）
exec /sbin/tini -- /usr/lib/frr/docker-start
