#!/bin/bash
# register_hosts.sh — 节点实验环境平台纳管 7 台实验节点（幂等，可重复执行）
#
# ⚠️ 本脚本按【平台容器内实际运行的 v4.0.1 代码】编写（官方镜像首启从 gitee 拉取，
#    与本仓库工作区 tip 代码不联动，两者 host 接口有实质差异：v4.0.1 用独立分组表
#    group_ids，无 zone 字段）。
# 原理：POST /api/host/ 带 password 时，平台先用密码登录一次并把平台公钥追加到
#    节点 ~/.ssh/authorized_keys（容器内 views._do_host_verify），之后全部走私钥认证。
#    ops 口令经 lab/nodes/.env 注入，不硬编码进本脚本。
#
# 用法：bash lab/scripts/register_hosts.sh [BASE_URL]   # 默认 http://localhost
# 依赖：curl（Git Bash 自带）

set -euo pipefail

BASE_URL="${1:-http://localhost}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# .env 可能被 Windows 记事本存成 CRLF，先剥掉 \r 再 source（compose 容忍 \r，bash 不容忍）
tr -d '\r' < "${SCRIPT_DIR}/../nodes/.env" > /tmp/opsforge.env
# shellcheck source=/dev/null
source /tmp/opsforge.env && rm -f /tmp/opsforge.env

SPUG_USER="${SPUG_USER:?SPUG_USER missing, check lab/nodes/.env}"
SPUG_PASS="${SPUG_PASS:?SPUG_PASS missing, check lab/nodes/.env}"

# 节点清单与节点实验环境约定表格一致：name 分组 ssh端口（容器网络内统一 22）
NODES=(
    "web-01 web 22"
    "web-02 web 22"
    "db-01 db 22"
    "db-02 db 22"
    "cache-01 cache 22"
    "cache-02 cache 22"
    "cache-03 cache 22"
)

# 1. 登录拿 token（body 必须带 type 字段，RUNBOOK §6 坑 3；认证头为 x-token；
#    平台 json.dumps 默认带空格，token 提取兼容 ": " 与 ":"）
TOKEN=$(curl -sSf -X POST "${BASE_URL}/api/account/login/" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${SPUG_USER}\",\"password\":\"${SPUG_PASS}\",\"type\":\"default\"}" \
    | sed -E 's/.*"access_token":[ ]*"([a-f0-9]{32})".*/\1/') || true
if [ "${#TOKEN}" -ne 32 ]; then
    echo "ERROR: login failed (平台未启动或凭据错误): ${BASE_URL}" >&2
    exit 1
fi
echo "== 登录成功，token: ${TOKEN:0:8}... =="

# 2. 建分组（幂等）：GET 返回 groups 字典 {"<id>": "<名称/路径>"}，按值精确匹配找 ID
get_group_id() {
    curl -s "${BASE_URL}/api/host/group/" -H "x-token: ${TOKEN}" \
        | grep -oE "\"[0-9]+\": *\"${1}\"" | head -1 | grep -oE '[0-9]+' || true
}

declare -A GROUP_IDS
for g in web db cache; do
    GID=$(get_group_id "${g}")
    if [ -z "${GID}" ]; then
        curl -s -X POST "${BASE_URL}/api/host/group/" \
            -H 'Content-Type: application/json' -H "x-token: ${TOKEN}" \
            -d "{\"name\":\"${g}\"}" > /dev/null
        GID=$(get_group_id "${g}")
        echo "[GRP]  分组 ${g} 已创建 (id=${GID})"
    else
        echo "[GRP]  分组 ${g} 已存在 (id=${GID})"
    fi
    GROUP_IDS["${g}"]="${GID}"
done

# 3. 逐台纳管
FAIL=0
for entry in "${NODES[@]}"; do
    read -r NAME GNAME PORT <<<"$entry"
    GID="${GROUP_IDS[${GNAME}]}"
    RESP=$(curl -s -X POST "${BASE_URL}/api/host/" \
        -H 'Content-Type: application/json' -H "x-token: ${TOKEN}" \
        -d "{\"group_ids\":[${GID}],\"name\":\"${NAME}\",\"hostname\":\"${NAME}\",\"port\":${PORT},\"username\":\"ops\",\"password\":\"${OPS_PASSWORD}\"}")

    # 成功：{"data": {..."id":N...}, "error": ""}；已存在：{"error":"已存在的主机名称【x】"}
    if grep -q '已存在的主机名称' <<<"${RESP}"; then
        echo "[SKIP] ${NAME} (${GNAME}) 已纳管，跳过"
    elif { grep -q '"error": ""' <<<"${RESP}" || grep -q '"error": null' <<<"${RESP}"; } \
         && grep -q '"id"' <<<"${RESP}"; then
        echo "[OK]   ${NAME} (${GNAME}) 纳管成功，平台公钥已分发"
    else
        echo "[FAIL] ${NAME} (${GNAME}): ${RESP}" >&2
        FAIL=$((FAIL + 1))
    fi
done

if [ "${FAIL}" -gt 0 ]; then
    echo "== ${FAIL} 台纳管失败 ==" >&2
    exit 1
fi
echo "== 7/7 完成 =="
