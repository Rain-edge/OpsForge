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
| 8 | json_response 成功格式 | `{"data":"", "error":""}`；部分接口成功返回 `"error": null` | 判断成功需同时兼容两种 error 取值 |
