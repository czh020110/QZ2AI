# Blog 项目实现方案（Obsidian + Quartz + FastAPI + RAG）

本文档整理了当前讨论过的完整实现方案，目标是搭建一个：

- 使用 **Obsidian** 作为本地写作入口
- 使用 **Quartz** 发布静态博客
- 使用 **FastAPI + RAG** 提供 AI 问答能力
- 部署在 **腾讯云轻量服务器（2核4G）** 上
- 使用 **Docker / Docker Compose** 作为主要交付与部署方式
- 使用 **VS Code Remote SSH + GitHub** 进行开发和版本管理

---

## 1. 项目目标

希望实现的工作流是：

1. 在本地 Obsidian 中写作和维护笔记
2. 笔记同步到腾讯云 COS
3. 服务器定时同步最新 Markdown 内容
4. Quartz 构建为静态博客并由 Nginx 提供访问
5. 后端读取 Markdown 构建向量索引
6. 用户在博客页面中通过 AI 聊天框进行问答
7. 后端基于 RAG 返回与博客内容相关的答案

这是一个“**本地写作 -> 自动同步 -> AI 增强博客**”的轻量级知识站方案。

---

## 2. 当前方案是否有效

结论：**总体有效，可以落地。**

适合当前服务器配置（2核4G）的原因：

- Quartz 适合发布 Obsidian 风格内容
- FastAPI 足够轻量，容易部署
- LlamaIndex 很适合文档 ingestion / chunking / retrieval
- Chroma 支持本地持久化，适合作为轻量向量库
- 整体架构可以保持简单，不必引入重型数据库或复杂中间件

但需要注意以下修正：

1. **不要同时引入 LangChain 和 LlamaIndex**
   - MVP 阶段建议只用 **LlamaIndex**
   - 否则后续维护复杂度会提升

2. **不要每次全量重建索引**
   - 应优先设计为增量索引更新
   - 否则笔记变多后构建耗时会明显上升

3. **不要在服务器本地运行大模型推理**
   - 2核4G 不适合本地推理
   - 应使用外部 LLM API 与 Embedding API

4. **第一阶段不要做过度自动化**
   - 不必一开始就上 COS 回调 / 云函数
   - 推荐先用 `cron` 或 `systemd timer` 实现定时同步

5. **应增加 Swap**
   - 构建前端和批量做 embedding 时会有内存峰值
   - 建议至少补 4GB Swap

---

## 3. 推荐技术栈

### 前端 / 静态博客
- **Quartz（推荐用当前新版本能力）**
- 用于将 Obsidian 风格 Markdown 生成静态博客

Quartz 的优点：
- 原生适合 Obsidian 内容发布
- 支持 `[[双链]]`、标签、callout、embed 等能力
- 默认样式可直接使用
- 易于扩展自定义组件，例如 AI 聊天浮窗

### 后端
- **FastAPI**
- 提供 `/chat`、`/reindex` 等接口
- 支持 SSE 流式返回

### RAG 框架
- **LlamaIndex**
- 负责：
  - Markdown 解析
  - Chunking
  - Metadata 注入
  - Retrieval
  - 与 Chroma 集成

### 向量数据库
- **Chroma（本地持久化）**

推荐原因：
- 部署简单
- 轻量
- 支持本地持久化目录
- 适合单机小项目

### Web 服务
- **Nginx**
- 提供静态站点
- 反向代理 API

### 内容同步
- 腾讯云 **COS**
- 服务器通过定时脚本同步笔记

---

## 4. 系统整体架构

建议的最小可用架构：

```text
Obsidian -> COS -> 服务器同步脚本 -> Markdown 本地目录
                                     -> Quartz build -> Nginx 静态站
                                     -> LlamaIndex -> Chroma

浏览器 -> Nginx -> Quartz 页面
浏览器 -> Nginx -> FastAPI /chat -> Chroma 检索 -> LLM 生成回答
```

职责分层：

- **Obsidian**：内容创作入口
- **Quartz**：展示层
- **FastAPI**：AI 能力层
- **Chroma**：检索存储层
- **COS**：内容同步通道

---

