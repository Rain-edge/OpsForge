#!/bin/bash
# import_playbooks.sh — Playbook 库双轨导入：仓库源 lab/playbooks/ → 平台 Playbook 库 + 卷内辅助目录
#
# 幂等，可重复执行：
#   1. docker cp 仓库源到容器临时目录（供 import.py 读取）
#   2. docker cp group_vars/host_vars/templates 到 REPOS_DIR/ansible_ops/playbooks/
#      （ansible-playbook 自动加载 playbook 同目录的这三个子目录，覆盖合并、不删除孤儿）
#   3. import.py 经平台 API 灌 4 个主 playbook（自带 syntax-check + git 版本化提交）；
#      同名已存在默认跳过，--force 时更新覆盖
#
# 用法：bash lab/scripts/import_playbooks.sh [--force]
# 依赖：平台容器（默认名 spug）在运行；凭据经 lab/nodes/.env（SPUG_USER/SPUG_PASS）

set -euo pipefail

# Git Bash (MSYS) 会把 /tmp、/data 开头的参数转换成 Windows 路径再传给 docker.exe，
# 导致容器内路径被改写（实测 python3 找不到文件、rm 删错位置）；排除这两个前缀，
# 而 docker cp 的宿主源路径 /c/Users/... 不受影响仍正常转换（Linux 下该变量被忽略）
export MSYS2_ARG_CONV_EXCL="/tmp;/data"

FORCE="${1:-}"
CONTAINER="${CONTAINER:-spug}"
REPOS_PLAYBOOKS="/data/repos/ansible_ops/playbooks"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/../playbooks"

tr -d '\r' < "${SCRIPT_DIR}/../nodes/.env" > /tmp/opsforge.env
# shellcheck source=/dev/null
source /tmp/opsforge.env && rm -f /tmp/opsforge.env
: "${SPUG_USER:?SPUG_USER missing, check lab/nodes/.env}"
: "${SPUG_PASS:?SPUG_PASS missing, check lab/nodes/.env}"

# 失败兜底：清临时目录与凭据文件（凭据经 docker exec -e 传递执行期可被宿主
# ps/docker inspect 观测，实验环境已接受的局限，见 import.py 头注）
trap 'rm -f /tmp/opsforge.env; docker exec "${CONTAINER:-spug}" rm -rf /tmp/playbooks-src 2>/dev/null || true' EXIT

# 1. 源文件整体拷入容器临时目录（主 yml + 辅助目录 + import.py）
#    注意：Windows 下 docker cp 目录合并（src/.）覆盖不可靠（实测静默保留旧文件），
#    一律先删目标再整目录拷贝，保证「仓库源为准」
docker exec "${CONTAINER}" rm -rf /tmp/playbooks-src
docker cp "${SRC_DIR}" "${CONTAINER}:/tmp/playbooks-src"
echo "[CP  ] 源文件已拷入 ${CONTAINER}:/tmp/playbooks-src/"

# 2. 辅助目录重建进 REPOS 卷（以仓库源为准，删除重建防旧文件残留）
for d in group_vars host_vars templates; do
    docker exec "${CONTAINER}" rm -rf "${REPOS_PLAYBOOKS}/${d}"
    docker cp "${SRC_DIR}/${d}" "${CONTAINER}:${REPOS_PLAYBOOKS}/${d}"
    echo "[CP  ] ${d}/ -> ${REPOS_PLAYBOOKS}/${d}/"
done

# 3. 主 playbook 经平台 API 入库（syntax-check + git 版本化在平台侧完成）
FORCE_ARG=""
[ "${FORCE}" = "--force" ] && FORCE_ARG="--force"
docker exec \
    -e SPUG_USER="${SPUG_USER}" -e SPUG_PASS="${SPUG_PASS}" \
    "${CONTAINER}" python3 /tmp/playbooks-src/import.py ${FORCE_ARG}

docker exec "${CONTAINER}" rm -rf /tmp/playbooks-src
echo "== 双轨导入完成 =="
