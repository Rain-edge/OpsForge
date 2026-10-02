# 从零重建全量复跑 · 实验记录（2026-10-02）

> 实施过程：2026-10-02（环境清理 → 从零重建 → 数据采集 → 缺陷修复 → 回归）。
> 目的：①验证「down -v 删卷后从零重建」这条链路真正可重复；②给 README 数据表
> 提供新时点的可溯源数字；③把只在"空环境"才暴露的问题修掉。
> 原始日志：宿主侧逐条命令输出（含时间戳）见执行记录，本文只摘关键结论。

## 1. 背景与动机

此前所有实测都跑在**已积累状态**的环境上（9 月起陆续手工建过检测任务、发布仓库、
smoke-ping 等）。本次刻意 `down -v` 清空全部卷与自建镜像后复跑，暴露了 7 个此前
不可见的问题——这正是"从零可复现"这一验收标准的价值。

## 2. 从零重建时间线（实测）

| # | 步骤 | 命令要点 | 耗时 |
|---|---|---|---|
| 0 | 拆旧环境 | 3 套 compose `down -v` + 删 6 卷 3 镜像 | ~10s |
| 1 | 平台镜像构建+启动 | `compose up -d --build`（构建层命中缓存） | **2s** |
| 2 | 等 mariadb 就绪 | `mysqladmin ping` 轮询 | 首次 3s 内 init 会报 (115)，轮询后成功（见 §5-1） |
| 3 | 平台初始化 | `docker exec spug init_spug admin spug.cc` | **4s** |
| 4 | API 就绪 | 登录接口 200 轮询 | **1s** |
| 5 | 7 台被管节点 | `lab/nodes compose up -d --build` | **5s** |
| 6 | 2 台 FRR 网络设备 | `lab/net compose up -d --build` | **1s** |
| 7 | 节点 sshd 就绪 | 平台容器内 22 端口探测 7/7 | ~2s |
| 8 | 纳管 7 台 | `register_hosts.sh`（建组+公钥分发） | **4s** |
| 9 | Playbook 入库 | `import_playbooks.sh`（6 册） | **5s** |
| 10 | 自愈演示环境 | `setup_heal_demo.sh`（检测+规则+webhook+设备口令） | **3s** |
| 11 | 演示态 | `setup_demo_state.sh`（demo 仓库 + 四册部署 + 巡检/探测） | **54s** |
| — | **合计** | 不含四册部署 ≈ **25s**；含演示态 ≈ **1 分 30 秒** | — |

环境：Windows 11 + Docker Desktop（WSL2，memory=6GB / 4 核），平台 `opsforge-spug:4.0`
（676MB）+ mariadb:10.8 + 7×labnode:1.0（256MB 限额）+ 2×FRR。

## 3. 功能实验数据（全部本轮实测）

| 实验 | 命令/对象 | 结果 |
|---|---|---|
| A. ad-hoc 并发执行 | 7 台 `hostname`（job 1） | `changed=7, unreachable=0, failed=0`，**1.0s** |
| B. 中间件一键部署 | MySQL 主从（db-01/02，job 2） | `ok=19 changed=12`，0 失败，**35.0s** |
| B′. 幂等复跑 | 同 playbook 再跑（job 3） | `changed=0`（**幂等成立**），4.0s |
| C. 巡检一轮 | 7 节点 × 6 检项 | **42 ok / 0 warn / 0 crit**，单轮 **1–2s**（HTML+Excel+manifest 三产物） |
| D. 网络探测 | 9 目标 × ICMP/TCP | 部署前 18/20（web-01/02 `tcp:80` 红）→ 部署 nginx 后 **20/20 全绿** |
| E. nginx 部署 | web-01/02（job 4） | `ok=10 changed=10`，**10.3s** |
| F. **自愈端到端** | 注入 `systemctl stop nginx` → 恢复 | **30s**（检测相位 27s + 闭环 3.2s），`crit→ok`，job #5 |
| G. 告警外发 | 钉钉+企微 mock 双渠道 | **2 条**，含主机/规则→Playbook(job 号)/复检/耗时 |
| H. 内存占用 | `docker stats` 全量 | 平台+7 节点+2 设备 **≈ 1.10 GiB**（预算 4.5GB） |

内存明细：`spug 560MiB + spug-db 95MiB + db-01/02 各 125MiB + web/cache 10–12MiB + r1/r2 9MiB`。
（README 旧值 611MiB 为空载时点；部署中间件后 1.1GiB 为当前真实占用。）

## 4. 验收结果

| 阶段 | pytest | Playwright |
|---|---|---|
| 从零重建直后（修复前） | 142 passed | **27 passed / 4 failed** |
| 修复后回归 | 142 passed | **31 passed**（最终确认） |

