# 巡检报表批量巡检报表 · 实验记录

> 2026-09-19 实施过程。环境：平台容器 `spug`（opsforge-spug:4.0，重建后）+
> 7 节点（web-01/02 · db-01/02 · cache-01/02/03，256MB 限额）。

## 采集架构实测

- **单次 ansible 批量采集**：6 项检查（CPU/内存/磁盘/进程/端口/证书）合并为一条
  shell 脚本（各输出 base64 后拼单行 JSON），`ansible all -m shell -b` 一轮只发
  一次，7 节点 42 检项**单轮耗时约 1s**（report 8-11：created_at → finished_at 均
  为 1 秒内）。这是"复用引擎 raw 通道 + 批量采集"设计（约定"不另起 ssh 连接"）
  的直接收益。
- **容器口径修正（设计要点）**：节点 /proc/loadavg 与 /proc/meminfo 反映宿主
  （WSL2 6GB）而非节点自身——实测 web-01 `/proc/meminfo` MemTotal=6069488 kB。
  内存检查改读 cgroup v2（`memory.max`/`memory.current`，实测 268435456 /
  ~36MB），CPU 压力读 `cpu.pressure` PSI（some avg60）；两者不可用才回退 /proc
  并在 message 标注"宿主口径"。
- **证书检查**：节点无 openssl CLI → 采集只回传 PEM 内容（base64），平台侧用
  cryptography 解析 notAfter。默认扫描路径 `/etc/nginx/ssl/*.pem` 等，节点无证书
  → ok（"节点未发现证书"）。

## 验收 1：7 节点巡检出报告，异常标红

- report 8（手动 API 触发）：`POST /api/ansible_ops/inspection/run/` → report_id=8，
  success 42 ok / 0 warn / 0 crit。
- HTML 报告（`storage/reports/inspection-9-*.html`）内联 CSS 单文件，crit 行
  `<tr class="crit">`（#fde8e8 红底 #b42318 红字），warn 行橙底；下载响应带
  `Content-Disposition: attachment`。

## 验收 2：3 类故障注入 3/3 检出 + 恢复 diff

| 故障 | 节点 | 注入方式 | 检出 |
|---|---|---|---|
| 磁盘写满 | db-01 | `mount -t tmpfs -o size=4m` /mnt/fault + dd 写满（回环不伤容器层） | disk 100% crit |
| 停 nginx 进程 | web-01 | `systemctl stop nginx` | process crit + port 80 无监听 crit |
| 占用 80 端口 | web-02 | 停 nginx + `python3 -m http.server 80`（分离模式） | process crit + port 80 被 python 占用 crit |

- 故障轮 report 9：**42 - 5 = 37 ok / 5 crit**，3 类故障全部检出，diff 记录 5 条
  ok → crit。
- 恢复（start nginx ×2 + pkill http.server + umount）后 report 10：**42 ok / 0
  crit**，diff 记录 5 条 crit → ok——"恢复后下轮转 ok 且 diff 记录变化" ✓。
- 与上轮 diff 基线 = 最近一条成功轮的 manifest.json（首轮无基线时 HTML 标注）。

## 验收 3：Excel 完整性 + 依赖审计

- xlsx（openpyxl 3.1.2，上游既有依赖）下载后 `load_workbook` 重开正常：45 行、
  表头蓝底、crit 行红底/warn 行橙底、公式注入防护（`=+-@` 前缀转文本）。
- **本次任务零新增 Python 依赖**。pip-audit 复验：openpyxl 无已知漏洞；存量洞为上游
  锁定版本（django 4.2.30 / gitpython 3.1.40 / sqlparse / urllib3 / wheel）带来的
  背景项，非本次任务引入，处理策略待后续决策（README 声明口径）。

## 定时调度实测

- 方案：supervisor program `opsforge-inspection` 常驻 `manage.py run_inspection
  --loop`（内部 apscheduler，间隔 `OPSFORGE_INSPECTION_INTERVAL` 默认 300s）。
  否决约定字面 crontab 的理由：cron 进程不继承容器 env（MySQL 口令走 docker
  env），需 env 转储包装；supervisor 子进程天然继承。经确认（问题 1）。
- 日志链实测：`12:53:29 executed successfully → next run 12:58:27 → report 11
  scheduled success`——启动即跑首轮 + 每 300s 自动轮；手动轮与定时轮并发共存
  （并发名额与手动执行共享 MAX_JOBS=2，采集阶段排队等待最多 120s）。
- 进程重启恢复：`--loop` 启动时把遗留 running 轮次落 failed（防幽灵行导致前端
  无限轮询）。

## 踩坑（新增）

1. **ansible shell 段落解析**：结果行 `alias | CHANGED | rc=0 >>` 的正则必须吃满
   整行（`[^\n]*\n?`），否则 `| rc=0 >>` 残片混进 stdout 段落开头，JSON 解析必
   失败；且必须有 re.M，否则多主机输出只匹配第一行。
2. **base64 -w0 仍带尾换行**（GNU coreutils）：拼 JSON 前必须 `tr -d '\n'`，否则
   换行落进字符串使其非法。
3. **f-string 生成 printf 引号模板**极易错：闭合引号靠后续项 printf 的 `","` 首引
   号充当，改一处必须端到端实测（本次任务在容器内实测三次才稳定，教训：先写"脚本
   产出合法 JSON"的单测再联调）。
4. **运行时 v4.0.1 settings 无 TEMPLATES 配置**（上游后端零模板渲染）：
   `render_to_string` 直接 TemplateDoesNotExist——用独立
   `django.template.Engine(dirs=[...])` 指向 app 模板目录，不改上游 settings。
5. **USE_TZ=False（运行容器）与 USE_TZ=True（测试 settings）行为分叉**：
   `timezone.localtime()` 对 naive datetime 抛异常；证书到期统一 aware UTC
   （`datetime.now(timezone.utc)`）不受 settings 影响。
6. **工作区 spug_api 是 2.3.18 lineage**：Host 模型的 deleted_by 软删字段在运行
   容器（v4.0.1）不存在——"以容器内代码为准"再次生效（按工作区代码写的过滤
   直接 AttributeError）。
7. **pytest-django 事务与线程池**：真线程池里 ORM 用独立连接，看不见
   django_db 事务内夹具数据 → `_wait` 死轮询卡死。测试 mock `_pool.submit`
   为同步执行（与 test_views mock executor 线程同理）。
8. **`pkill -f http.server` 会自杀**：docker exec sh -c 的命令行本身含关键字，
   pkill -f 匹配到自身（exit 143）。用精确进程名或 `pkill -f '^python3 -m http'`。
9. **MSYS 路径转换**：`docker exec db-01 umount /mnt/fault` 被转成 Windows 路径 →
   `MSYS2_ARG_CONV_EXCL="/mnt"`（§6 既有坑的又一变体）。

## 自审修复记录

code-reviewer（自审）P0×5 + P1×6 + P2×5 全部修复后回归：采集脚本 JSON 合法性
（单测端到端跑 sh 验证）、RESULT_LINE_RE re.M 与整行匹配、测试 import 路径、
USE_TZ 分叉、并发名额 acquire/release 配对（失败路径不得净减共享计数器）、软删
过滤回退、xlsx 公式注入防护、文件视图 realpath 纵深防御、幽灵 running 轮
恢复、采集超时改 Popen+killpg 防孤儿进程组、nproc 采集补齐。pytest 100/100。
