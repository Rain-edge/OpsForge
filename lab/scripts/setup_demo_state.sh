#!/bin/bash
# setup_demo_state.sh — 把实验室带到「演示态」（幂等，可重复执行）
#
# 补上从零重建后的演示态缺口（2026-10-02 实测，详见 setup_demo_state.py 头注）：
#   ① 发布演示的 bare 仓库 /data/repos/demo-app.git（手工建的，重建即丢）
#   ② 四册 Playbook 至少部署一轮（e2e 幂等用例与巡检基线依赖）
#   ③ 让巡检/探测有最新数据
#
# 用法：bash lab/scripts/setup_demo_state.sh [--skip-deploy]
#   依赖：平台运行、节点已纳管（register_hosts.sh）、Playbook 已导入（import_playbooks.sh）
set -euo pipefail

export MSYS2_ARG_CONV_EXCL="/tmp;/data"

to_win_path() {
    if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

CONTAINER="${CONTAINER:-spug}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARG="${1:-}"

tr -d '\r' < "${SCRIPT_DIR}/../nodes/.env" > /tmp/opsforge.env
# shellcheck source=/dev/null
source /tmp/opsforge.env && rm -f /tmp/opsforge.env
: "${SPUG_USER:?SPUG_USER missing, check lab/nodes/.env}"
: "${SPUG_PASS:?SPUG_PASS missing, check lab/nodes/.env}"

docker cp "$(to_win_path "${SCRIPT_DIR}/setup_demo_state.py")" "${CONTAINER}:/tmp/setup_demo_state.py" >/dev/null
docker exec -e SPUG_USER="${SPUG_USER}" -e SPUG_PASS="${SPUG_PASS}" \
    "${CONTAINER}" python3 /tmp/setup_demo_state.py ${ARG}
docker exec "${CONTAINER}" rm -f /tmp/setup_demo_state.py
echo "== setup_demo_state 完成 =="
