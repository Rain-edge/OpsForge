# 网络模块网络探测与设备备份 · 实验记录

> 2026-09-23 实施过程。环境：平台容器 `spug`（opsforge-spug:4.0，新增
> netmiko==4.6.0 与 supervisor program `opsforge-net`）+ 7 被管节点 + 2 台 FRR
> 设备容器 r1/r2（`lab/net/`，frrouting/frr:latest 派生 + openssh-server，
> Alpine 3.16.2 / FRR 8.4_git，privileged + mem_limit 128m，接入 docker_default）。

## 链路构成

- **探测引擎**（`ansible_ops/net/probe.py`）：默认矩阵内置 `matrix.yaml`
  （7 节点 ICMP+TCP22、web-01/02 加 TCP80、r1/r2 ICMP+TCP22，共 9 目标 20 检查项），
  env `OPSFORGE_PROBE_MATRIX` 可覆盖；asyncio.Semaphore ≤50 并发（约定），ICMP 走
  系统 `ping -c1 -W2` 子进程（spug 容器内已验证 RAW/二进制均可用），TCP 走
  `asyncio.open_connection`（3s 超时）。快照整轮落 `net_probe_runs.cells`（JSON）。
- **断链状态机**（`apply_state`）：每（目标×检查项）一行 `net_target_states` 游标，
  连续失败 ≥3（env `OPSFORGE_NET_FAIL_THRESHOLD`）→ `Alarm(status='1')`，恢复 →
  `Alarm(status='2')`（duration=断链持续秒数，first_fail_at 起算）；告警只在状态
  跃迁时产生。走告警自愈的 `post_save(Alarm)` 挂点：告警名含"网络断链"不匹配
  任何自愈规则关键字（heal 匹配 name/target/type 子串），自然旁路不误触发自愈。
- **设备备份**（`ansible_ops/net/backup.py`）：设备清单内置 `devices.yaml`
  （r1/r2，netmiko `linux` 驱动 SSH + `vtysh -c 'show running-config'`——FRR 官方
  镜像无 netmiko 原生驱动，任务踩坑建议口径，实测可用）；口令读取 env
  `OPS_PASSWORD` 优先、`storage/net_devices.env` 文件兜底（沿用 heal_webhook.env
  惯例，storage 为持久卷，不入库不入镜像）；备份落
  `storage/netbackup/{device}/{ts}.cfg`，与最近存量版本逐行 diff，**内容有变化才
  写新文件并落 `net_config_changes`**（相邻版本=相邻的不同配置版本；回溯任意
  时间点取该时点前最近版本）。
- **常驻调度**（`run_net --loop`，supervisor program `opsforge-net`）：
  apscheduler 双 job——探测 30s（`OPSFORGE_NET_PROBE_INTERVAL`）/ 备份 120s
  （`OPSFORGE_NET_BACKUP_INTERVAL`），启动即各先跑一轮（同 run_inspection 口径）。
  手动 `POST /api/ansible_ops/net-probe/run/` 与定时轮共用同一状态机（口径一致）。

## 前置验证（开工时实测）

| 项 | 结果 |
|---|---|
| frrouting/frr:latest 拉取 | 成功（官方 docker-start 强制启动 zebra+staticd，frrcommon.sh:98 特判，静态路由开箱可用） |
| 原版镜像 sshd | **无**（`which sshd` 为空）→ 派生镜像 apk 装 openssh-server，ops 用户入 frr/frrvty 组跑 vtysh |
| spug 容器 ICMP | ping 可用，到 r1 实测 0.435ms |
| netmiko 4.6.0 + linux 驱动 | SSH ops@r1 + send_command_timing 采 show running-config 实测通过；输出仅需剥 `%` 前缀 vtysh 警告行 |
| pip-audit | netmiko 依赖树（paramiko/scp/tenacity/pyserial/ntc-templates）**零已知漏洞**；sqlparse/urllib3/wheel 为上游既有依赖条目，非本次任务引入 |

## 验收 1：人为断链 → 矩阵变红 + 告警 → 恢复转绿

`docker stop cache-03`（18:38:06）→ 定时探测轮连续失败 → 第 3 轮触发断链告警
→ `docker start cache-03`（18:41:09）→ 下一轮恢复。

| 事件 | 时刻 | 数据 |
|---|---|---|
| 停机 T1 | 18:38:06 | run#22（18:40:15）矩阵红格 ×2：`cache-03 icmp`（ping 退出码 2）、`cache-03 tcp:22`（gaierror: No address associated with hostname）——容器停后 Docker DNS 记录消失，失败原因可读 |
| 断链告警 ×2 | 18:39:15 | Alarm 29（icmp）/30（tcp:22），status=报警发生，duration=连续3次失败；**断链检出延迟 69s**（阈值 3 × 30s 间隔） |
| 恢复 T2 | 18:41:09 | — |
| 恢复告警 ×2 | 18:41:14 | Alarm 31/32，status=故障恢复，**duration=179秒**（first_fail_at 起算的断链持续时长，与停机窗口吻合）；**恢复检出延迟 5s** |
| 手动探测 run#26 | 18:42:09 | 20/20 全绿 |

UI 实证：矩阵页（/ansible/net-probe）红/绿标签渲染正常，全页截图
`lab/probe-results/net-acceptance-page.png`。

## 验收 2：修改 FRR 配置 → 下轮备份 diff 检出（延迟实测）

`docker exec r1 vtysh -c 'conf t' -c 'ip route 10.99.0.0/24 Null0' -c 'end'`，
变更时刻 **18:36:24**（运行态变更，不 write memory——r1 的 frr.conf 为只读挂载，
容器重启即回到种子配置，实验可重复）。

- 下轮备份 18:36:44 采集 → 新版本 `20260926-183644.cfg` 落盘 +
  `NetConfigChange #1`（from_ts=20260926-182928 → to_ts=20260926-183644，
  detected_at=18:36:46）。
- **检出延迟 = 22 秒**（变更 → 下轮 120s 备份轮采集落库）。
- diff 内容精确：unified diff 单行 `+ip route 10.99.0.0/24 Null0`，上下文完整。

## 验收 3：备份文件可回溯任意时间点

`GET /api/ansible_ops/netbackup/r1/files/{ts}/`：

- 旧版本 `20260926-182928`：无 10.99 路由、含种子路由 10.10.0.0/16 ✓
- 新版本 `20260926-183644`：两者皆有 ✓
- 版本列表按 ts 新→旧（ts 文件名字典序=时间序），变更记录表 50 条以内全量。

## 单元测试与回归

- pytest **136/136** 容器内全绿（既有 117 + 本次任务新增 19：矩阵加载 4、TCP 探测 2、
  ICMP mock 2、状态机 3、备份 5、API 3）。
- 前端矩阵页真实浏览器渲染验证（登录 → 菜单直达 → 矩阵/备份卡片/变更表全部正常）。

## 结论

三条验收标准全部通过。量化数据：断链检出 69s（阈值 3×30s）、恢复检出 5s、
配置变更检出 22s（备份间隔 120s）；断链持续时长 duration=179s 自动核算。
单台 FRR 内存 ~40MB（128m 限额），平台增量依赖 netmiko 无已知漏洞。
