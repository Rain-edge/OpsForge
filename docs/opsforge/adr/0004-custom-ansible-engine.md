# ADR-0004：为何自建 Ansible 执行引擎，而非只用 Spug 原生批量执行

- 状态：已接受
- 日期：2026-09-09（补记；决策随执行引擎落地）
- 关联：RUNBOOK §9、ADR-0003

## 背景

Spug v4.0.1 自带"批量执行"：paramiko 直连节点逐台跑命令，结果按主机展示。
对一次性命令够用，但项目的核心场景——Playbook 部署 MySQL 主从、定时巡检、
告警触发自愈——需要它不具备的能力。

## 决策

在平台内新增 `ansible_ops` app 自建执行引擎：ansible-core 2.15.13（镜像内置）+
按主机表生成动态 inventory + redis 计数信号量并发上限 + WebSocket 逐行回传 +
Job 模型与终止链路。不用 celery/额外 broker，进程内线程池 + redis 名额。

## 理由

1. **幂等与编排是硬需求**：raw 命令跑两遍就是改两遍。MySQL 主从部署实测对比：
   Playbook 一键 35s 一次通过、二跑 changed=0；手工逐台净 ~60s、含踩坑重试 ≈100s
   （见实验记录 2026-10-02-full-replay.md）——价值不在快，在可重复 + 零遗漏。
2. **一个引擎供三个模块复用**：巡检采集、自愈执行、手动执行
   走同一 `exec` 通道与并发名额，避免三套调度互相打爆 4.5GB 内存预算。
3. **并发必须可预算**：7 节点 + 平台同宿主，`-f 10` 单 job 加 redis 信号量全局上限 2，
   gunicorn 多进程下安全，是内存预算的直接推论。
4. **实时性与可终止**：WS 逐行回传让页面像终端；redis pid/标记跨 worker 传递 +
   看门狗，kill 3s 内落终态、无孤儿进程——原生批量执行没有任务对象，无从谈起。

## 后果

- ✅ 巡检单轮 7 节点 42 检项 1–2s（复用引擎批量通道）；自愈闭环段恒定 2.1–3.4s。
- ✅ Playbook 获得 git 版本化 + syntax-check + 回滚，配置即代码有落地载体。
- ⚠️ 镜像必须内置 ansible-core（Python 3.9 只能用 2.15 线）；存在 PYSEC-2026-3458，
   链路不达（不调用 ansible-galaxy）知情接受，使用约束写入 RUNBOOK。
- ⚠️ 平台零 collections：`sysctl`/`timedatectl` 等社区模块不可用，Playbook 用
  原生模块 + 模板 workaround（踩坑见 RUNBOOK §12）。
- ⚠️ Job 日志/状态多一份存储（storage 卷 + 表），换来的是审计与回放能力。
