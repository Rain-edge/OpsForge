# OpsForge：FRR 网络设备实验镜像，官方 frrouting/frr 派生。
# 原版镜像无 sshd（which sshd 为空已实测），netmiko 需 SSH 登录采集，
# 故增装 openssh-server；netmiko 用 linux 驱动 + vtysh 采 show running-config
# （口径：商业镜像 cEOS/IOL 有授权问题不用）。
# zebra 与 staticd 由官方 docker-start 强制启动（frrcommon.sh daemon_list
# 特判），静态路由开箱可用，无需改 daemons。
# tag 可复现性：latest 构建时 digest sha256:990e8349...（Alpine 3.16.2 /
# FRR 8.4_git）；上游漂移后重建前先 docker pull 复核 vtysh/docker-start 行为。
FROM frrouting/frr:latest

# Alpine 3.16 底座（官方镜像），apk 换 USTC 源（同平台镜像 web-builder 做法）
RUN sed -i 's@dl-cdn.alpinelinux.org@mirrors.ustc.edu.cn@g' /etc/apk/repositories \
    && apk add --no-cache openssh-server \
    && sed -ri 's/^#?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# 运维账号（口令由 entrypoint 从 env 注入，与被管节点 OPS_PASSWORD 同源约定）；
# 加入 frr/frrvty 组才能以非 root 运行 vtysh（vtysh.sock 属组权限要求）
RUN adduser -D -s /bin/sh ops \
    && adduser ops frr \
    && adduser ops frrvty

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
