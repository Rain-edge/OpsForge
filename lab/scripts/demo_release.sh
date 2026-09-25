#!/bin/bash
# demo_release.sh — 节点实验环境验收标准 4：平台原生"应用发布"演示（幂等，可重复执行）
#
# ⚠️ 按【平台容器内实际运行的 v4.0.1 代码】编写（与工作区 tip 的接口有差异）。
# 链路：建环境 → 建应用 → 建发布配置（常规发布，仓库 = 平台容器内本地 bare 仓库
#   file:///data/repos/demo-app.git，见 RUNBOOK）→ 建发布申请 → 执行发布 → 轮询结果。
# 幂等性：环境/应用/发布配置先查后建（重复执行跳过）；发布申请每次新建一条
#   （发布历史本身即追加性质，重复执行 = 再发一次同版本，节点内容不变）。
#
# 用法：bash lab/scripts/demo_release.sh [BASE_URL]   # 默认 http://localhost
# 依赖：curl；bare 仓库需已存在（RUNBOOK「节点实验环境」章节有创建命令）

set -euo pipefail

BASE_URL="${1:-http://localhost}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# .env 可能被 Windows 记事本存成 CRLF，先剥掉 \r 再 source（compose 容忍 \r，bash 不容忍）
tr -d '\r' < "${SCRIPT_DIR}/../nodes/.env" > /tmp/opsforge.env
# shellcheck source=/dev/null
source /tmp/opsforge.env && rm -f /tmp/opsforge.env

SPUG_USER="${SPUG_USER:?SPUG_USER missing, check lab/nodes/.env}"
SPUG_PASS="${SPUG_PASS:?SPUG_PASS missing, check lab/nodes/.env}"

API() { curl -s -X "$1" "${BASE_URL}$2" -H 'Content-Type: application/json' -H "x-token: ${TOKEN}" ${3:+-d "$3"}; }

# 1. 登录
TOKEN=$(curl -sSf -X POST "${BASE_URL}/api/account/login/" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${SPUG_USER}\",\"password\":\"${SPUG_PASS}\",\"type\":\"default\"}" \
    | sed -E 's/.*"access_token":[ ]*"([a-f0-9]{32})".*/\1/') || true
if [ "${#TOKEN}" -ne 32 ]; then
    echo "ERROR: login failed (平台未启动或凭据错误): ${BASE_URL}" >&2
    exit 1
fi
echo "== 登录成功 =="

# 2. 建Demo Env（幂等）
ENV_ID=$(API GET "/api/config/environment/" | grep -oE '\{"id": ?[0-9]+, ?"name": ?"Demo Env"[^}]*\}' | head -1 | grep -oE '[0-9]+' | head -1) || true
if [ -z "${ENV_ID}" ]; then
    RESP=$(API POST "/api/config/environment/" '{"name":"Demo Env","key":"demo","desc":"节点实验环境 release demo"}') || true
    ENV_ID=$(API GET "/api/config/environment/" | grep -oE '\{"id": ?[0-9]+, ?"name": ?"Demo Env"' | head -1 | grep -oE '[0-9]+' | head -1) || true
    [ -n "${ENV_ID}" ] || { echo "ERROR: 创建环境失败: ${RESP:0:200}" >&2; exit 1; }
    echo "[ENV]  Demo Env已创建 (id=${ENV_ID})"
else
    echo "[ENV]  Demo Env已存在 (id=${ENV_ID})"
fi

# 3. 建应用（幂等，key 唯一）
APP_ID=$(API GET "/api/app/" | grep -oE '\{"id": ?[0-9]+, ?"name": ?"Demo Static Site"' | head -1 | grep -oE '[0-9]+' | head -1) || true
if [ -z "${APP_ID}" ]; then
    RESP=$(API POST "/api/app/" '{"name":"Demo Static Site","key":"demo_app","desc":"节点实验环境 release demo app"}') || true
    APP_ID=$(API GET "/api/app/" | grep -oE '\{"id": ?[0-9]+, ?"name": ?"Demo Static Site"' | head -1 | grep -oE '[0-9]+' | head -1) || true
    [ -n "${APP_ID}" ] || { echo "ERROR: 创建应用失败: ${RESP:0:200}" >&2; exit 1; }
    echo "[APP]  应用已创建 (id=${APP_ID})"
else
    echo "[APP]  应用已存在 (id=${APP_ID})"
fi

# 4. web 组主机 ID（web-01 / web-02）
HOSTS_JSON=$(API GET "/api/host/") || true
W1=$(grep -oE '\{"id": ?[0-9]+, ?"name": ?"web-01"' <<<"${HOSTS_JSON}" | grep -oE '[0-9]+' | head -1) || true
W2=$(grep -oE '\{"id": ?[0-9]+, ?"name": ?"web-02"' <<<"${HOSTS_JSON}" | grep -oE '[0-9]+' | head -1) || true
if [ -z "${W1}" ] || [ -z "${W2}" ]; then
    echo "ERROR: web 组主机未纳管，请先执行 register_hosts.sh" >&2
    exit 1
