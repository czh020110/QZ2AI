# syntax=docker/dockerfile:1
# FastAPI 后端镜像。瘦身优先：slim 基础 + 仅装 requirements。
# 数据（chroma/notes/docstore）通过卷挂载到 /data，不打进镜像。

FROM python:3.12-slim

WORKDIR /app

# 先装依赖，利用 Docker 层缓存：requirements 不变则跳过重装
# pip 默认使用腾讯云内网镜像源，构建快且不依赖外网。
# --mount=type=cache 持久化 pip 下载目录，requirements 变更时只增量下载差异包。
ARG PIP_INDEX_URL=http://mirrors.tencentyun.com/pypi/simple
ARG PIP_TRUSTED_HOST=mirrors.tencentyun.com
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_INDEX_URL=${PIP_INDEX_URL} PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# 单 worker：嵌入式 Chroma 单写入者约束，禁止多进程同时写 data/chroma
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
