# labnode — OpsForge 实验被管节点基础镜像
# 设计：systemd（可用 systemctl）+ sshd（密码/公钥双认证，纳管瞬间
# 由平台经密码通道自动分发公钥）+ Ansible 目标机硬需求 python3
# + 常用排障工具。统一实验用户 ops（口令不进仓库：entrypoint 从 OPS_PASSWORD 注入）。
FROM debian:12-slim

# apt 国内源（debian 12 为 deb822 格式）
RUN sed -i 's@deb.debian.org@mirrors.ustc.edu.cn@g' /etc/apt/sources.list.d/debian.sources

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv openssh-server python3 python3-apt \
        curl wget vim less procps iproute2 iputils-ping net-tools lsof rsync sudo \
    && rm -rf /var/lib/apt/lists/*

# 统一实验用户：ops（sudo 免密，供 Ansible become 与日常排障）
RUN useradd -m -s /bin/bash ops \
    && echo 'ops ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ops \
    && chmod 440 /etc/sudoers.d/ops

# sshd drop-in：禁 root、开密码（仅纳管瞬间使用）与公钥认证
RUN printf 'PermitRootLogin no\nPasswordAuthentication yes\nPubkeyAuthentication yes\n' \
        > /etc/ssh/sshd_config.d/99-labnode.conf

# host key 留到容器首启生成（entrypoint ssh-keygen -A），避免 7 节点共享同一指纹
RUN rm -f /etc/ssh/ssh_host_*

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# systemd 正确停机信号
STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
