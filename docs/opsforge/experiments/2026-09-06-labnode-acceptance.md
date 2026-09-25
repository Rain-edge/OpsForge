# 2026-09-06 节点实验环境验收与内存实测

## 目的

1. 验证 7 节点实验环境满足节点实验环境五条验收标准。
2. 实测「平台 + 节点」总内存，验证 内存预算（7 节点 × 256MB + 平台 ≈2GB ≤ 4.5GB），
   判断是否触发「超 4.5GB 自动降级 cache×2」条款。

## 环境

- 宿主：Windows 11 + Docker Desktop（WSL2 后端，memory=6GB / processors=4）
- 平台：`spug`（openspug/spug-service:4.0）+ `spug-db`（mariadb:10.8），compose up 于 09:43
- 节点：labnode:1.0 × 7（debian:12-slim + systemd + sshd，privileged，mem_limit=256m）

## 步骤与原始输出

### 1. 节点起跑与 systemd 验证

```bash
cd lab/nodes && cp .env.example .env && docker compose up -d --build
for n in web-01 web-02 db-01 db-02 cache-01 cache-02 cache-03; do
  echo -n "$n: systemctl=$(docker exec $n systemctl is-active ssh) hostname=$(docker exec $n hostname)"; echo
done
```

输出（全部一致）：

```
web-01: systemctl=active hostname=web-01
web-02: systemctl=active hostname=web-02
db-01: systemctl=active hostname=db-01
db-02: systemctl=active hostname=db-02
cache-01: systemctl=active hostname=cache-01
cache-02: systemctl=active hostname=cache-02
cache-03: systemctl=active hostname=cache-03
```

### 2. 平台容器 → 节点 SSH（密码通道，纳管前验证）

```bash
docker exec spug python3 -c "..."   # paramiko 逐台 connect(web-01..cache-03, 22, 'ops', <OPS_PASSWORD>)
```

输出：`7/7 OK`，各节点返回 hostname 与 free -m（节选）：

```
web-01 OK | web-01 / Mem: 5927 1361 3937 26 838 4565
...
cache-03 OK | cache-03 / Mem: 5927 1364 3934 26 838 4562
```

> 注：`free -m` 显示 5927MB = WSL2 宿主内存，非节点 cgroup 限额（cgroup 限制不改变
> free 视图）。此现象已写入 RUNBOOK 坑 1 与 ADR-0002。

### 3. 纳管（register_hosts.sh，幂等验证）

首次：7/7 `[OK]`，分组 web/db/cache 自动创建（id=2/3/4）。
重跑：分组 `[SKIP]` + 7/7 `[SKIP]`（幂等成立）。
UI 复核：主机管理页分组树 cache=3 / db=2 / web=2，7 台状态全部「已验证」。

### 4. 批量执行（UI 实测）

执行任务页全选 7 台（分 web/db/cache 三组勾选）→ `hostname && free -m`：

```
成功 7 / 失败 0，耗时 0.4 秒
web-01(web-01:22) ... cache-03(cache-03:22) 全部绿勾
```

### 5. Web 终端（UI 实测）

双击 web-01 打开会话：`ops@web-01:~$` 提示符出现；键入命令后 bash 回显
（WS 消息流记录：`\u001b[7mwhoami && hostname && uptime\u001b[27m`）。

### 6. 应用发布（demo_release.sh）

```
[REQ]  发布申请已创建 (id=5)
[EXEC] 发布已触发，轮询结果 ...
== 发布成功（申请 id=5）==
```

产物落地验证：

```
== web-01 ==  build.sh, index.html (version: 1.0)
== web-02 ==  build.sh, index.html (version: 1.0)
```

### 7. 内存实测（docker stats --no-stream）

```
cache-01    8.684MiB / 256MiB      cache-02    7.441MiB / 256MiB
cache-03    8.387MiB / 256MiB      db-01       8.734MiB / 256MiB
db-02       9MiB / 256MiB          web-01      10.16MiB / 256MiB
web-02      9.629MiB / 256MiB      spug        455.6MiB
spug-db     93.36MiB
```

## 结论

| 验收标准 | 结果 |
|---|---|
| 1. 7 节点 running + ssh 可登录 | ✅ 7/7（systemctl active + 密码登录 OK） |
| 2. 平台纳管 7/7，分组一致 | ✅（web×2/db×2/cache×3，全部已验证） |
| 3. 批量执行 7/7 + Web 终端可用 | ✅（0.4s；终端会话与命令回显正常） |
| 4. 应用发布演示留记录 | ✅（申请 id=5，产物 2/2 节点落地） |
| 5. 总内存 ≤4.5GB 记入 RUNBOOK | ✅ 平台 549MiB + 节点 62MiB ≈ **611MiB** |

**内存预算降级条款未触发**（611MiB 远低于 4.5GB），7 节点配置维持不变。
内存余量主要供 Playbook 库在节点内部署 MySQL/Redis 使用；256MB/节点下部署中间件需
调小 InnoDB buffer 等参数，已记入 ADR-0002 后果节。

## 下一步

- 解决「容器运行代码 ≠ 工作区代码」（挂载或自建镜像，ADR-0001），在工作区
  代码基础上开发 ansible_ops app；接口核对基准 = 容器内实际运行代码。
- W3 Keepalived 实验若受 VRRP 多播限制按预案降级（ADR 记录）。
