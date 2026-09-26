# 告警自愈告警闭环自愈 · 实验记录

> 2026-09-21 实施过程。环境：平台容器 `spug`（opsforge-spug:4.0）+
> 7 节点（web-01/02 · db-01/02 · cache-01/02/03）。故障对象 web-01 nginx 进程，
> 自愈 Playbook = 平台库 heal-nginx（id 14，`systemd state=restarted` + pgrep 确认）。

## 链路构成（源码读证后的挂点决策）

- **挂点零侵入**：上游 monitor 告警落库点（`monitor.utils.handle_notify` →
  `Alarm.objects.create`）即"告警发生"事件源，heal 以 `post_save(Alarm)` signal
  挂入（`ansible_ops/heal/signals.py`，`apps.py ready()` 注册，dispatch_uid 防重复）。
  上游文件零改动——Dockerfile 里上游代码是构建期 git clone 的 v4.0.1 原件，
  工作区（2.3.18 lineage）改 monitor/ 既不生效也不该改（基线决策在待办①）。
  已知耦合：上游若改掉 handle_notify 落库行为，signal 失效。
- **触发链**：signal（同步段仅匹配+去重，毫秒级）→ daemon 线程闭环 →
  ①自愈前复检（INSP 单项插件，防告警过时误触发）→ ②AnsibleJob（复用执行引擎
  引擎 + redis 并发名额排队）同步等终态 → ③自愈后复检（重试 3 次 ×5s）→
  ④恢复判定 + HealEvent 落库 + webhook 外发（钉钉/企微 markdown，URL 从
  env/`heal_webhook.env` 读）。
- **防自愈风暴三道闸**（实测全部生效）：冷却窗口（cooldown 分钟内同规则同主机
  不重复触发）、闭环进行中去重、自愈前复检已恢复则不跑 playbook。
- **自愈失败升级**：job 失败/超时（300s 预算）或复检未恢复 → failed + 消息
  标"⚠️ 需人工介入"。

## 验收 1：注入 nginx 进程挂 → 端到端计时

检测任务 Detection #1（进程检测，targets=[web-01]，rate=1 分钟，threshold=1，
quiet=1）。规则 HealRule #1（keyword=nginx，host=web-01，playbook 14，
复检插件 process，cooldown=1 分钟）。

**口径**：端到端 = 注入时刻 → 复检确认恢复（recovered 判定）；闭环段 =
告警事件创建 → 恢复判定（HealEvent duration_ms）。

| 轮次 | 注入时刻 | 端到端 | 闭环段 | 结果 |
|---|---|---|---|---|
| 1 | 16:35:09 | **~61 s** | 3293 ms | recovered（crit→ok）|
| 2 | — | **27982 ms** | 2189 ms | recovered |
| 5 | — | **12345 ms** | 2317 ms | recovered |
| 7 | — | **67084 ms** | 2308 ms | recovered |
| 8 | — | **39485 ms** | 2143 ms | recovered |
| 9 | — | **39984 ms** | 2341 ms | recovered |

- **端到端 6 样本 12.3s–67.1s，≤60s 达标 4/6**：瓶颈不在自愈而在检测发现——
  上游 Detection 的 rate 单位是分钟（最小 1），注入到下一检测轮的相位 0–60s
  浮动 + 闭环 ~3s。相位不利时（轮 1/7）超线 ~1-7s。生产场景 rate 可配 10s/30s
  级（或事件驱动），该段可压缩到秒级；闭环段恒定 2.1–3.4s 与相位无关。
- 闭环段明细（全部 recovered 样本）：2189 / 2308 / 2314 / 2317 / 2323 / 2341 /
  3293 / 3342 / 3361 / 3433 ms——含自愈前复检 + playbook 重启 nginx + 自愈后
  复检（重试预算未动用，nginx restart 后立即就绪）。

## 验收 2：同类故障重复 ≥5 次，自愈成功率

- 全程共 **18 次告警事件：13 recovered + 5 suppressed + 0 failed**
  （`HealEvent` 全表统计）。剔除 5 次冷却抑制（防风暴机制正确工作，非自愈失败），
  **自愈触发即成功率 13/13 = 100%**。
- suppressed 事件 detail 落 `{"reason": "冷却窗口内（1 分钟）"}`，可审计。

## 验收 3：外发消息 + 冷却抑制

- **消息外发**：每事件双渠道（钉钉 markdown + 企微 markdown），消息含
  主机 / 指标（告警名）/ 触发动作（规则 → Playbook + **job 号**）/ 复检
  before → after / 耗时 ms；失败时标"⚠️ 自愈失败，需人工介入"。
  本机无真实机器人，用容器内 mock 接收端（`lab/scripts/mock_webhook.py`，
  端口 9999，webhook URL 经 `heal_webhook.env` 指向 mock，双渠道各收一条）
  ——消息页截图 `lab/probe-results/heal-webhook-messages-top.png` +
  HTML 原件 `heal-mock-messages.html`。生产切换 = 改 env 为真实 webhook 地址，
  钉钉支持 `URL|加签密钥` 后缀（复用上游 `libs/webhook.gen_dd_sign` 算法）。
- **冷却期内重复告警不重复触发**（专门演示，17:00 前后）：注入A → 告警
  17:00:07 → 自愈恢复 17:00:10（event 16，job #71）；恢复后**立即**再注入 →
  17:01:07 检出告警 → 距上次闭环 57s < cooldown 60s → **event 17 suppressed，
  未创建 job、未发自愈动作**；冷却过期后 17:02:07 下一轮告警正常自愈
  （event 18）——抑制是暂时的，故障不会因冷却而无人认领。

## 数据留痕

- `HealEvent` 表 18 条全留（验收可查）；自愈 job（AnsibleJob 表，event.job
  关联）可在平台任务记录复核。
- 消息 HTML：`lab/probe-results/heal-mock-messages.html`（浏览器可直接打开）。
- 观察器：`lab/scripts/heal_watch.py`（容器内轮询 HealEvent 到终态，输出 JSON）；
  复跑路径：建 Detection/HealRule → `docker exec web-01 systemctl stop nginx` →
  `docker exec spug python3 /tmp/heal_watch.py`。
