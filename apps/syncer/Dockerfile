# 多阶段构建：docker CLI + coscli + bash 守护进程
# syncer 容器职责：轮询 API 同步状态，触发 coscli sync + reindex + Quartz 重建

# 阶段一：获取 docker CLI + compose 插件
FROM docker:27-cli AS docker-cli

# 阶段二：运行时（需要 python3 解析 .env 和 JSON，需要 curl 调 API）
FROM python:3.12-slim

# 安装 curl
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 docker CLI
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

# 安装 docker compose 插件
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose \
    /usr/local/libexec/docker/cli-plugins/docker-compose

# 安装 coscli（腾讯云 COS 命令行工具，Go 单二进制）
RUN curl -fsSL https://cosbrowser.cloud.tencent.com/software/coscli/coscli-linux-amd64 \
    -o /usr/local/bin/coscli && chmod +x /usr/local/bin/coscli

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
