# Quartz v5 前端镜像（多阶段）。
# v5 仅在分支 v5，无 release tag，pin 到具体 commit 防漂移（docs-research 实查 git ls-remote）。
#
# 设计要点：Quartz 是"构建工具"而非常驻服务。内容来自运行时挂载的 /data/notes，
# 构建产物写入共享卷 /public 交 nginx 托管。笔记更新走 rebuild（重跑构建），不需重建镜像。

# ---------- 阶段一：拉取 Quartz 源码 + 安装依赖 ----------
# 用完整版 node:22（内置 git），避免 slim 版 apt 装 git 在本地源极慢。
FROM node:22 AS deps

WORKDIR /quartz

ARG QUARTZ_COMMIT=92a4a48430cd33810317993a09a14c5eb4b4f3c6
RUN git clone https://github.com/jackyzha0/quartz.git . \
    && git checkout ${QUARTZ_COMMIT}

# npm ci 需要 lockfile；Quartz 仓库自带 package-lock.json
RUN npm ci

# v5 插件全部从 github clone 到 .quartz/plugins。必须在有 git + 网络的构建阶段
# 一次性装好并焊进镜像，runtime 阶段就不再需要 git / 访问 github（自包含、可移植）。
# 配置用项目自带 quartz.config.yaml（基于默认配置改 baseUrl/pageTitle）。
COPY quartz.config.yaml ./quartz.config.yaml
RUN npx quartz plugin install --from-config

# ---------- 阶段二：运行时镜像 ----------
FROM node:22-slim AS runtime

WORKDIR /quartz

# 从 deps 阶段复制装好插件的完整 Quartz 项目（含 node_modules、.quartz/plugins）
COPY --from=deps /quartz /quartz

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# /data/notes：挂载的 Markdown 内容源（只读）
# /public：构建产物输出卷，nginx 从此卷托管
VOLUME ["/data/notes", "/public"]

ENTRYPOINT ["/entrypoint.sh"]
