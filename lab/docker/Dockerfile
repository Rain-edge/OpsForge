# OpsForge 运行镜像：基于官方构建文件（docs/docker/Dockerfile，v4.0.1 版）全量自建。
# 不以 openspug/spug-service:4.0 为底叠加，原因：官方镜像声明 VOLUME /data 且无法在
# 派生镜像中撤销，代码只能走数据卷（copy-on-first-use，镜像更新后卷内代码不随更新）；
# 自建镜像不声明 VOLUME，/data/spug 即镜像层，storage/repos 显式挂卷持久化。
# 与官方镜像的差异：①pip 增装 ansible-core（执行引擎）与 pytest（测试）；
# ②上游代码与前端产物构建期内置（与官方 entrypoint 首启 clone/下载同源同版本）；
# ③OpsForge 二开（ansible_ops app 与注册文件）随镜像 COPY。
# 构建上下文为仓库根：docker build -f lab/docker/Dockerfile .
# 前端构建阶段：v4.0.1 前端源码（gitee）+ OpsForge overlay（lab/web-overlay
# 覆盖同名文件/新增页面）。工作区 spug_web 是 2.x lineage（antd 3）与运行时 v4.0.1（antd 4）
# 不同源，不可用作改动基线。node 18 配合 package.json scripts 内置的
# --openssl-legacy-provider（react-scripts 3.4.3 老依赖树需 --legacy-peer-deps 安装）
FROM node:18-alpine AS web-builder
RUN sed -i 's@dl-cdn.alpinelinux.org@mirrors.ustc.edu.cn@g' /etc/apk/repositories \
    && apk add --no-cache git
RUN git clone -q -b v4.0.1 https://gitee.com/openspug/spug.git /build \
    && rm -rf /build/spug_api /build/.git
COPY lab/web-overlay/ /build/spug_web/
WORKDIR /build/spug_web
RUN npm config set registry https://registry.npmmirror.com \
    && npm install --legacy-peer-deps \
    && npm run build

# 基底 python:3.9-slim-bookworm：官方构建文件用 bullseye 是为兼容旧内核宿主（seccomp
# 对 clone3 的拦截），本机 Docker Desktop 29.7.2 无此顾虑；bullseye 已于 2026-08 结束
# LTS，security 源残缺 404，故切仍在支持期的 bookworm（对实验环境无行为差异）
FROM python:3.9-slim-bookworm

ENV TZ=Asia/Shanghai

# apt 国内源（USTC，同节点实验环境做法）：bookworm 为 deb822 格式
RUN sed -i 's@deb.debian.org@mirrors.ustc.edu.cn@g' /etc/apt/sources.list.d/debian.sources

# 与 docs/docker/Dockerfile 官方 apt 列表一致；ansible-core 锁 2.15 线：容器 Python 3.9
# 可用的最后版本线（2.16 起要求 3.10+）。sshpass 已在列表内（口令分发/文件分发用）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        redis-server \
        supervisor \
        git \
        curl \
        wget \
        unzip \
        gcc \
        pkg-config \
        default-libmysqlclient-dev \
        libldap2-dev \
        libsasl2-dev \
        libssl-dev \
        iputils-ping \
        net-tools \
        procps \
        sshpass \
        rsync \
        sshfs \
        locales \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 版本与 docs/docker/Dockerfile 保持一致（官方镜像同款预装），单列一层便于缓存
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    apscheduler==3.10.4 \
    "Django>=4.2.0,<4.3.0" \
    paramiko==3.4.0 \
    "channels>=4.0.0,<5.0.0" \
    "daphne>=4.0.0,<5.0.0" \
    "gunicorn>=21.0.0" \
    "channels-redis>=4.1.0,<5.0.0" \
    "django_redis>=5.4.0,<6.0.0" \
    asgiref==3.7.2 \
    "requests>=2.31.0,<3.0.0" \
    python-ldap==3.4.3 \
    GitPython==3.1.40 \
    openpyxl==3.1.2 \
    user_agents==2.2.0 \
    mysqlclient \
    cryptography

# OpsForge 增量依赖（增 netmiko：设备配置备份，容器内实测 4.6.0 兼容既有 paramiko 3.4）
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    ansible-core==2.15.13 \
    netmiko==4.6.0 \
    pytest==8.3.4 \
    pytest-django==4.9.0

