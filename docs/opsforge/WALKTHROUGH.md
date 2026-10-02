# OpsForge 从零跑一遍 · 手把手引导

> 面向：第一次上手这套实验环境的人。
> 口径：每条命令都在 Windows 11 + Git Bash + Docker Desktop(WSL2) 上实测过；
> 「预期」给的是真实输出样式，「你应该理解什么」是本步的知识点，「坑」是实测踩过的。
> 全程约 5 分钟（不含四册部署与验收套件）。

## 0. 前置检查（30 秒）

```bash
docker version --format '{{.Server.Version}}'      # 预期：29.x，Docker Desktop 已启动
docker info --format 'mem={{.MemTotal}} cpu={{.NCPU}}'   # 预期：≤6.2GB（本项目按 6GB WSL 预算设计）
netstat -ano | grep -c ":80 .*LISTENING"           # 预期：0（80 端口空闲，被占改 override 端口映射）
cat ~/.wslconfig                                    # 确认 memory=6GB / processors=4（本项目资源预算来源）
```

**你应该理解什么**：为什么这套环境的预算是 4.5GB？——平台容器 ~650MB + 7 节点 × 256MB 限额
+ 2 台设备 128MB，留 2GB 给中间件与余量；宿主机 WSL2 只有 6GB，所以并发上限设 2（见 §5）。

## 1. 清空历史环境（可选，验证"从零"才需要）

```bash
cd <repo>
docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml down -v
(cd lab/nodes && docker compose down -v)
docker compose -f lab/net/docker-compose.yml down -v
docker volume ls | grep -E "spug|labnode"        # 预期：无输出
```

**坑**：`-v` 会删数据卷（主机、作业历史、巡检报告全没）。要保留就先去
`docs/opsforge/RUNBOOK.md` 看备份命令。

## 2. 起平台（约 10 秒）

```bash
docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml up -d --build

# 等数据库就绪——不等的后果：init 报 (2002, "Can't connect to server on 'db' (115)")
until docker exec spug-db sh -c 'mysqladmin ping -uroot -pspug.cc 2>/dev/null' | grep -q alive; do sleep 2; done

docker exec spug init_spug admin spug.cc          # 预期：初始化/更新成功 + 创建用户成功
```

**验证点**：`docker ps` 显示 `spug` 与 `spug-db` 两个容器 Up；浏览器打开 http://localhost 能出登录页。

**你应该理解什么**：
- `docs/docker/` 是上游 v4.0.1 原件（禁改），`docker-compose.override.yml` 是 OpsForge 的本地适配
  （命名卷替代宿主绝对路径；换自建镜像 `opsforge-spug:4.0`）；
- 镜像里内置了上游代码 + 二开代码 + 前端产物 + ansible-core（为什么不用官方镜像：官方镜像
  声明 `VOLUME /data` 无法撤销，代码只能落卷，见 ADR-0001）。

## 3. 起 7 台被管节点（约 5 秒）

```bash
cd lab/nodes && cp .env.example .env && docker compose up -d --build && cd ../..

# 验证 systemd 与 SSH 都在
for n in web-01 web-02 db-01 db-02 cache-01 cache-02 cache-03; do
  echo -n "$n: $(docker exec $n systemctl is-active ssh) "; done; echo
```

**预期**：7 个 `active`；宿主 2221–2227 端口可 SSH。

**你应该理解什么**：节点是 `debian:12-slim + systemd + sshd` 的 privileged 容器（systemd 需要），
每台 `mem_limit 256m`；节点内 `free -m` 显示的是 WSL 宿主内存（cgroup 限额不改变 free 视图）——
所以巡检的内存检查读的是 cgroup v2 的 `memory.current`（见 §7）。

## 4. 纳管 7 台（约 4 秒）

```bash
bash lab/scripts/register_hosts.sh
```