## 5. 后端代码应该放在哪里

建议不要把后端代码放在 Obsidian 笔记目录，也不要和 Quartz `content` 混在一起。

推荐作为**独立的后端服务目录**，与前端并列。

推荐目录结构：

```text
/blog-system
  /apps
    /web          # Quartz 博客前端
    /api          # FastAPI + LLM/RAG 后端
  /data
    /notes        # 从 COS 同步下来的 Markdown
    /chroma       # Chroma 本地持久化数据
  /scripts
    sync.sh
    build_index.py
    deploy.sh
  /env
    api.env
```

后端内部进一步拆分建议：

```text
/apps/api
  main.py
  chat.py
  retriever.py
  llm_client.py
  embed_client.py
  indexer.py
  prompt_templates.py
  models.py
```

说明：
- `main.py`：FastAPI 入口
- `chat.py`：聊天接口逻辑
- `retriever.py`：向量检索与过滤
- `llm_client.py`：调用 LLM API
- `embed_client.py`：调用 embedding API
- `indexer.py`：索引构建与增量更新
- `models.py`：请求响应 schema

---

## 6. 代码应该放本地还是服务器

建议：**两边都有，但职责不同。**

### 推荐分工

#### 本地电脑
作为：
- 开发环境
- 版本管理入口
- 调试入口（如果本地也跑容器）

#### 服务器
作为：
- 运行环境
- 部署环境
- 数据持久化环境

### 最稳妥的原则
- **GitHub 私有仓库**：长期可信的代码主仓库
- **服务器目录**：运行副本 / 工作副本

也就是说：
- 不建议只把代码放服务器
- 应以 GitHub 仓库作为真正的长期保存中心

---

## 7. 推荐开发方式：VS Code Remote SSH + GitHub

这是当前最适合你的方式之一。

### 工作模式
- 本地用 **VS Code Remote SSH** 连接服务器
- 直接编辑服务器上的代码目录
- 使用 **GitHub 私有仓库** 做版本管理和云备份

工作流如下：

```text
VS Code Remote SSH
        ↓
直接编辑服务器工作副本
        ↓
git commit
        ↓
git push 到 GitHub 私有仓库
```

### 优点
- 不需要来回上传代码
- 调试环境接近部署环境
- GitHub 提供版本回滚与备份
- 适合单人维护项目

### 建议
即使你主要在服务器开发，也不要把服务器视为唯一主存储。

最佳原则：
- **开发入口在服务器**
- **版本中心在 GitHub**
- **运行环境也在服务器**

---

## 8. Docker 化是否合适

结论：**非常适合。**

建议将整个系统包装为一个 **Docker Compose 多服务项目**。

目标是做到：
- 代码逻辑上前后端分离
- 部署上统一交付
- 别人拿到项目后可以直接启动

### 推荐服务拆分

```text
frontend / web   -> Quartz 静态站相关
api              -> FastAPI + RAG
nginx            -> 对外代理
chroma           -> 向量库（可独立容器，也可本地持久化）
```

### 推荐 compose 级别的理解
- **代码层面前后端分离**
- **镜像层面前后端分离**
- **部署层面一个 compose 项目统一管理**

---

## 9. 推荐 Docker 项目结构

```text
my-ai-blog/
  compose.yaml
  .env.example

  apps/
    web/
      Dockerfile
      ...Quartz code

    api/
      Dockerfile
      requirements.txt
      app/
        main.py
        chat.py
        retriever.py
        llm_client.py
        indexer.py

  nginx/
    default.conf

  data/
    notes/
    chroma/

  scripts/
    sync.sh
    rebuild.sh
```

### 说明
- `apps/web`：Quartz 前端代码
- `apps/api`：FastAPI + RAG 代码
- `nginx/`：反向代理配置
- `data/notes`：同步下来的 Markdown 内容
- `data/chroma`：向量库持久化数据
- `scripts/`：同步、重建索引、部署脚本

---

## 10. 生产环境 Docker 部署建议

### 推荐部署模型

使用 `docker compose` 管理多个服务：

- `web`
- `api`
- `nginx`
- `chroma`