RUN sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# 上游运行配置：docs/docker/ 原件原样 COPY（上游禁改文件，不经修改直接入镜像）
COPY docs/docker/init_spug /usr/bin/
COPY docs/docker/nginx.conf /etc/nginx/
COPY docs/docker/ssh_config /etc/ssh/
COPY docs/docker/spug.ini /etc/supervisord.conf
COPY docs/docker/redis.conf /etc/
COPY docs/docker/entrypoint.sh /

RUN chmod +x /usr/bin/init_spug /entrypoint.sh

# 上游运行代码 v4.0.1 + 前端产物（与官方 entrypoint 首启 clone/CDN 下载同源）；
# 代码已内置，entrypoint 的首启准备分支自动跳过
RUN git clone -q -b v4.0.1 https://gitee.com/openspug/spug.git /data/spug \
    && mkdir -p /data/spug/spug_web \
    && curl -fsSL https://cdn.spug.cc/spug/web_v4.0.1.tar.gz -o /tmp/web.tar.gz \
    && tar xf /tmp/web.tar.gz -C /data/spug/spug_web/ \
    && rm -rf /tmp/web.tar.gz /data/spug/.git

# OpsForge 前端产物：用 overlay 构建产物覆盖 CDN tarball 的官方产物
# （含新增 Ansible 页面与菜单；tarball 产物先清空再覆盖，防旧哈希文件残留）
RUN rm -rf /data/spug/spug_web/build
COPY --from=web-builder /build/spug_web/build /data/spug/spug_web/build/

# OpsForge 二开：app 代码（含入库迁移文件）+ settings/urls/routing 注册（对 v4.0.1 原件
# diff 仅注册行）+ overrides（数据库配置 env 化）。迁移文件治理：上游 .gitignore
# 忽略 migrations/（运行时生成会导致容器重建后迁移状态漂移），本 app 迁移文件在
# 容器内生成后 docker cp 出来 git add -f 入库，随镜像固化。
COPY spug_api/apps/ansible_ops /data/spug/spug_api/apps/ansible_ops/
COPY spug_api/pytest.ini /data/spug/spug_api/pytest.ini
COPY spug_api/spug/test_settings.py /data/spug/spug_api/spug/test_settings.py
COPY lab/docker/spug_settings.py /data/spug/spug_api/spug/settings.py
COPY lab/docker/spug_urls.py /data/spug/spug_api/spug/urls.py
COPY lab/docker/spug_routing.py /data/spug/spug_api/consumer/routing.py
COPY lab/docker/spug_overrides.py /data/spug/spug_api/spug/overrides.py

RUN mkdir -p /data/repos /var/log/supervisor /run/nginx \
    /data/spug/spug_api/storage/ansible_logs \
    /data/spug/spug_api/storage/reports \
    /data/spug/spug_api/storage/netbackup

# OpsForge 定时巡检调度：向 supervisor 追加常驻 program（内部
# apscheduler 按 OPSFORGE_INSPECTION_INTERVAL 秒触发，默认 300）。上游
# docs/docker/spug.ini 原件不动，增量只在此追加段；选 supervisor 而非 crontab
# 的理由（crontab 不继承容器 env 的 MySQL 口令等）见踩坑记录
RUN printf '\n[program:opsforge-inspection]\ncommand = python manage.py run_inspection --loop\ndirectory = /data/spug/spug_api\nautostart = true\nstartsecs = 3\nstdout_logfile = /data/spug/spug_api/logs/inspection.log\nredirect_stderr = true\n' >> /etc/supervisord.conf

# OpsForge 网络探测与设备备份调度：探测 30s / 备份 120s 各一 job
# （间隔 env OPSFORGE_NET_PROBE_INTERVAL / OPSFORGE_NET_BACKUP_INTERVAL 可调）
RUN printf '\n[program:opsforge-net]\ncommand = python manage.py run_net --loop\ndirectory = /data/spug/spug_api\nautostart = true\nstartsecs = 3\nstdout_logfile = /data/spug/spug_api/logs/net.log\nredirect_stderr = true\n' >> /etc/supervisord.conf

EXPOSE 80
ENTRYPOINT ["/entrypoint.sh"]
