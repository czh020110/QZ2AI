---
title: Blog 架构
tags:
  - 架构
  - docker
  - 全栈
---

# Blog 架构

这是一个"会回答问题的博客"：用 [[quartz-静态博客]] 发布 Obsidian 笔记，再叠加一个基于 [[rag-检索增强]] 的 AI 问答助手。整套服务用 Docker Compose 编排，跑在一台 2 核 4G 的云服务器上。

> [!abstract] 一句话架构
> Obsidian 写作 → 同步到服务器 `data/notes` → Quartz 构建静态站 + LlamaIndex 建索引 → Nginx 统一入口（静态站 + `/api` 反代 FastAPI）。

## 服务组成

| 服务 | 职责 |
| --- | --- |
| Nginx | 统一入口：`/` 静态站，`/api` 反代后端 |
| Web (Quartz) | 把 Markdown 构建成静态 `public/` |
| API (FastAPI) | 提供 `/api/chat` 流式问答、`/api/reindex` 重建索引 |

向量库用嵌入式 Chroma（持久化到 `data/chroma`）。

> [!danger] 单写入者约束
> 嵌入式 Chroma 底层是 SQLite，多进程同时写会损坏数据。因此索引重建必须统一在 API 进程内触发（走 `/api/reindex`），禁止另起独立进程写同一个 `data/chroma`。

## 数据流

1. 浏览器访问 `/`，Nginx 返回 Quartz 静态页。
2. 用户在问答框提问，请求走 `/api/chat`。
3. FastAPI 用 [[rag-检索增强]] 召回相关片段，拼 prompt 交 LLM 流式返回。
4. 回答附上来源链接，指回对应的 [[quartz-静态博客]] 页面。

## 相关笔记

- [[rag-检索增强]]：问答背后的检索逻辑
- [[quartz-静态博客]]：前端渲染与内容来源
