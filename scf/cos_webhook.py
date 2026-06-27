# -*- coding: utf-8 -*-
"""
腾讯云 SCF 函数：COS 事件通知中转到 Blog API Webhook

部署方式：
1. 在腾讯云 SCF 控制台创建函数，选择 Python 3.9 运行时
2. 将此文件内容作为函数代码
3. 配置环境变量：
   - WEBHOOK_URL: Blog API 的 webhook 地址，如 https://your-domain.com:18088/admin/api/webhook/cos
   - WEBHOOK_SECRET: 与服务器 .env 中 WEBHOOK_SECRET 一致
   - WEBHOOK_TIMEOUT: 超时秒数（默认 10）
4. 在 COS 存储桶 > 基础配置 > 事件通知 中配置：
   - 事件类型：ObjectCreated:* 和 ObjectRemove:*
   - 前缀：Obsidian/Blog/online/（根据实际 NOTES_COS_PREFIX 调整）
   - 推送到此 SCF 函数
"""

import json
import logging
import os

import urllib.request
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
WEBHOOK_TIMEOUT = int(os.environ.get("WEBHOOK_TIMEOUT", "10"))


def main_handler(event, context):
    """SCF 入口函数"""
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL 未配置")
        return {"statusCode": 500, "body": "WEBHOOK_URL 未配置"}

    if not WEBHOOK_SECRET:
        logger.error("WEBHOOK_SECRET 未配置")
        return {"statusCode": 500, "body": "WEBHOOK_SECRET 未配置"}

    logger.info("收到 COS 事件: %s", json.dumps(event, ensure_ascii=False)[:500])

    payload = json.dumps(event, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Secret": WEBHOOK_SECRET,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT) as resp:
            status_code = resp.status
            body = resp.read().decode("utf-8")
            logger.info("Webhook 响应: status=%d body=%s", status_code, body[:200])
            return {"statusCode": status_code, "body": body}
    except urllib.error.HTTPError as e:
        logger.error("Webhook 返回错误: status=%d body=%s", e.code, e.read().decode("utf-8", errors="replace")[:200])
        return {"statusCode": e.code, "body": "webhook error"}
    except urllib.error.URLError as e:
        logger.error("Webhook 请求失败: %s", e.reason)
        return {"statusCode": 502, "body": "webhook unreachable"}
    except Exception as e:
        logger.error("Webhook 异常: %s", e)
        return {"statusCode": 500, "body": "internal error"}
