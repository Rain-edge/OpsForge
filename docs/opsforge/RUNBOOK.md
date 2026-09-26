# OpsForge RUNBOOK — 平台基线运行手册

> OpsForge = 基于 [openspug/spug](https://github.com/openspug/spug) v4.0.1 二次开发的自动化运维平台实验环境。
> 本手册记录平台从零到可登录运行的全部操作与实测踩坑。基线建立日：2026-09-01。

## 0. 环境前提

| 项 | 要求 | 本机实测 |
|---|---|---|
| 宿主 | Windows + Docker Desktop（WSL2 后端） | Docker Engine 29.7.2 |
| WSL2 内存 | `~/.wslconfig` 限 6GB（`memory=6GB`、`processors=4`） | 与 CloudForge 的 k3d 集群错峰运行 |
| 镜像加速 | `~/.docker/daemon.json` 已配国内 mirror（docker.1panel.live 等） | 拉镜像无需另配 |
| 端口 | 宿主 80 空闲（被占用见 §6 坑 4） | 实测空闲 |

## 1. 仓库与上游基线

OpsForge 仓库从零历史起步，首个提交起所有历史为：上游 Spug 历史 → OpsForge 二开提交。

```bash
# 配置上游 remote（走 ghfast.top 镜像，fake-ip 代理模式下 github.com 直连不稳）
git remote add upstream https://ghfast.top/https://github.com/openspug/spug.git

# 拉取上游全部历史与 tag（Windows git 需关闭 schannel 吊销检查）
git -c http.schannelCheckRevoke=false fetch upstream --tags

# 工作区对齐基线（内部流程文档与任务记录已被 .git/info/exclude 排除，不受影响）
git reset --hard upstream/master
```

**基线锚点（2026-09-01 实测）**：

- 基线 = 开工时 `upstream/master` tip = `50b2838`（"Update README.md"）
- 参考 tag `v4.0.1` = `81c98a4`；tip 比 tag 多 **77 个提交**（README 更新与少量前端优化，无破坏性变更）
- `docs/docker/`（官方 compose 与镜像构建文件）**在 tip 已被上游删除，仅存在于 v4.0.1** —— 本仓库用 `git checkout v4.0.1 -- docs/docker/` 整目录恢复（镜像构建仍需这批官方文件，决策记录见 ADR-0001）

后续同步上游新提交：

```bash
git -c http.schannelCheckRevoke=false fetch upstream
git rebase upstream/master   # 二开提交在历史顶端，rebase 保持线性
```

## 2. 启动平台

官方 compose（`docs/docker/docker-compose.yml`）+ 本地适配 override（**上游 compose 禁改**）：

```bash
docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml up -d
```

- 组件：`mariadb:10.8`（spug-db）+ `openspug/spug-service:4.0`（spug，内含 nginx/redis/gunicorn/daphne/supervisor）
- override 只做一件事：官方的宿主绝对路径挂载（`/data/spug/...`）在 Docker Desktop (WSL2 后端) 会落进虚拟机内部，改为**命名卷**（`docker_spug-db` / `docker_spug-service` / `docker_spug-repos`），数据持久且不依赖宿主路径
- `spug` 容器 `privileged: true` 为官方要求（文件分发依赖 sshfs），保留

**首次启动时序（实测）**：容器 09:43:45 启动 → entrypoint 从 gitee clone v4.0.1 代码 + 从 cdn.spug.cc 下载前端产物 → 09:43:55 supervisord 拉起 7 个服务（nginx/redis/api/ws/worker/monitor/scheduler），首启代码准备约 **10 秒**。用 `docker logs -f spug` 观察。

## 3. 首次初始化（建表 + 管理员）

容器内官方初始化命令（**没有浏览器向导**，必须手动执行）：

```bash
docker exec spug init_spug admin spug.cc
#                    └用户名┘└密码┘ 可自定义；-n 管理员 -s 为超管
```

等价于 `manage.py updatedb`（migrate）+ `manage.py user add`。成功标志：

```
初始化/更新成功
创建用户成功
```

> ⚠️ 本机账号为本地实验环境默认值（admin/spug.cc，与上游官方文档一致），不得用于任何暴露公网的环境。

## 4. 登录与界面验证

浏览器打开 `http://localhost` → 登录页 → 输入 admin / spug.cc → 首次登录弹「未能获取到访问者的真实IP」安全警告（nginx 未传 X-Forwarded-For 的提示，本地实验可忽略，点「知道了」）→ 进入工作台。

实测可用的页面（2026-09-01，浏览器逐一点验）：

| 菜单 | 路由 | 说明 |
|---|---|---|
| 工作台 | `/home` | 登录落地页 |
| **主机管理** | `/host` | 顶部含 **Web 终端**按钮（在线终端入口）；主机列表待节点实验环境纳管 |
| **批量执行** | `/exec/task` | 子菜单：执行任务/模板管理/文件分发 |
| **监控中心** | `/monitor` | 监控任务列表 |
| **报警中心** | `/alarm/alarm` | 子菜单：报警历史/联系人/联系组 |

命令行验证登录接口（**注意必须带 `type` 字段**，见 §6 坑 3）：

```bash
curl -s -X POST http://localhost/api/account/login/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"spug.cc","type":"default"}'
# 成功返回 {"data": {"id":1, "access_token":"...", "is_supper":true, ...}, "error":""}
```

## 5. 停止 / 重启 / 重置

```bash
# 停止与重启
docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml down
docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml up -d
# 4.0 镜像 supervisorctl 不可用（官方未开 RPC），重启服务直接 docker restart spug

# 彻底重置（删数据卷，回到未初始化状态）——破坏性操作，先确认
docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml down -v
```

## 6. 踩坑清单（实测）

| # | 坑 | 现象 | 处置 |
|---|---|---|---|
| 1 | 首次 `init_spug` 偶发报错 | `UnboundLocalError: local variable 'r' referenced before assignment`（Python 3.9 `sre_compile` 内部报错） | **重跑一次即可成功**；首跑表已建好（二次跑提示 No migrations to apply），推测为 CPython 3.9 边角 bug 的首次编译触发，未深究根因 |
| 2 | GitHub 直连不稳 | fetch/clone 卡住或失败 | remote 全部走 `https://ghfast.top/` 前缀镜像；git 命令加 `-c http.schannelCheckRevoke=false` |
| 3 | 登录接口报 `Column 'type' cannot be null` | curl 登录 500 | 登录接口要求 body 带 `"type":"default"`（普通登录）或 `"ldap"`；漏传即触发登录记录表非空约束 |
| 4 | 宿主 80 端口被占 | spug 容器起不来 | 改 override 中端口映射（如 `"8000:80"`），浏览器地址同步改 |
| 5 | 绝对路径挂载不落地 | `/data/spug/...` 落进 Docker VM 内部，Windows 侧不可见 | 已用命名卷替代（见 §2） |
| 6 | 容器运行代码 ≠ 本仓库代码 | 官方镜像首启从 gitee 拉 **v4.0.1** 代码运行 | 本次任务仅验证平台可运行；**让容器跑本仓库二开代码是执行引擎的前置工作**（挂载或自建镜像） |

## 7. 实测数据（2026-09-01）

| 项 | 值 |
|---|---|
| 平台内存占用 | spug 443.7 MiB + spug-db 93.4 MiB ≈ **537 MiB**（内存预算 2GB，余量充足） |
| 镜像体积 | spug-service:4.0 = 629MB，mariadb:10.8 = 370MB |
| 首启代码准备 | 约 10 秒（gitee clone + CDN 前端包） |
| 上游规模 | 640 提交 / 315 个跟踪文件 |

## 8. 节点实验环境（2026-09-06）

用容器模拟 7 台被管"真机"（systemd + sshd + 排障工具），作为平台纳管与后续
Ansible/巡检/自愈/Playbook 部署的靶场。设计取舍见 [ADR-0002](adr/0002-labnode-systemd-and-network.md)。

### 8.1 节点清单（约定固定）

| 节点 | 分组 | 角色 | 宿主 SSH 端口 |
|---|---|---|---|
| web-01 | web | Nginx（兼 LB/Keepalived 实验） | 2221 |
| web-02 | web | Nginx（兼 LB/Keepalived 实验） | 2222 |
| db-01 | db | MySQL 主 | 2223 |
| db-02 | db | MySQL 从 | 2224 |
| cache-01 | cache | Redis master | 2225 |
| cache-02 | cache | Redis replica | 2226 |
| cache-03 | cache | Redis sentinel | 2227 |

- 节点内统一 SSH 端口 22（平台纳管经 docker 网络直连 `节点名:22`）；宿主映射端口仅用于人工验收。
- 镜像 `labnode:1.0`：自建（debian:12-slim + systemd + openssh-server + python3 + 排障工具），构建文件 `lab/nodes/Dockerfile`。
- 每节点内存限额 256MB（compose `mem_limit`）。

### 8.2 启动 / 停止

```bash
# 首次：准备口令文件（.env 不入库，已在 .gitignore）
cd lab/nodes && cp .env.example .env   # OPS_PASSWORD 见下文，可改
docker compose up -d --build           # 构建镜像 + 启动 7 节点

# 日常 停止/启动（在 lab/nodes 下）
docker compose down
docker compose up -d
```

### 8.3 口令与 SSH

- 统一实验用户 `ops`，口令 `Opsforge.lab`（`lab/nodes/.env` 的 `OPS_PASSWORD`，仅限本地实验环境，勿用于任何公网/共享环境）。
- 人工验证（宿主）：`ssh -p 2221 ops@localhost`，输入上述口令。
- 平台侧认证：Spug v4 主机仅支持密钥认证。纳管时携带密码，平台自动用密码登录一次
  并把平台公钥追加到节点 `~/.ssh/authorized_keys`（容器内 `views._do_host_verify`），
  此后平台全部走私钥认证；密码通道仍开启（作为人工 ssh 入口），因此宿主端口映射绑定
  `127.0.0.1`，不暴露到局域网。

### 8.4 纳管与发布（脚本化，幂等可重复）

```bash
# 1) 纳管 7 台：建分组 web/db/cache + 逐台注册 + 平台公钥自动分发
bash lab/scripts/register_hosts.sh            # 默认 http://localhost

# 2) 应用发布演示：建环境/应用/发布配置 → 从本地 bare 仓库发布静态页到 web 组
bash lab/scripts/demo_release.sh
```

两个脚本均按**平台容器内实际运行的 v4.0.1 接口**编写（见 8.6 坑 2）。

- 发布源仓库：平台容器内 `/data/repos/demo-app.git`（bare，静态页 index.html v1.0，
  落在 `spug-repos` 命名卷持久化）。创建命令：

```bash
MSYS_NO_PATHCONV=1 docker exec -w /tmp spug sh -c '
mkdir -p /tmp/demo-app && cd /tmp/demo-app && git init -q -b main
printf "<!DOCTYPE html>\n<html><body><h1>OpsForge Demo App</h1><p>version: 1.0</p></body></html>\n" > index.html
printf "#!/bin/bash\n" > build.sh
git add -A && git -c user.email=opsforge@lab.local -c user.name=opsforge commit -qm "feat: demo static page v1.0"
git clone -q --bare /tmp/demo-app /data/repos/demo-app.git && rm -rf /tmp/demo-app'
```

- 发布产物验证：`docker exec web-01 ls /home/ops/demo/`（web-02 同）→ `index.html`、`build.sh`。

### 8.5 实测数据（2026-09-06）

| 项 | 值 |
|---|---|
| 节点运行状态 | 7/7 running，`systemctl is-active ssh` 全 active，hostname 正确 |
| 宿主端口 2221-2227 | 全部 open（/dev/tcp 探测） |
| SSH 登录 | 平台容器 paramiko 密码登录 7/7 成功；人工 ssh 可登录 |
| 平台纳管 | 7/7（web×2 / db×2 / cache×3 分组正确，状态全部"已验证"） |
| 批量执行 | `hostname && free -m` 7/7 成功，耗时 0.4s |
| Web 终端 | 双击主机打开会话，`ops@web-01:~$` 提示符 + 命令回显正常 |
| 应用发布 | 申请 id=5 发布成功，产物 2/2 节点落地 |
| 节点内存 | 每台 8.4~10.2 MiB，7 台合计 ≈62 MiB（限额 256MiB/台生效） |
| **总内存** | 平台 549 MiB（spug 455.6 + db 93.4）+ 节点 62 MiB ≈ **611 MiB**，预算 4.5GB 内（内存预算降级条款未触发） |

### 8.6 踩坑清单

| # | 坑 | 现象 | 处置 |
|---|---|---|---|
| 1 | 容器内 `free -m` 不是节点内存 | 显示 WSL2 宿主的 5.9GB | cgroup 限额不改变 free 视图；巡检报表中节点内存改读 `/sys/fs/cgroup/memory.current`（cgroup v2）或 docker stats |
| 2 | API 与本仓库源码不一致 | host 接口报「请选择主机分组」，工作区源码中查无此文案 | 容器运行 gitee 拉取的 v4.0.1：host 接口用独立分组表（先 POST `/api/host/group/` 建组，再传 `group_ids`）；工作区 tip 已改为 `zone` 字符串。**以「容器内实际运行代码」为接口核对基准** |
| 3 | Git Bash 下 curl 发中文 JSON 失败 | 平台报 `Invalid data type for parse` | Windows Git Bash 的中文按 GBK 编码发出，json.loads 解析 UTF-8 失败；lab 脚本所有 JSON body 一律用 ASCII |
| 4 | 应用 key 校验 | `demo-app` 被拒 | v4.0.1 要求 key 仅字母/数字/下划线 → 用 `demo_app` |
| 5 | 平台 JSON 响应带空格 | `"access_token": "..."`（冒号后有空格） | 脚本提取 token/id 时模式写 `[ ]*` 兼容；错误字段可能为 `null` 而非空串 |
| 6 | Web 终端打开主机无反应 | 单击树节点无会话 | v4.0.1 交互为**双击**叶子节点开会话（`nativeEvent.detail > 1`） |
| 7 | 发布申请创建响应无 id | POST 成功但 body 只有 `{"data":"","error":null}` | 从申请列表反查：列表按 `-id` 排序，首个元素即最新（`GET /api/deploy/request/`） |
| 7 | 官方镜像 VOLUME /data 无法撤销 | 以官方镜像为底 COPY 二开代码，被 Docker 自动挂的匿名卷遮蔽，容器 restart 循环（supervisord 报 logs 不存在） | **全量自建镜像**（基于官方构建文件，不声明 VOLUME，代码内置镜像层），见 §9 与 lab/docker/Dockerfile 头注 |
| 8 | docs/docker 全部文件 CRLF | 容器报 `exec /entrypoint.sh: no such file or directory`（shebang 带 \r）；core.autocrlf=true 且 .gitattributes 规则晚于文件恢复 | .gitattributes 补 `docs/docker/** text eol=lf`，rm + git checkout 重新落盘（blob 零变化） |
| 9 | bullseye 基底 apt 装不了 | debian-security 包 404（bullseye 2026-08 结束 LTS，源残缺） | 基底切 python:3.9-slim-bookworm（仍在支持期）；USTC 源 bookworm 为 deb822 格式（sed 路径 /etc/apt/sources.list.d/debian.sources） |
| 10 | ad-hoc 命令没有 recap | 状态判定报「退出码 0 无 recap」——recap 是 ansible-playbook 专属输出 | parse_output 双解析：recap 优先，回退逐主机结果行（`host \| SUCCESS/UNREACHABLE`）统计 |

## 9. Ansible 执行引擎运行形态（2026-09-09；决策记录见 ADR-0004）

平台镜像换为自建 **`opsforge-spug:4.0`**：基于官方构建文件（docs/docker/Dockerfile）改造
——依赖同官方 + `ansible-core==2.15.13`（Python 3.9 可用的最后版本线，2.16 起要求 3.10+；
sshpass 官方已带）+ pytest 工具链；上游代码与前端产物构建期固化（gitee v4.0.1 + CDN 包）；
OpsForge 二开（`ansible_ops` app + settings/urls 注册副本）随镜像 COPY。

```bash
# 构建（上下文 = 仓库根，白名单见 .dockerignore）与启动：命令不变
docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml build spug
docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml up -d

# 单元测试（仅容器内可跑；sqlite 内存库，工作区基线与镜像内 v4.0.1 不同源）
docker exec spug sh -c 'cd /data/spug/spug_api && python -m pytest apps/ansible_ops/tests'

# 执行引擎 API（鉴权同平台：x-token；nginx /api/ 前缀）
#   POST /api/ansible_ops/exec/   {host_ids:[], raw_module:{name,args}} → 202 {job_id}
#   GET  /api/ansible_ops/jobs/   分页列表；GET /api/ansible_ops/jobs/{id}/ 详情含 summary
```

- 执行记录 `ansible_jobs` 表；日志落容器内 `storage/ansible_logs/{job_id}.log`（spug-storage 卷持久）。
- 并发上限 2（约定，防 4.5GB 预算打爆）：redis 计数信号量 `spug:ansible:running`（gunicorn 多进程安全），
  单 job 内 `-f 10`。容器重启名额自然清零（redis 与 api 同容器）。
- **风险记录（2026-09-09 决策接受）**：ansible-core 2.15.13 存在 PYSEC-2026-3458
  （ansible-galaxy role install 参数注入 RCE，修复版 2.16+ 且 py3.9 不可用）。平台执行链路
  不调用 ansible-galaxy，攻击面不可达。**使用约束：禁止在平台容器内执行 ansible-galaxy
  安装第三方 role。**
- 后续改模型的迁移流程：容器内 `makemigrations` → `docker cp` 出 → `git add -f` → rebuild
  （migrations 被上游 .gitignore 忽略，运行时生成会导致容器重建后迁移状态漂移；构建期
  BuildKit 跑 makemigrations 会 segfault，仅在容器运行期执行）。
- 实测数据：7 节点 raw ping 两连跑 7/7 ok 且 summary 一致（幂等前置证明）；坏主机 →
  job=failed + 日志含 UNREACHABLE 详情；rebuild 后回归 7/7；pytest 27/27。

## 10. 实时回传、终止与前端页面（2026-09-11）

```
# 前端构建形态：镜像多阶段，node:18-alpine 阶段克隆 gitee v4.0.1 前端源码
# + lab/web-overlay/ 覆盖（新页面 + routes.js/config-overrides.js 注册副本）
# → npm install --legacy-peer-deps（npmmirror）→ npm run build → 覆盖 CDN tarball 产物。
# 工作区 spug_web/ 是上游 2.x lineage，不是运行前端的基线，勿在其上改。

# API 变更与新增（x-token 鉴权同平台）：
#   POST /api/ansible_ops/exec/               → 200 {job_id}（原定 202：spug 前端
#     http 封装把非 200 一律按失败弹出，故回归 200，偏差已记录记录待确认）
#   POST /api/ansible_ops/jobs/{id}/kill/     → 终止：running 走进程组 SIGTERM
#     （pid 经 redis spug:ansible:pid:{id} 跨 gunicorn worker 传递）；
#     pending 预置标记排队生效。终态 failed + summary {"reason":"killed"|"timeout"}

# WebSocket 实时回传：/api/ws/ansible_ops/jobs/{id}/?x-token=<32位token>
#   group = ansible-job-{id}；事件 {type:"log",line,ts} 逐行 + {type:"done",status,summary}；
#   中途连接先补发日志文件已有内容；已终态任务补发全量+done 后关闭（历史详情复用此通道回放）。
#   排查连接问题先看 nginx access log：WS 升级记为 "GET /api/ws/... 101"。

# 前端页面（浏览器访问）：
#   /ansible/exec  发起执行 + 实时滚动 + 运行中可终止
#   /ansible/jobs  历史列表 + 详情日志回放
```

- 前端产物无版本化 lockfile（上游无 package-lock.json）：rebuild 失败优先怀疑
  npmmirror 依赖漂移；npm 层缓存未失效时不受影响。
- **改前端后验证必看 bundle 版本**：nginx 不给 index.html 发 no-cache，浏览器启发式
  缓存整页加载且 SPA 内部导航不换 bundle——用带 query 的 URL 强刷或换 origin
  （127.0.0.1），并 grep 产物特征串确认（如 `grep -c ansible/exec build/static/js/main.*.js`）。
- kill 后残留 `ssh ... [mux]` 进程为 ansible ControlPersist 复用 master（设计行为，
  空闲后自退），非泄漏；pytest 42/42（的 27 + 本次任务 15）。

## 11. Playbook 管理与 git 版本化（2026-09-13）

# 数据面：REPOS_DIR/ansible_ops/playbooks/ 即 git 仓库（单仓库多文件，挂 spug-repos 卷
# 持久），文件 {id}.yml；模型 ansible_playbooks.content 与文件互为镜像，写路径统一走
# repo.write_and_commit（内容归一化 LF 后同时落文件与 DB）。容器内手工检查：
#   cd /data/repos/ansible_ops/playbooks && git log --pretty=format:'%h %an %s'
# 提交作者 = 操作人昵称（审计），提交者固定 OpsForge <opsforge@local>；镜像内无全局
# git 身份，GitPython 必须显式传 author/committer Actor。
# 保存/编辑/回滚前先 ansible-playbook --syntax-check（60s 超时；PYTHONWARNINGS=ignore
# 防止 paramiko 弃用警告混入返回给前端的报错文本）。
# 版本历史 API 每条 commit 附 git show 的 patch 文本（前端渲染实现"diff 可见"）；
# 回滚 = 取旧 commit 的 blob 内容重新提交为新 commit（历史线性，不丢后续记录）。
#   GET  /api/ansible_ops/playbooks/                列表/创建（POST）
#   GET/PUT/DELETE /api/ansible_ops/playbooks/{id}/ 详情/编辑/删除
#   GET  /api/ansible_ops/playbooks/{id}/versions/  commit 列表（含 patch）
#   POST /api/ansible_ops/playbooks/{id}/rollback/  body {commit}
# exec 联动：POST /api/ansible_ops/exec/ 的 playbook_id 即 Playbook.id，执行命令
# ansible-playbook -i <动态inventory> -f 10 /data/repos/ansible_ops/playbooks/{id}.yml；
# 排队期间 Playbook 被删（FK SET_NULL）→ 任务落 failed，summary.reason 可读。
# 前端：/ansible/playbooks 管理页（ACEditor yaml 编辑、版本抽屉含 diff/回滚）；
# /ansible/exec 页执行类型切换，/ansible/exec?playbook=<id> 从管理页直达。
# 踩坑：
# - GitPython repo.git.show() 字符串输出会去末尾换行，字节级内容必须走对象 API
#   （commit.tree / rel → data_stream.read()）。
# - Python 3.9 Path.write_text 无 newline 参数（3.10 才有），且其语义是"不翻译"
#   而非归一化，CRLF 要手动 replace；spug JsonParser 会 strip 首尾，API 入库内容无尾换行。
# - git 仓库根就是 playbooks/ 目录本身：git add 用绝对路径，show sha:path 用仓库相对路径。
# - 迁移文件被上游 .gitignore(migrations/) 忽略：提交用 git add -f（T1 先例）。
# 实测记录（2026-09-14 凌晨）：smoke-ping 三版两改 → 4 commits
# （作者"管理员"）→ diff 渲染正确 → 回滚 76a810b 内容字节级回退；坏 YAML 拒保存
# 且 toast 展示 ansible ERROR 原文；管理页「执行」直达 exec 页联动 Job#24 真实
# ping web-01 成功（recap ok=2 failed=0，WS 实时回传正常）。pytest 77/77 容器内全绿。

## 12. Playbook 库与幂等验证（2026-09-16）

> 数据与踩坑完整版：本节 + experiments/2026-10-02-full-replay.md

### 运行形态

```
lab/playbooks/（仓库源，git 入库）
   ├─ 4 个主 playbook ── import_playbooks.sh ──> 平台 Playbook 库（id 10-13，
   │    syntax-check + git 版本化，平台内可编辑/回滚）
   └─ group_vars/host_vars/templates ── docker cp ──> REPOS 卷
        （/data/repos/ansible_ops/playbooks/，ansible-playbook 自动加载
          playbook 同目录的这三个子目录，容器内已实测）
执行：POST /api/ansible_ops/exec/ {host_ids, playbook_id, extra_vars}
口令：group_vars 实验弱口令默认值 + extra_vars 覆盖（优先级最高）
```

### 四个 Playbook 与实测数据

| Playbook | 目标节点 | 首跑（job） | 幂等二跑（job） | 关键结果 |
| --- | --- | --- | --- | --- |
| baseline | 全 7 台 | 54（45 changed） | 55（0） | 时区 CST、PermitRootLogin no、chrony masked、sysctl 3 参数生效 |
| nginx | web-01/02 | 40（10） | 41（0） | handler「优雅重载」触发留证；X-Node/节点名渲染 |
| mysql-master-slave | db-01/02 | 46（33s） | 47（0） | 双 Yes、server-id 101/102、主建表从可查；内存 123MiB |
| redis-sentinel | cache-01/02/03 | 50（20s） | 51（0） | failover：检出→切换 1.1s（sentinel 日志链）；旧主自动转 replica |

手工 vs Playbook 对比（验收 1 数据）：手工逐台搭主从净耗时 ~60s 且踩 3 坑
（无 /var/log/mysql、-N 参数误判复制状态、policy-rc.d 不自启），含重试 ≈100s；
Playbook 一键 33s 一次通过且可无限重复——**价值不在快，在可重复 + 幂等 + 零遗漏**。

### 节点重置与重新纳管（节点重建后必读）

节点 force-recreate 后 host key 与 authorized_keys 重置：平台 known_hosts 旧指纹冲突
+ 公钥丢失 → 任务 unreachable。恢复：`ssh-keygen -f /root/.ssh/known_hosts -R <节点>`
+ 平台「编辑主机」带 password 提交（接口 `_do_host_verify` 重发公钥）。

### failover 恢复姿势

failover 后重跑 redis-sentinel.yml 不能回到声明拓扑（sentinel.conf 被自改为监控新
master，直接重跑还与故障转移竞态，job 52 partial 如实记录）。恢复 = 重置 cache 节点
后重跑（job 53，20s 一键重建并恢复 cache-01 主）。

### 踩坑（新增）

- `ansible.builtin.sysctl` 不在 ansible-core 2.15（community.general 才有，平台零
  collections）→ 模板持久化 + 逐参数比对后 `sysctl -w`。
- privileged 容器 dbus 未运行，`timedatectl` 不可用 → /etc/localtime 符号链接方案。
- `mysql -NBe "SHOW SLAVE STATUS\G"`：-N 去列名与 \G 组合后 grep 字段名必空；
  `grep 'Slave_(IO|SQL)_Running'` 会多匹配 `Slave_SQL_Running_State`——匹配加尾随 `: `。
- Debian 12 redis 包无 redis-sentinel.service/二进制 → playbook 自建 systemd unit。
- MSYS 路径转换破坏 docker exec/cp 的容器路径 → `MSYS2_ARG_CONV_EXCL="/tmp;/data"`。

## 13. 批量巡检报表（2026-09-19）

### 13.1 运行形态

```bash
# 手动触发（或页面「Ansible 自动化 → 巡检报表 → 立即巡检」）
curl -s -X POST "http://localhost/api/ansible_ops/inspection/run/?x-token=$TOKEN"
# → {"data": {"report_id": N}}；列表 GET /inspections/、产物
# GET /inspections/{id}/files/{html|xlsx|manifest}/[?dl=1]（?x-token= 鉴权）

# CLI 单轮（调试）
docker exec spug sh -c 'cd /data/spug/spug_api && python manage.py run_inspection'
```

- **定时**：supervisor program `opsforge-inspection` 常驻 `run_inspection --loop`
  （apscheduler，间隔 `OPSFORGE_INSPECTION_INTERVAL` 秒默认 300；启动即跑首轮，
  重启自动把遗留 running 轮落 failed）。supervisor 增量段在 lab/docker/Dockerfile
  追加，上游 docs/docker/spug.ini 原件未动；选 supervisor 而非 crontab 的理由见踩坑记录。
- **采集通道**：复用引擎动态 inventory + `ansible all -m shell -b`，6 检查合并一条
  采集脚本（base64 拼单行 JSON），7 节点单轮 ~1s；并发名额与手动执行共享
  MAX_JOBS=2（采集阶段排队最多 120s）。
- **检查协议**：`CHECKS = {name: fn}`，`check(runner, host) -> CheckResult(ok|warn|
  crit)`，新增检查只需在 `inspection/checks.py` 注册；阈值常量在同文件头部。
- **产物**：`storage/reports/inspection-{report_id}-{ts}.{html|xlsx|manifest.json}`；
  diff 基线 = 最近一条成功轮 manifest。阈值口径：内存/磁盘 >85 warn >95 crit；
  PSI avg60 >20 warn >50 crit；证书剩余 <30d warn <7d crit；端口检查仅对装了
  nginx 的节点生效（80 的监听者必须为 nginx）。

### 13.2 实测数据（2026-09-19）

| 轮次 | 触发 | 结果 | 耗时 | 说明 |
|---|---|---|---|---|
| report 8 | manual | 42 ok | ~1s | 健康基线轮 |
| report 9 | manual | 37 ok / **5 crit** | ~1s | 3 类故障 3/3 检出 |
| report 10 | manual | 42 ok | ~1s | 恢复轮，diff 记录 crit→ok ×5 |
| report 11 | scheduled | 42 ok | ~2s | 定时器自动轮（300s 周期） |

- 故障注入：db-01 tmpfs 写满（disk）/ web-01 停 nginx（process+port）/ web-02
  python3 占 80（port 被占用 + process）。明细与注入命令见
  `experiments/2026-10-02-full-replay.md`。
- pytest 100/100（77 既有 + 23 新增）；本次任务零新增依赖，openpyxl（上游既有）
  pip-audit 无已知漏洞。

### 13.3 踩坑清单（新增）

- ansible shell 结果行解析必须吃满整行 + re.M（残片毁 JSON / 漏主机），详见
  experiments 踩坑 1-3（采集脚本 JSON 引号配对、base64 尾换行）。
- v4.0.1 运行容器 settings 无 TEMPLATES（上游后端零模板渲染）→ 用独立
  `django.template.Engine(dirs=[...])`，别动上游 settings。
- USE_TZ=False（运行容器）：`timezone.localtime()` 对 naive 抛异常；证书到期一律
  aware UTC 计算不受 settings 影响。
- 节点巡检口径：/proc/loadavg、/proc/meminfo 在容器里是宿主值——内存读 cgroup v2
  （memory.max/current），CPU 读 cpu.pressure PSI。
- `pkill -f <关键字>` 会匹配 docker exec 自身命令行（自杀 exit 143）。
- pytest-django 事务 + 真线程池 = 独立连接看不见夹具数据 → 测试把
  `_pool.submit` mock 成同步。

## 14. 告警闭环自愈（2026-09-21）

### 14.1 架构与挂点

- **挂点零侵入**：`post_save(Alarm)` signal（`ansible_ops/heal/signals.py`，注册于
  `apps.py ready()`）。上游 monitor 的告警落库点 `handle_notify → Alarm.objects.create`
  即事件源，上游文件零改动。信号只认 `status='1'`（报警发生），恢复告警不触发。
- **闭环线程**：signal 同步段只做匹配+去重；闭环主体（前复检 → playbook →
  后复检 → 判定 → 外发）在 daemon 线程，不占 monitor worker 线程池配额。
- **复检复用 INSP 单项插件**：`run_collection([host])` 单主机采集 + `CHECKS[name]`
  单项判定，与巡检报告同口径。
- **自愈执行复用执行引擎**：进程内建 AnsibleJob + `try_acquire_slot`（与手动
  执行/巡检共享 MAX_JOBS=2）+ `submit_job`，同步轮询终态（300s 预算）。
- **防自愈风暴三道闸**：冷却窗口 / 闭环进行中去重 / 前复检已恢复则不跑 playbook。

### 14.2 验证核对清单（实测过）

1. `HealRule` CRUD：`POST /api/ansible_ops/heal-rules/`（keyword/host/playbook/
   check_name/cooldown，check_name 校验必须在 CHECKS 注册表内）。
2. 注入 `docker exec web-01 systemctl stop nginx` → 告警（worker.log
   "send fault alarm notification"）→ HealEvent running → playbook job →
   复检 crit→ok → recovered + 双渠道 webhook 消息（含 job 号）。
3. 端到端 12.3s–67.1s（6 样本 4/6 ≤60s；瓶颈=1 分钟检测相位，闭环段恒定
   2.1–3.4s）；2026-10-02 从零重建后复跑端到端 30s（闭环 3.2s）。详见
   experiments/2026-10-02-full-replay.md。
4. 冷却抑制：恢复后 57s 内再注入 → suppressed（不建 job、不自愈）；冷却过期后
   下一轮告警正常自愈（故障不会因冷却无人认领）。
5. 失败升级路径单测覆盖（job 失败 / 复检未恢复 / 名额满）。

### 14.3 踩坑与建议

- **signal sender 字符串用 app_label**：`'alarm.Alarm'` 而非 `'apps.alarm.Alarm'`
  ——INSTALLED_APPS 是 `apps.alarm`，label 取最后段；写错启动即炸
  `makemigrations --check`。
- **JsonParser 未传字段填 None**：`create(**form)` 会把 None 显式塞给
  非空字段（NOT NULL failed）；PUT 部分更新要 `if v is not None` 过滤，
  否则把没传的字段写 NULL。
- **Argument 加 default 后 help 会丢**：filter 校验失败的报错退化成
  "Value Error: check failed"——help 参数必须同在。
- **测试外发静音**：heal 单测若不 mute `notify_heal_report`，测试事件会经
  HTTP 打到 `heal_webhook.env` 指向的真实渠道（实测漏出 20 条到 mock）。
  mute fixture 必须 autouse。
- **watch 类观察脚本的窗口口径**：按 `created_at >= now-90s` 过滤会捞到上一轮
  的旧终态事件假返回（实测 round3/4 数据作废）；改为"只认本进程启动后创建的
  事件"（started 时刻过滤）。另：MSYS 下 `docker exec spug python3 /tmp/x.py`
  必须 `MSYS2_ARG_CONV_EXCL="/tmp;/data"`，否则路径被转换成 Windows 形态且
  stderr 一吞就静默失败。
- **IAB fullPage 截图有双列伪影**：消息页用高视口（1400）+ 普通 viewport 截图。
- **mock 读取与运行**：容器内 `docker exec -d spug python3 /tmp/mock_webhook.py`，
  消息落 `/tmp/heal-mock/`；webhook URL 从 `heal_webhook.env` 读（env 优先，
  文件兜底——supervisor 常驻进程吃不到运行期注入的 env，文件是实验场景的
  覆盖通道）。
