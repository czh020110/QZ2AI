# Quartz v5 前端镜像（多阶段）。
# v5 仅在分支 v5，无 release tag，pin 到具体 commit 防漂移。
#
# 设计要点：Quartz 是"构建工具"而非常驻服务。内容来自运行时挂载的 /data/notes，
# 构建产物写入共享卷 /public 交 nginx 托管。笔记更新走 rebuild（重跑构建），不需重建镜像。
#
# GitHub 访问：构建阶段通过 git insteadOf 将 github.com URL 重写到 ghfast.top 镜像加速，
# 无需宿主机代理。vendored Quartz 源码已含 node_modules + lockfile，跳过 npm ci。
# 运行时镜像无 git，不再尝试在线安装插件。

# ---------- 阶段一：使用仓库内 vendored Quartz 源码 + 安装社区插件 ----------
FROM node:22 AS deps

ARG GH_MIRROR=https://ghfast.top

WORKDIR /quartz

# vendored Quartz 已含 node_modules + lockfile，无需 npm ci。
COPY _vendor_quartz/ /quartz/

# 配置 git 将 github.com 请求重写到镜像加速站，解决国内构建环境无法直连 GitHub 的问题。
RUN git config --global url."${GH_MIRROR}/https://github.com/".insteadOf "https://github.com/"

# v5 插件全部从 github clone 到 .quartz/plugins。必须在有网络的构建阶段
# 一次性装好并焊进镜像，runtime 阶段就不再需要 git / 访问 github（自包含、可移植）。
COPY quartz.config.yaml ./quartz.config.yaml
COPY quartz.ts ./quartz.ts
COPY quartz ./quartz
RUN npx quartz plugin install --from-config

# 修复 note-properties 插件默认配置：将 includeAll 改为 true，清空 includedProperties
RUN if [ -f /quartz/.quartz/plugins/note-properties/package.json ]; then \
    sed -i 's/"includeAll": false/"includeAll": true/g' \
        /quartz/.quartz/plugins/note-properties/package.json && \
    sed -i 's/"includedProperties": \["description", "tags", "aliases"\]/"includedProperties": []/g' \
        /quartz/.quartz/plugins/note-properties/package.json && \
    echo "✓ note-properties plugin patched: includeAll=true, includedProperties=[]"; \
fi

# ---------- 阶段二：运行时镜像 ----------
FROM node:22-slim AS runtime

WORKDIR /quartz

# 从 deps 阶段复制装好插件的完整 Quartz 项目（含 node_modules、.quartz/plugins）
COPY --from=deps /quartz /quartz

COPY entrypoint.sh /entrypoint.sh
COPY scripts/ /quartz/scripts/
RUN chmod +x /entrypoint.sh

# /data/notes：挂载的 Markdown 内容源（只读）
# /public：构建产物输出卷，nginx 从此卷托管
VOLUME ["/data/notes", "/public"]

ENTRYPOINT ["/entrypoint.sh"]
