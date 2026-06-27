# COS 事件通知 → Blog 自动同步：SCF 部署说明

## 概述

COS 文件变动时，通过腾讯云 SCF（云函数）将事件转发到 Blog API 的 webhook 端点，触发自动同步链路。

**链路**：COS 事件通知 → SCF 云函数 → `POST /admin/api/webhook/cos` → syncer 容器自动执行 sync + reindex + Quartz 重建

## 前提条件

- Blog 服务已部署并可通过公网访问（如 `https://blog.czh.edu.kg`）
- `.env` 中已配置 `AUTO_SYNC_ENABLED=true` 和 `WEBHOOK_SECRET`
- COS Bucket 已创建且笔记存储在指定前缀下（如 `Obsidian/Blog/online/`）

## 部署步骤

### 1. 创建 SCF 函数

1. 登录腾讯云控制台，进入 **云函数 SCF**
2. 点击 **新建**，选择 **自定义创建**
3. 配置：
   - 函数名称：`blog-cos-webhook`（自定义）
   - 运行环境：**Python 3.9**
   - 代码类型：**在线编辑**
   - 将 `scf/cos_webhook.py` 的内容粘贴到编辑器中
   - 高级配置 > 环境变量：
     - `WEBHOOK_URL` = `https://你的域名/admin/api/webhook/cos`
     - `WEBHOOK_SECRET` = 与 `.env` 中 `WEBHOOK_SECRET` 一致
     - `WEBHOOK_TIMEOUT` = `10`（可选，默认 10 秒）
   - 执行超时时间：建议 30 秒
4. 点击 **完成** 创建函数

### 2. 配置 COS 事件通知

1. 进入 **对象存储 COS** 控制台
2. 选择存储桶 > **基础配置** > **事件通知**
3. 点击 **添加事件通知**：
   - 事件类型：勾选 **创建对象**（ObjectCreated:*）和 **删除对象**（ObjectRemove:*）
   - 前缀：填入 `Obsidian/Blog/online/`（与 `.env` 中 `NOTES_COS_PREFIX` 对应）
   - 推送目标：选择刚创建的 SCF 函数 `blog-cos-webhook`
4. 保存

### 3. 验证

1. 在 COS 上传一个测试文件到 `Obsidian/Blog/online/` 目录
2. 检查 SCF 日志：应看到"收到 COS 事件"和"Webhook 响应: status=200"
3. 检查 Blog 管理后台：同步状态应显示 pending → syncing → 完成

## 注意事项

- SCF 函数与 COS Bucket 需在同一地域
- COS 事件通知是异步调用，SCF 默认最多重试 3 次
- 如果 Blog API 暂时不可用，事件不会丢失（SCF 会重试）
- 建议在 SCF 中配置日志投递到 CLS，便于排查问题
