#!/bin/bash
# setup_heal_demo.sh — 告警自愈自愈演示环境一键初始化（幂等）
#
# 补上「down -v 从零重建 → 自愈演示可用」的缺口：原链路里检测任务与自愈规则
# 是手工建的，重建后无法一键恢复（RUNBOOK §14 只有手工核对清单）。
#
# 做五件事（全部可重复执行）：
#   1. 拷 setup_heal_demo.py / mock_webhook.py 进容器
#   2. 若 heal-nginx Playbook 缺失 → 调 import_playbooks.sh 导入（前置依赖）
#   3. mock webhook 接收端未在跑则拉起（:9999，预览页 http://127.0.0.1:8002/）
#   4. 容器内执行 setup_heal_demo.py：建检测任务 + 自愈规则 + 写 heal_webhook.env
#      + 写 storage/net_devices.env（网络设备口令，缺它 NET 备份会静默失败）
#   5. 缺 OPS_PASSWORD 时给出明确警告（网络备份不可用）
#
# 用法：bash lab/scripts/setup_heal_demo.sh [--force]
#   依赖：平台容器运行中、节点已纳管（register_hosts.sh）
set -euo pipefail

# Git Bash(MSYS) 会把 /tmp、/data 开头参数转换成 Windows 路径，容器内路径必须排除
export MSYS2_ARG_CONV_EXCL="/tmp;/data"

# 宿主→容器拷贝的源路径反向归一（本机 MSYS 关闭参数转换时 /c/... 会原样交给
# docker.exe 报 "GetFileAttributesEx C:\c:"；Linux 无 cygpath，原样透传）
to_win_path() {
    if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

FORCE="${1:-}"
CONTAINER="${CONTAINER:-spug}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

tr -d '\r' < "${SCRIPT_DIR}/../nodes/.env" > /tmp/opsforge.env
# shellcheck source=/dev/null
source /tmp/opsforge.env && rm -f /tmp/opsforge.env
: "${SPUG_USER:?SPUG_USER missing, check lab/nodes/.env}"
: "${SPUG_PASS:?SPUG_PASS missing, check lab/nodes/.env}"

# 0. 前置：heal-nginx 是否已入库（缺则先导入 Playbook 库）
if ! docker exec "${CONTAINER}" python3 -c "
import os, sys
sys.path.insert(0, '/data/spug/spug_api')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spug.settings')
import django; django.setup()
from apps.ansible_ops.models import Playbook
sys.exit(0 if Playbook.objects.filter(name='heal-nginx').exists() else 1)" 2>/dev/null; then
    echo "[PREP ] heal-nginx 未入库，先执行 import_playbooks.sh"
    bash "${SCRIPT_DIR}/import_playbooks.sh"
fi

# 1. 拷贝脚本
docker cp "$(to_win_path "${SCRIPT_DIR}/setup_heal_demo.py")" "${CONTAINER}:/tmp/setup_heal_demo.py" >/dev/null
docker cp "$(to_win_path "${SCRIPT_DIR}/mock_webhook.py")" "${CONTAINER}:/tmp/mock_webhook.py" >/dev/null
echo "[CP   ] setup_heal_demo.py / mock_webhook.py -> ${CONTAINER}:/tmp/"

# 2. mock 接收端（未在跑才拉起，重复执行不会起第二个实例）
if docker exec "${CONTAINER}" python3 -c "
import urllib.request, sys
try:
    urllib.request.urlopen('http://127.0.0.1:9999/', timeout=3)
except Exception:
    sys.exit(1)" 2>/dev/null; then
    echo "[SKIP ] mock webhook 已在运行（:9999）"
else
    docker exec -d "${CONTAINER}" python3 /tmp/mock_webhook.py
    sleep 1
    echo "[START] mock webhook -> :9999（宿主预览 http://127.0.0.1:8002/）"
fi

# 3. 建检测任务 + 自愈规则 + webhook 配置
docker exec -e SPUG_USER="${SPUG_USER}" -e SPUG_PASS="${SPUG_PASS}" -e OPS_PASSWORD="${OPS_PASSWORD}" \
    "${CONTAINER}" python3 /tmp/setup_heal_demo.py ${FORCE}

docker exec "${CONTAINER}" rm -f /tmp/setup_heal_demo.py
echo "== setup_heal_demo 完成 =="
