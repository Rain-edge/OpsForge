#!/bin/bash
# Playbook 库手工安装对比用（一次性脚本，experiments 记录命令与耗时依据）
# 模拟传统手工运维：逐台 docker exec（等价 ssh 进节点敲命令）搭 MariaDB 主从
set -e
T0=$(date +%s)
step() { echo "[T+$(printf '%3d' $(( $(date +%s) - T0 )))s] $1"; }

step "== 手工流程开始（对照节点：db-01 主 / db-02 从）=="

# ---------- db-01（主库）----------
step "db-01: apt update + 安装 mariadb-server/mariadb-client"
docker exec db-01 bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mariadb-server mariadb-client'

step "db-01: 建日志目录（slim 镜像无 /var/log/mysql，手工踩坑点）+ 写配置并重启"
docker exec db-01 bash -c 'mkdir -p /var/log/mysql && chown mysql:mysql /var/log/mysql && printf "[mysqld]\nserver-id = 101\nbind-address = 0.0.0.0\nlog_bin = /var/log/mysql/mariadb-bin\nrelay_log = /var/log/mysql/mariadb-relay\n" > /etc/mysql/mariadb.conf.d/60-manual.cnf && systemctl restart mariadb && systemctl is-active mariadb'

step "db-01: 建复制账号 repl 并查 binlog 位点"
docker exec db-01 bash -c "mysql -e \"CREATE USER 'repl'@'%' IDENTIFIED BY 'Repl_2026Lab'; GRANT REPLICATION SLAVE ON *.* TO 'repl'@'%';\" && mysql -NBe 'SHOW MASTER STATUS'"

BINLOG=$(docker exec db-01 bash -c "mysql -NBe 'SHOW MASTER STATUS'" | awk '{print $1}')
BINPOS=$(docker exec db-01 bash -c "mysql -NBe 'SHOW MASTER STATUS'" | awk '{print $2}')
step "db-01: 位点=$BINLOG:$BINPOS"

# ---------- db-02（从库）----------
step "db-02: apt update + 安装 mariadb"
docker exec db-02 bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mariadb-server mariadb-client'

step "db-02: 建日志目录 + 写配置（server-id 102）并重启"
docker exec db-02 bash -c 'mkdir -p /var/log/mysql && chown mysql:mysql /var/log/mysql && printf "[mysqld]\nserver-id = 102\nbind-address = 0.0.0.0\nlog_bin = /var/log/mysql/mariadb-bin\nrelay_log = /var/log/mysql/mariadb-relay\n" > /etc/mysql/mariadb.conf.d/60-manual.cnf && systemctl restart mariadb'

step "db-02: CHANGE MASTER 指向 db-01 并 START SLAVE"
docker exec db-02 mysql -e "CHANGE MASTER TO MASTER_HOST='db-01', MASTER_USER='repl', MASTER_PASSWORD='Repl_2026Lab', MASTER_LOG_FILE='$BINLOG', MASTER_LOG_POS=$BINPOS; START SLAVE;"

step "db-02: 校验复制状态（等待双 Yes）"
for i in $(seq 1 15); do
    S=$(docker exec db-02 mysql -NBe "SHOW SLAVE STATUS\G" | grep -E 'Slave_(IO|SQL)_Running' | awk -F': ' '{print $2}' | paste -sd, -)
    [ "$S" = "Yes,Yes" ] && break
    sleep 2
done
echo "db-02 复制状态: $S"
step "== 手工流程结束 =="
