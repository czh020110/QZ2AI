# 合并容器：FastAPI + nginx + coscli + docker CLI + supervisord
# supervisord 管理 nginx + uvicorn 两个进程；syncer 为 Python 后台线程

FROM python:3.12-slim

# 使用腾讯云镜像源加速 apt
RUN sed -i 's|deb.debian.org|mirrors.tencentyun.com|g' /etc/apt/sources.list.d/debian.sources

# 安装 nginx + curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx curl && rm -rf /var/lib/apt/lists/*

# 安装 docker CLI + compose 插件（多阶段从 docker:27-cli 复制）
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:27-cli /usr/local/libexec/docker/cli-plugins/docker-compose \
    /usr/local/libexec/docker/cli-plugins/docker-compose

# 安装 coscli（腾讯云 COS 命令行工具，Go 单二进制）
RUN curl -fsSL https://cosbrowser.cloud.tencent.com/software/coscli/coscli-linux-amd64 \
    -o /usr/local/bin/coscli && chmod +x /usr/local/bin/coscli

WORKDIR /app

# Python 依赖（含 supervisor 进程管理器）
ARG PIP_INDEX_URL=http://mirrors.tencentyun.com/pypi/simple
ARG PIP_TRUSTED_HOST=mirrors.tencentyun.com
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_INDEX_URL=${PIP_INDEX_URL} PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    pip install --no-cache-dir -r requirements.txt supervisor

COPY app ./app

# nginx 配置：覆盖默认站点
COPY nginx/default.conf /etc/nginx/sites-available/default
COPY nginx/proxy_common.conf /etc/nginx/proxy_common.conf
COPY nginx/nginx-wrapper.sh /nginx-wrapper.sh
RUN chmod +x /nginx-wrapper.sh \
    && rm -f /etc/nginx/sites-enabled/default \
    && ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default

# supervisord 配置
COPY supervisord.conf /etc/supervisor/conf.d/blog.conf

EXPOSE 80

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/blog.conf"]
