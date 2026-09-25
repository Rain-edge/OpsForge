# ADR-0002：节点实验环境的形态取舍（labnode / systemd / 网络）

- 状态：已接受
- 日期：2026-09-06
- 关联：ADR-0001、RUNBOOK §8

## 背景

节点实验环境需要用容器模拟 7 台被管"真机"（约定：labnode 基础镜像、统一 ops 用户、
每节点内存限额 ≤256MB、SSH 端口映射 2221-2227），供平台纳管与后续所有模块（Ansible
执行、巡检、告警自愈、Playbook 部署中间件）作为靶场。需要在三个技术分叉上做取舍：

## 决策

### 1. systemd：保留（privileged 运行），仅 sshd 为保底方案而未启用

- 优先级：优先 systemd（能 `systemctl`），保底仅 sshd。
- 实测 `debian:12-slim + systemd + privileged: true` 一步到位：7 节点
  `systemctl is-active ssh` 全部 active，无需降级。
- 代价：7 个 privileged 容器。评估：与平台容器同级（官方要求 privileged）；节点的
  宿主端口映射绑定 127.0.0.1（不暴露到局域网），SSH 密码认证仅作为纳管瞬间分发公钥
  的引导通道，日常运维走平台私钥。实验环境风险可接受。
- 收益：巡检任务可直接用 `systemctl is-active <svc>` 做服务检测，不降级为
  进程探测；Keepalived 实验也依赖 systemd 单元管理。

### 2. 基础镜像：自建 labnode（debian:12-slim）而非社区 systemd 镜像

- 选 debian:12-slim 而非 ubuntu：体积小、apt 国内源（USTC）加速构建。
- 预装 python3 + python3-apt：**Ansible 目标机硬需求**（起所有 Playbook 都依赖）。
- 统一 ops 用户 + sudo NOPASSWD：为 Ansible become 部署 MySQL/Redis 铺路。
- 口令治理：镜像与仓库内不落口令——entrypoint 从 `OPS_PASSWORD` 环境变量注入
  （compose 经 `.env`，已被 .gitignore 排除），缺失即拒绝启动。

### 3. 网络：接入平台 compose 网络（docker_default）直连 + 宿主端口映射并存

- 平台纳管地址 = 节点容器名:22（走 docker_default external 网络，DNS 直连，不依赖
  宿主端口转发），这是更接近真实运维的形态，且 Spug 批量执行/发布/终端全部经此路径。
- 宿主 2221-2227 映射仅用于人工验收（`ssh -p 2221 ops@localhost`），与约定表格一致。
- 取舍：不采用 host.docker.internal 经宿主 NAT 的路径（多一跳、且行为随 Docker
  Desktop 版本漂移）。

## 后果与已知限制

- **容器运行代码 ≠ 本仓库代码**（ADR-0001 已预警，本次实测确认）：官方镜像运行的是首启从
  gitee 拉取的 v4.0.1。两个 API 脚本按容器内 v4.0.1 实际接口编写；执行引擎自建
  镜像运行工作区代码后，需重新核对本仓库 tip 的接口（差异实例：host 接口 v4.0.1 用
  独立分组表 `group_ids`，工作区 tip 用 `zone` 字符串字段）。
- 容器内 `free -m` 看到的是 WSL2 宿主内存（约 5.9GB）而非节点 256MB cgroup 限额
  （cgroup 内存限制不改变 free 视图）。**做内存巡检时不能用 free -m 判断节点
  限额**，应读 /sys/fs/cgroup/memory.current（cgroup v2）或 docker stats。
- node host key 在容器首启生成（镜像内已清理），容器重建后指纹变化；平台侧
  paramiko 用 AutoAddPolicy 不受影响，人工 ssh 用 `-o StrictHostKeyChecking=accept-new`。
- privileged + systemd 的节点镜像启动即约 8-10MiB 内存，限额 256MB 下跑 MySQL 主从
  需要调小 InnoDB buffer 等参数，届时若触顶按预案砍 cache 节点。

## 相关实测数据

见 RUNBOOK「节点实验环境」章节：7 节点 running、纳管 7/7、批量执行 7/7、发布产物
两节点落地、平台+节点内存实测（预算 4.5GB 内）。