### 关于 Quartz 的特别说明
Quartz 是静态站点生成器，不一定要长期作为“运行中的前端容器”。

更推荐：

- Quartz 容器负责编译/构建
- 构建产物交给 Nginx 提供访问

而不是让 Quartz 开发服务器长期对外运行。

### 推荐生产形态
- `nginx`：对外暴露 80/443
- `/` 转发到静态站点
- `/api` 转发到 FastAPI

---

## 11. 配置与密钥管理

### 环境变量原则
真实密钥不要写死在代码里。

应该：
- 提交 `.env.example`
- 服务器保存真实 `.env`
- `.env` 不进入 Git 仓库

示例：

```env
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
EMBEDDING_MODEL=
CHROMA_DIR=/data/chroma
NOTES_DIR=/data/notes
```

### API Key 应放在哪里
只放服务器，不放在：
- Quartz 前端代码
- Obsidian 笔记
- GitHub 仓库

---

## 12. 数据与代码要分离

不要把以下内容直接混进代码仓库：

- Markdown 笔记数据
- Chroma 数据目录
- 日志
- 运行时缓存

这些内容应该：
- 单独挂载目录
- 或由 Docker Volume 管理

---

## 13. Quartz 的前端样式是否要自己设计

结论：**不需要从零设计。**

Quartz 本身就带有**现成的默认样式**，而且默认风格就是为：
- Obsidian 发布
- 数字花园
- 知识库博客

这意味着它开箱并不是裸框架，而是已经有：
- 内容页布局
- 侧边栏
- 搜索
- tags
- backlinks
- graph
- Obsidian 风格内容渲染

### 你可以怎么选

#### 方案 A：直接用 Quartz 默认样式
适合：
- 想快速上线
- 目标是知识博客 / 文档型内容站

#### 方案 B：使用社区主题
适合：
- 想要更多现成视觉风格
- 不想自己深度写 CSS

#### 方案 C：在默认样式基础上轻定制
推荐你走这个路线：
- 改 logo
- 改主色
- 改字体
- 改首页介绍
- 加 AI 聊天浮窗

### 当前最推荐的策略
先用默认样式上线，验证：
- 内容流是否顺畅
- 搜索是否好用
- AI 问答是否实用

之后再做定制。

---

## 14. 自动化发布建议

### 第一阶段（推荐）
使用简单脚本 + 定时任务：

1. 从 COS 同步 Markdown
2. 增量更新向量索引
3. 构建 Quartz 静态站
4. 更新静态文件
5. 必要时重启 API

推荐脚本：

```text
scripts/sync.sh
scripts/rebuild.sh
scripts/deploy.sh
```

### 调度方式
优先使用：
- `cron`
- 或 `systemd timer`

不建议在第一阶段就上：
- COS 回调
- 云函数 Webhook 自动触发

原因：
- 复杂度高
- 排障难
- MVP 阶段收益不大

---

## 15. 推荐的最小可用落地方案（MVP）

### 第一阶段目标
先跑通最关键闭环：

1. Obsidian 写作
2. COS 同步
3. 服务器拉取 Markdown
4. Quartz 构建静态博客
5. FastAPI 提供 `/chat`
6. LlamaIndex + Chroma 建索引并检索
7. 页面中嵌入 AI 聊天框

### MVP 原则
- 不追求过度自动化
- 不追求复杂 UI
- 不引入过多中间件
- 优先追求：可用、稳定、易维护

---

## 16. 推荐的部署与开发目录示例

服务器上可使用如下结构：

```text
/opt/my-ai-blog/
  compose.yaml
  .env
  apps/
    web/
    api/
  data/
    notes/
    chroma/
  scripts/
```

如果希望开发目录和运行目录分开，可以采用：

```text
/home/ubuntu/projects/my-ai-blog   # 开发目录（git）
/opt/my-ai-blog                    # 部署目录（运行）
```

开发流程：
- 在 `~/projects/my-ai-blog` 开发
- 测试后部署到 `/opt/my-ai-blog`

如果当前想保持简单，也可以先只保留一个目录，后面再拆。

---

## 17. 最终建议总结

