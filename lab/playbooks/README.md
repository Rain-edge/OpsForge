# lab/playbooks — Playbook 库（仓库源）

OpsForge Playbook 库的**仓库源**。运行时形态在平台内（Spug Web「Playbook 管理」页，git 版本化），
本目录是开发与追溯源：四个主 playbook 经导入脚本灌入平台库，变量/模板辅助目录直接进入平台卷。

## 目录结构

```
lab/playbooks/
├── baseline.yml            # 全节点基线加固（tags: hardening / time / kernel）
├── nginx.yml               # web 组 Nginx 部署（handler 优雅重载）
├── mysql-master-slave.yml  # db 组 MariaDB 10.11 主从复制（一键部署）
├── redis-sentinel.yml      # cache 组 Redis 哨兵拓扑（声明式管理）
├── group_vars/
│   ├── all.yml             # 时区、工具包清单、内核参数（全节点）
│   ├── db.yml              # 主库声明、复制账号口令默认值、低内存调优参数
│   └── cache.yml           # Redis 口令默认值、声明拓扑（master 名/quorum/阈值）
├── host_vars/
│   ├── cache-01.yml        # redis_role: master
│   ├── cache-02.yml        # redis_role: replica
│   └── cache-03.yml        # redis_role: sentinel
├── templates/              # 8 个 Jinja2 模板（配置/服务单元下发全走模板）
├── import.py               # 导入器（在 spug 容器内执行，urllib 标准库零依赖）
└── README.md
```

## 双轨机制（接口约定「存放位置」的落地说明）

| 内容物 | 去向 | 为什么 |
| --- | --- | --- |
| 4 个主 playbook | 平台 API 入库（`POST/PUT /api/ansible/playbooks/`） | 平台内可编辑 + git 版本化（repos 机制），语法校验入库前自动执行 |
| group_vars / host_vars / templates | docker cp 进容器卷 `REPOS_DIR/ansible_ops/playbooks/` | 平台模型是单文件 playbook（`{id}.yml`），API 管不到目录；ansible-playbook 会自动加载 playbook 同目录的这三个子目录（容器内实测验证） |

git 仓库里 lab/playbooks/ 全量入版本库（可追溯）；平台卷内辅助目录不在 git 历史
（repo.py 按 `{id}.yml` 单文件提交，本次任务不动平台代码），改辅助文件 = 改仓库源 + 重跑导入。

## 导入与执行

```bash
bash lab/scripts/import_playbooks.sh          # 首次导入（同名跳过）
bash lab/scripts/import_playbooks.sh --force  # 仓库源为准覆盖更新
```

执行：Spug Web「执行 Ansible」页选择 Playbook（或 API/CURL 带 `playbook_id`，
端点 `POST /api/ansible_ops/exec/`）；口令可用 `extra_vars` 覆盖默认值
（如 `{"mysql_repl_password": "..."}`）。

## 变量与口令

- 变量集中 group_vars/host_vars（接口约定），playbook 内不硬编码环境相关值。
- 敏感口令（`mysql_repl_password` / `redis_password`）在 group_vars 放**实验弱口令默认值**
  （2026-09-16 约定），执行时 extra_vars 注入即覆盖（ansible 变量优先级：extra_vars 最高）。
- playbook 开头 assert 校验变量字符集（白名单，禁 `$` 与前导 `-`），防 SQL/redis-cli 参数注入。
- **口令轮换限制（如实声明）**：MySQL 复制账号仅首部署创建（`CREATE USER IF NOT EXISTS`
  不改密），extra_vars 新口令对既有部署不生效——轮换需在主库 `ALTER USER ... IDENTIFIED BY`
  并同步从库 `CHANGE MASTER`，或删账号后重跑；Redis 口令经模板+重启真实生效，但
  sentinel.conf（force=no）里的 auth-pass 停留旧值，需按「sentinel 配置变更」流程处理。
- `redis-cli -a <password>` 口令进进程 argv（ps 瞬时可见），实验环境已接受的暴露面。

## 幂等口径与已知局限（如实记录）

1. **幂等验证**：干净拓扑上每个 playbook 连跑两次，第二次 recap `changed=0`（experiments 留档）。
2. **sentinel.conf 特殊**：sentinel 运行后改写自己的配置文件（加 myid/known-* 自管状态），
   模板用 `force: false` 仅首部署写入；改 sentinel 配置需停 sentinel、删配置、重导重跑。
3. **failover 后重跑 = 拓扑重置**：sentinel 不改写客户端实例配置文件，旧主重启会按 conf 回到
   master；重跑本 playbook 把拓扑重置回声明状态（cache-01 主）——这是声明式的预期语义，
   changed>0 恰好反映「真实状态偏离声明」，不破坏幂等口径（口径只在干净拓扑上取证）。
4. **MariaDB 选型**：节点 256MB 限额跑不动 MySQL 8，用 Debian 12 自带 MariaDB 10.11
   （协议兼容，`SHOW SLAVE STATUS` 双 Yes 验收口径不变）。
5. **chrony 只装不跑**：privileged 容器与宿主共享内核，chronyd 运行会调宿主时钟，故 mask。
6. **sysctl 真实生效于宿主内核**（WSL2）：参数只选低危值，评估见 RUNBOOK。
