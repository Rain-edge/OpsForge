#!/bin/bash
# labnode entrypoint：口令注入 → host key 生成 → 交棒 systemd
set -e

# 统一实验用户口令来自环境变量（compose 经 .env 注入），缺失即拒绝启动，
# 保证口令不落在仓库内的任何镜像层或配置文件里
if [ -z "${OPS_PASSWORD}" ]; then
    echo "[labnode] OPS_PASSWORD is not set; refusing to start" >&2
    exit 1
fi
echo "ops:${OPS_PASSWORD}" | chpasswd

# 容器首启生成本机唯一 host key（镜像构建时已清理）
ssh-keygen -A

exec /sbin/init