### 技术选型建议
- 博客：**Quartz**
- 后端：**FastAPI**
- RAG：**LlamaIndex**
- 向量库：**Chroma**
- 反向代理：**Nginx**
- 部署：**Docker Compose**
- 开发：**VS Code Remote SSH**
- 代码托管：**GitHub 私有仓库**

### 架构建议
- 前后端分离
- 代码与数据分离
- 配置与密钥分离
- 使用 GitHub 作为版本中心
- 使用服务器作为运行环境

### 样式建议
- 先用 Quartz 默认样式
- 后续只做轻量定制
- 暂不建议一开始深度重做 UI

### 运维建议
- 增加 Swap
- 使用定时脚本做同步与构建
- 不在本机跑大模型推理
- 只把检索和服务放在本机运行

---

## 18. 推荐下一步执行顺序

建议按照下面顺序推进：

1. 初始化 GitHub 私有仓库
2. 创建项目目录结构
3. 搭建 Quartz 前端骨架
4. 搭建 FastAPI 后端骨架
5. 确定 `.env` 结构与 API 接入方式
6. 接入 Chroma 与 LlamaIndex
7. 实现 Markdown 索引构建
8. 实现 `/chat` SSE 接口
9. 在 Quartz 页面中加入 AI 聊天浮窗
10. 编写 `compose.yaml`
11. 编写同步/重建/部署脚本
12. 配置 Nginx 与定时任务

---

## 19. 一句话版本

这个项目最适合被做成一个：

> **基于 Quartz 的 Obsidian 静态博客 + 基于 FastAPI/LlamaIndex/Chroma 的 RAG 问答后端 + Docker Compose 部署的单机轻量知识站。**

它完全适合你当前的服务器规格，也适合作为后续共享给他人使用的可复用模板。

---

## 20. 快速部署

### 前置条件

- Docker & Docker Compose
- 至少 2GB 可用磁盘（构建镜像 + 向量数据）
- 建议增加 Swap（4GB），embedding 构建时有内存峰值

### 步骤

1. **克隆仓库**

   ```bash
   git clone <repo-url> && cd Blog
   ```

2. **配置环境变量**

   ```bash
   cp .env.example .env
   ```

   编辑 `.env`，必须填入：

   | 变量 | 说明 |
   |------|------|
   | `LLM_API_KEY` | LLM API 密钥（OpenAI 兼容） |
   | `EMBEDDING_API_KEY` | Embedding API 密钥 |
   | `REINDEX_TOKEN` | 索引重建接口保护 token（留空则拒绝调用） |
   | `ADMIN_TOKEN` | 管理后台接口保护 token（留空则拒绝调用） |

   如使用阿里云百炼 Embedding/Reranker，按 `.env.example` 注释填写对应 `EMBEDDING_*` / `RERANK_*` 变量。

3. **准备笔记内容**

   将 Markdown 笔记放入 `data/notes/` 目录（支持子目录）：

   ```bash
   mkdir -p data/notes
   # 将 .md 文件复制或同步到此目录
   ```

4. **启动服务**

   ```bash
   docker compose up -d --build
   ```

   首次启动会构建镜像，耗时约 3-5 分钟。

5. **构建索引**

   服务启动后调用一次索引重建：

   ```bash
   curl -X POST "http://localhost:18088/api/reindex" \
     -H "Authorization: Bearer <你的REINDEX_TOKEN>"
   ```

   返回 `{"status":"ok",...}` 即成功。

6. **访问**

   | 地址 | 说明 |
   |------|------|
   | `http://<IP>:18088/` | 博客首页 |
   | `http://<IP>:18088/admin/` | 管理后台（需 ADMIN_TOKEN） |

   博客页面右下角的 AI 助手浮窗可直接问答。

### 常用操作

- **更新笔记后重建索引**：再次调用 `/api/reindex`（增量更新，非全量重建）
- **查看管理后台**：访问 `/admin/`，输入 ADMIN_TOKEN 登录，可管理反馈和修改环境配置
- **查看日志**：`docker compose logs -f api`
- **重启 API**：`docker compose restart api`
