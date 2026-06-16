# FastAPI 后端镜像。瘦身优先：slim 基础 + 仅装 requirements。
# 数据（chroma/notes/docstore）通过卷挂载到 /data，不打进镜像。
FROM python:3.12-slim

WORKDIR /app

# 先装依赖，利用 Docker 层缓存：requirements 不变则跳过重装
# pip 原生读取 PIP_INDEX_URL / PIP_TRUSTED_HOST 环境变量。默认官方 PyPI（可移植）；
# 网络受限服务器可传 --build-arg 指向 http 镜像并声明 trusted-host 加速。
# 仅在本 RUN 行注入，不写入镜像 ENV，避免运行时残留构建期源配置。
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
COPY requirements.txt .
RUN PIP_INDEX_URL=${PIP_INDEX_URL} PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# 单 worker：嵌入式 Chroma 单写入者约束，禁止多进程同时写 data/chroma
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
