---
title: Quartz 静态博客
tags:
  - quartz
  - 前端
  - 静态站
---

# Quartz 静态博客

Quartz 是一个把 **Obsidian 笔记**直接发布成静态网站的工具。它原生理解 `[[双链]]`、标签、callout 和 embed，几乎零改造就能把本地知识库变成可公开访问的博客。

> [!info] 与本项目的关系
> 本项目用 Quartz 把 `data/notes` 下的 Markdown 构建成静态站，交给 Nginx 托管。写作在 Obsidian，发布交给 Quartz，二者解耦。

## 关键特性

- **双向链接**：像 [[rag-检索增强]] 这样的链接会自动生成反向链接（backlinks）。
- **标签页**：按 `tags` 聚合笔记。
- **全文搜索**：内置客户端搜索。
- **Graph 视图**：可视化笔记之间的连接。

## 构建产物

Quartz 读取内容目录，生成纯静态的 `public/`，没有运行时后端依赖。AI 问答能力由独立的 FastAPI 服务提供，前后端通过 Nginx 统一入口，详见 [[blog-架构]]。

> [!warning] 内容来源
> 开发期 `data/notes` 放手工示例笔记；上线后由同步脚本从对象存储拉取真实内容，下游构建逻辑不变。

## 相关笔记

- [[blog-架构]]：Quartz 在整体架构中的位置
- [[rag-检索增强]]：问答能力背后的检索逻辑