修复前 4 条失败的根因（全部为"演示态/测试数据未随重建恢复"，非产品缺陷）：

| 用例 | 根因 | 修复 |
|---|---|---|
| AC-ANS-07 smoke-ping | ① `smoke-ping` Playbook 只存在于旧环境 DB；② 新建的 smoke-ping 漏了默认 `gather_facts`（recap ok=2 ≠ 用例口径 ok=4） | 入仓库 `lab/playbooks/smoke-ping.yml`（并保留默认 gather_facts） |
| AC-E2E-01 四册幂等 | redis-sentinel/baseline 未部署过（首跑 changed≠0），且用例硬编码旧环境 playbook id 10–13 | `setup_demo_state.sh` 部署四册；用例改为**按名称动态解析** playbook/主机 id |
| AC-INFRA-06 应用发布 | 发布源 bare 仓库 `demo-app.git` 是手工建的，重建即丢 | `setup_demo_state.sh` 自动创建 |
| AC-E2E-04 备份 diff | `storage/net_devices.env` 丢失 → 设备备份静默失败（§5-6） | `setup_heal_demo.sh` 生成口令文件 |

## 5. 本轮发现并修复的问题（7 项，均已落码/落文档）

| # | 问题 | 影响 | 修复 |
|---|---|---|---|
| 1 | 空库首次 `init_spug` 报 `(2002, Can't connect to server on 'db' (115))` | 从零启动照 README 步骤走会失败 | 快速开始加"等 DB 就绪"步骤（RUNBOOK 坑 7） |
| 2 | 空卷首跑 `import_playbooks.sh`：`Could not find the file /data/repos/...` | 从零重建无法导入 Playbook | 脚本加 `mkdir -p`（RUNBOOK 坑 8） |
| 3 | `docker cp` 源路径报 `GetFileAttributesEx C:\c:`（本机 MSYS 不做参数转换） | 两个脚本在 Git Bash 下不可用 | 统一 `to_win_path()`（RUNBOOK 坑 9） |
| 4 | **自愈规则可能被静默跳过**：`CHECKS` 注册表靠 import 副作用填充，仅加载 monitor 链路的进程里为空 | 上游/部署形态一变自愈即静默失效 | `heal/service._handle_alert` 显式导入 checks（回归自检通过） |
| 5 | `heal/__init__.py` 注释写"monitor/utils.py 最小侵入 4 行"，与实现（signal 零侵入）矛盾 | 文档与代码打架，评审会翻车 | 注释更正为 signal 挂点描述 |
| 6 | 设备备份**静默失败**：`storage/net_devices.env` 丢失 → `WARNING 口令未配置`，UI 无感 | NET 备份/变更审计整体失效且难察觉 | `setup_heal_demo.sh` 生成该文件（0600）+ 缺参显式警告 |
| 7 | 演示态三缺口：`smoke-ping`、`demo-app.git`、四册部署 | 4 条验收用例失败 | 新增 `lab/playbooks/smoke-ping.yml` + `lab/scripts/setup_demo_state.sh` |
| 8 | 验收用例硬编码旧环境数据：`e01` 写死 playbook id 10–13、`ac07` 依赖 smoke-ping 口径 | 从零重建后用例必失败 | 用例改为按名称动态解析 id（缺对象时报错指向 setup 脚本） |
| 9 | 自建 `smoke-ping.yml` 少写默认 `gather_facts` 口径 | recap ok=2 与用例口径 ok=4 不符，AC-ANS-07 失败 | 恢复默认 gather_facts，并在文件头注记该口径约束 |

## 6. 修复的回归验证

| 验证项 | 方法 | 结果 |
|---|---|---|
| CHECKS 显式导入 | 在"只加载 monitor 链路"的进程里造一条命中规则的告警（复现修复前被跳过的路径） | 事件成功创建（`recovered`，前复检判定），**规则不再被跳过** |
| 设备备份恢复 | 修复后等一个备份轮（120s） | `net.log` 出现 `Authentication (password) successful`，`storage/netbackup/r1/20261002-102149.cfg` 落盘 |
| 全套验收 | `playwright test` | **31 passed** |
| 单元测试 | 容器内 pytest | **142 passed** |

## 7. 结论

- 「从零重建」链路现已**一条命令一步**可复现：平台 25s、含演示态 90s；
- 本轮修复全部是"只在空环境暴露"的问题——说明**验收必须包含一次真实清空复跑**，
  已建议纳入发布前检查单；
- 新增 `setup_heal_demo.sh` 与 `setup_demo_state.sh` 后，重建 → 演示 → 验收全链路无手工步骤；
- 数据与此前复测一致（MySQL 35s、幂等 changed=0、自愈闭环 3.2s），跨环境复现性好。
