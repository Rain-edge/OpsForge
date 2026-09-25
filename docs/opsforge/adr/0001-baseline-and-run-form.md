# ADR-0001：仓库基线与平台运行形态

- 状态：已接受
- 日期：2026-09-01

## 背景

OpsForge 以 openspug/spug 为基线二开。开工时面临两个决策：

1. **基线取哪个点**：`v4.0.1` tag 还是 `upstream/master` tip（tag 之后还有 77 个提交）；
2. **本地怎么跑平台**：上游 v4.0.1 官方 docker 形态是预构建镜像 `openspug/spug-service:4.0`
   （内含 Python/Django 4.2 运行环境，不含代码），容器首启自己从 gitee clone
   `SPUG_DOCKER_VERSION` 指定版本的代码 + 从 cdn.spug.cc 下载前端产物。且 `docs/docker/`
   （compose 与构建文件）**在 master tip 已被上游删除**，仅 v4.0.1 tag 保留。

## 决策

1. **基线 = 开工时 upstream/master tip（`50b2838`），tag v4.0.1 作为参考锚点**。tip 与
   v4.0.1 之间 77 个提交为 README 更新与少量前端优化，无破坏性变更；取 tip 让后续
   rebase 上游更线性。
2. **`docs/docker/` 从 v4.0.1 tag 整目录恢复入库**（`git checkout v4.0.1 -- docs/docker/`）：
   compose 是运行基，Dockerfile/entrypoint 等构建文件留给执行引擎自建镜像时使用；
   恢复的文件保持原样禁改，本地适配全部进 `docker-compose.override.yml`。
3. **运行形态 = 官方 compose + 官方预构建镜像**，数据挂载由 override 改为命名卷
   （官方绝对路径在 Docker Desktop 会落进 VM 内部）。此阶段不做镜像定制。

## 后果

- ✅ 平台一键起、可复现，初始化与登录全流程已实测进 RUNBOOK。
- ⚠️ **容器运行的是 gitee 拉取的 v4.0.1 代码，与本仓库工作区的二开代码暂不联动**。
  这是已知且有意的取舍：先验证基线可运行；执行引擎（加装 ansible-core）必须先解决
  「本仓库代码进容器」——要么挂载工作区到 `/data/spug`，要么基于官方 Dockerfile 自建
  含 ansible 的镜像（最终走后者，见 ADR-0004）。
- ⚠️ 上游 tip 未来可能再动 `docs/` 布局，rebase 时留意 `docs/docker/` 无冲突
  （tip 已删该目录，本仓库恢复为新增文件，理论无冲突）。