**预期**：`[GRP] 分组 web/db/cache 已创建` + 7 行 `[OK] 纳管成功，平台公钥已分发`；重跑全 `[SKIP]`（幂等）。

**你应该理解什么**：纳管 = 平台先用密码登录一次并**把平台公钥追加到节点 authorized_keys**，
之后全部走私钥认证（`ansible_ssh_private_key_file` 由执行引擎按主机私钥动态生成，0600 落盘，
用完即删——见 `spug_api/apps/ansible_ops/inventory.py`）。

**坑**：节点被 `--build` 重建后文件系统重置，平台 known_hosts 旧指纹冲突 + 公钥丢失，
需按 RUNBOOK §12 清指纹重发。

## 5. 第一次执行：ad-hoc（1 秒）

浏览器 → 「Ansible 自动化 / 执行」→ 勾选 7 台 → shell → `hostname && uptime` → 执行。

命令行等价：

```bash
TOKEN=$(curl -s -X POST http://localhost/api/account/login/ -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"spug.cc","type":"default"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")
curl -s -X POST http://localhost/api/ansible_ops/exec/ -H "x-token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"host_ids":[1,2,3,4,5,6,7],"raw_module":{"name":"shell","args":"hostname"}}'
# → {"data": {"job_id": 1}, "error": ""}   然后 GET /api/ansible_ops/jobs/1/ 看 summary
```

**预期**：`changed=7, unreachable=0, failed=0`，墙钟约 1 秒；页面日志逐行实时滚动（WebSocket）。

**你应该理解什么**：
- 为什么 ad-hoc 的结果是 `changed=7` 而不是 `ok=7`？——`shell` 模块不做幂等判断，
  执行成功一律计 changed；playbook 的 recap 才有准确语义（ok/changed 分开）。
- 并发上限为什么是 2？——`MAX_JOBS=2` 走 **redis incr/decr 名额**（不是线程池），
  因为 gunicorn 是多进程，模块级线程池管不住跨进程并发（见 `executor.py` 头部注释）。
- kill 一个正在跑的任务靠什么？——`start_new_session` 让 ansible 及其 ssh 子进程成组，
  kill 时对进程组发 SIGTERM；pid 经 redis 跨进程传递。

## 6. 一键部署中间件 + 幂等验证（约 40 秒）

浏览器 →「Ansible 自动化 / Playbook」→ `mysql-master-slave` 行点「执行」→ 选 db-01/db-02。

**预期**：首次 job `ok=19 changed=12`（约 35 秒）；**再跑一次 changed=0**（幂等）。

**你应该理解什么**：
- "幂等"的证明方式就是连跑两次第二次 changed=0——它说明 playbook 描述的是**终态**而非**动作序列**；
- 对比手工：他们手工装过一次净耗时约 60 秒、含踩坑约 100 秒（RUNBOOK §12）；
- 查看某个任务到底改了什么：任务详情页的日志里每个 task 的 `changed` 标记。

## 7. 巡检一轮（1–2 秒）

「Ansible 自动化 / 巡检」→ 点「立即巡检」→ 等状态变「成功」→ 下载 HTML / Excel。

**预期**：42 项（7 节点 × 6 检项：cpu / memory / disk / process / port / cert）全绿；
报告里能看到与上一轮的 diff 列。

**你应该理解什么**：
- 一轮巡检只发**一次** `ansible all -m shell`（6 项检查合并成一条采集脚本，输出 base64 后拼单行
  JSON，避开 shell↔JSON 双重转义）——42 次 SSH vs 1 次，这是"巡检不打扰生产"的关键设计；
- 内存/CPU 检查读 cgroup v2（`memory.max/memory.current`、`cpu.pressure` PSI），不用
  `/proc/meminfo`（容器里那是宿主的）。

## 8. 网络探测与设备备份（30 秒 / 120 秒轮）

「Ansible 自动化 / 网络探测」→ 点「立即探测」。

**预期**：`通 20 / 断 0`（9 目标 × ICMP/TCP）；下方设备备份表能看到 r1/r2 的配置版本与 diff。

