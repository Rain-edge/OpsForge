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
