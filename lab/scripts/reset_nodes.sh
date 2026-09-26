#!/bin/bash
# Playbook 库临时工具：重置指定节点为裸机并重新纳管（重发平台公钥 + 清旧指纹）
# 用法：bash reset_nodes.sh <name>:<host_id>:<group_id> ...
#   例：bash reset_nodes.sh db-01:3:3 db-02:4:3
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tr -d '\r' < "${ROOT}/lab/nodes/.env" > /tmp/opsforge.env
source /tmp/opsforge.env && rm -f /tmp/opsforge.env
: "${SPUG_USER:?}"; : "${SPUG_PASS:?}"; : "${OPS_PASSWORD:?}"

NAMES=()
for spec in "$@"; do NAMES+=("${spec%%:*}"); done
echo "[1/3] force-recreate 节点: ${NAMES[*]}"
docker compose -f "${ROOT}/lab/nodes/docker-compose.yml" up -d --force-recreate "${NAMES[@]}"
sleep 10

echo "[2/3] 登录平台"
TOKEN=$(curl -sSf -X POST "http://localhost/api/account/login/" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${SPUG_USER}\",\"password\":\"${SPUG_PASS}\",\"type\":\"default\"}" \
    | sed -E 's/.*"access_token":[ ]*"([a-f0-9]{32})".*/\1/')
[ "${#TOKEN}" -eq 32 ] || { echo "登录失败"; exit 1; }

for spec in "$@"; do
    IFS=: read -r NAME HID GID <<<"$spec"
    docker exec spug bash -c "ssh-keygen -f /root/.ssh/known_hosts -R ${NAME} >/dev/null 2>&1; true"
    echo "[3/3] 重新验证 ${NAME} (id=${HID} group=${GID})"
    curl -s -X POST "http://localhost/api/host/" -H 'Content-Type: application/json' -H "x-token: ${TOKEN}" \
        -d "{\"id\":${HID},\"group_ids\":[${GID}],\"name\":\"${NAME}\",\"hostname\":\"${NAME}\",\"port\":22,\"username\":\"ops\",\"password\":\"${OPS_PASSWORD}\"}" \
        | grep -q '"error": ""' && echo "  OK" || { echo "  FAIL"; exit 1; }
done
echo "== 重置完成 =="