fi
echo "[HOST] web 组主机: web-01=${W1}, web-02=${W2}"

# 5. 建发布配置（幂等，常规发布 extend=1）
DEPLOY_ID=$(API GET "/api/app/deploy/?app_id=${APP_ID}" | grep -oE '"id": ?[0-9]+' | head -1 | grep -oE '[0-9]+' | head -1) || true
if [ -z "${DEPLOY_ID}" ]; then
    RESP=$(API POST "/api/app/deploy/" "{
        \"app_id\": ${APP_ID}, \"env_id\": ${ENV_ID}, \"host_ids\": [${W1}, ${W2}],
        \"rst_notify\": {\"mode\": \"0\"}, \"extend\": \"1\", \"is_audit\": false,
        \"git_repo\": \"file:///data/repos/demo-app.git\",
        \"dst_dir\": \"/home/ops/demo\", \"dst_repo\": \"/home/ops/releases/demo\",
        \"versions\": 5, \"filter_rule\": {\"type\": \"exclude\", \"data\": \"\"}}") || true
    DEPLOY_ID=$(API GET "/api/app/deploy/?app_id=${APP_ID}" | grep -oE '"id": ?[0-9]+' | head -1 | grep -oE '[0-9]+' | head -1) || true
    [ -n "${DEPLOY_ID}" ] || { echo "ERROR: 创建发布配置失败: ${RESP:0:200}" >&2; exit 1; }
    echo "[DEP]  发布配置已创建 (id=${DEPLOY_ID})"
else
    echo "[DEP]  发布配置已存在 (id=${DEPLOY_ID})"
fi

# 6. 取 main 分支最新 commit（接口会先 clone/fetch 仓库）
VERSIONS=$(API GET "/api/app/deploy/${DEPLOY_ID}/versions/") || true
SHA=$(grep -oE '"main": ?\[\{"id": ?"[a-f0-9]{40}"' <<<"${VERSIONS}" | grep -oE '[a-f0-9]{40}' | head -1) || true
if [ -z "${SHA}" ]; then
    echo "ERROR: 获取版本失败: ${VERSIONS:0:200}" >&2
    exit 1
fi
echo "[VER]  main@${SHA:0:6}"

# 7. 建发布申请（成功响应不含 id，从申请列表按 name 反查；name 含唯一 sha）
R_NAME="Release main@${SHA:0:6}"
RESP=$(API POST "/api/deploy/request/ext1/" "{
    \"deploy_id\": ${DEPLOY_ID}, \"name\": \"${R_NAME}\",
    \"extra\": [\"branch\", \"main\", \"${SHA}\"], \"host_ids\": [${W1}, ${W2}],
    \"type\": \"1\", \"desc\": \"节点实验环境 acceptance demo\"}")
if grep -q '"error": null' <<<"${RESP}" || grep -q '"error": ""' <<<"${RESP}"; then
    # 申请列表按 -id 排序，首个元素即最新创建的申请
    R_ID=$(API GET "/api/deploy/request/" \
        | grep -oE '\[\{"id": ?[0-9]+' | head -1 | grep -oE '[0-9]+' | head -1) || true
else
    echo "ERROR: 创建发布申请失败: ${RESP:0:200}" >&2
    exit 1
fi
if [ -z "${R_ID}" ]; then
    echo "ERROR: 发布申请已创建但未查到 id: ${RESP:0:120}" >&2
    exit 1
fi
echo "[REQ]  发布申请已创建 (id=${R_ID})"

# 8. 执行发布（全量）并轮询状态（DeployRequest.STATUS：'3'=发布成功，'-1'驳回，'-3'异常）
RESP=$(API POST "/api/deploy/request/${R_ID}/" '{"mode": "all"}') || true
if grep -q '"error": ""' <<<"${RESP}" || grep -q '"error": null' <<<"${RESP}"; then
    echo "[EXEC] 发布已触发，轮询结果 ..."
else
    echo "ERROR: 发布触发失败: ${RESP:0:200}" >&2
    exit 1
fi
for i in $(seq 1 30); do
    sleep 2
    ST=$(API GET "/api/deploy/request/${R_ID}/" | grep -oE '"status": ?"[0-9-]+"' | head -1 | grep -oE '[0-9-]+' | head -1) || true
    case "${ST}" in
        3) echo "== 发布成功（申请 id=${R_ID}）=="; exit 0 ;;
        -1|-3) echo "ERROR: 发布失败（status=${ST}），详情见平台 UI 应用发布页" >&2; exit 1 ;;
    esac
done
echo "ERROR: 轮询超时（60s），status=${ST:-unknown}" >&2
exit 1