**动手**：停一台设备看它变红、并触发告警：

```bash
docker stop cache-03
# 立即探测 3 轮（每轮间隔 ≥3s，状态机连续 3 次失败才告警）
docker start cache-03   # 恢复后几轮内回绿
```

**你应该理解什么**：状态机为什么要"连续 3 次失败"才告警？（防抖：一次网络抖动不该出告警）；
设备配置备份为什么"内容变化才落新版本"？（否则每 2 分钟一个快照，无法审计）。

## 9. 告警自愈闭环（端到端约 30 秒）

```bash
# 注入故障
docker exec web-01 systemctl stop nginx

# 观察闭环（容器内轮询 HealEvent 到终态）
MSYS2_ARG_CONV_EXCL="/tmp;/data" docker exec spug python3 /tmp/heal_watch.py --timeout 180
# 预期：{"status":"recovered","duration_ms":~3200,"before":"crit","after":"ok",...}
docker exec web-01 systemctl is-active nginx    # 预期：active（被自愈 playbook 拉起）
```

**你应该理解什么**：
- 端到端 30 秒 = 上游检测相位（1 分钟轮询，最坏 60 秒）+ 闭环段 ~3 秒；**闭环段才是自愈本身的耗时**；
- 三道防风暴闸：① 自愈前复检（告警到处置有延迟，故障可能已自愈 → 不白跑 playbook）
  ② 冷却窗口（同规则同主机 1 分钟内不重复）③ 进行中去重；
- 外发消息（钉钉/企微 mock）里带 **job 号**——从告警消息能直接追到执行日志；
- 这条链路的挂点是 `post_save(Alarm)` signal（上游文件零改动），代价是**隐式耦合**
  （上游改掉落库行为即失效）——ADR-0003 记录了这个取舍。

## 10. 跑验收套件（约 5 分钟）

```bash
cd tests/acceptance && npx playwright test        # 预期：31 passed
docker exec spug sh -c 'cd /data/spug/spug_api && python -m pytest apps/ansible_ops/tests -q'   # 预期：142 passed
```

**你应该理解什么**：验收用例都是"用户可见行为"级（点按钮、看文案、断言 API 一致），
不做白盒——所以能覆盖"前端跳转带参失败""实时日志断流"这类单测抓不到的回归。

## 11. 停止 / 重置

```bash
# 日常停止（保留数据）
docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml down
(cd lab/nodes && docker compose down)

# 彻底重置（删数据卷）：见 §1
```

## 附：一条命令跑完全流程

```bash
bash lab/scripts/register_hosts.sh && \
bash lab/scripts/import_playbooks.sh && \
bash lab/scripts/setup_heal_demo.sh && \
bash lab/scripts/setup_demo_state.sh
```

四个脚本全部幂等，可重复执行；从零重建（§1）后依次跑完即到达"可演示 + 可验收"状态。

## 附：这一遍你应该能回答的 12 个问题

1. 为什么并发上限是 2 而不是 20？名额怎么实现跨进程？
2. ad-hoc 的 `changed=7` 和 playbook 的 `changed=0` 分别说明什么？
3. kill 一个运行中的 ansible 任务，信号发给了谁？
4. Playbook 回滚为什么读 git blob 而不是 `git show`？
5. 巡检为什么一轮只发一次 ansible？内存检查为什么不读 /proc？
6. 状态机为什么要连续 3 次失败才告警？
7. 自愈的三道闸分别防什么？
8. 自愈为什么用 signal 挂点？这个选择的代价是什么？
9. 端到端 30 秒里，哪一段是"检测"、哪一段是"自愈"？
10. 节点 `free -m` 为什么显示宿主内存？正确姿势是什么？
11. 从零重建（down -v）后，哪几步是必须的？漏了会怎样？
12. 如果节点从 7 台变 700 台，这套设计哪里会先崩？
