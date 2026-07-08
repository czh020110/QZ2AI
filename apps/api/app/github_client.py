"""GitHub REST API 客户端 — 用于在笔记仓库中管理 Actions 工作流文件"""
import base64
import logging
from typing import Any

logger = logging.getLogger("github_client")


def _parse_repo(github_repo_url: str) -> tuple[str, str]:
    """从 https://github.com/owner/repo.git 解析出 (owner, repo)"""
    url = github_repo_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.split("/")
    if len(parts) < 2:
        raise ValueError(f"无法解析仓库 URL: {github_repo_url}")
    return parts[-2], parts[-1]


def _api_base(owner: str, repo: str) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}"


def _request(token: str, method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """发起 GitHub API 请求，返回 JSON 响应体"""
    import urllib.request
    import json

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "blog-admin",
    }

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            response_body = resp.read().decode("utf-8")
            return json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        logger.error("GitHub API 错误 %s %s: %s", method, url, error_body[:500])
        raise


def get_file(token: str, repo_url: str, branch: str, path: str) -> dict[str, Any] | None:
    """获取仓库中的文件，返回 {sha, content, ...} 或 None"""
    owner, repo = _parse_repo(repo_url)
    url = f"{_api_base(owner, repo)}/contents/{path}?ref={branch}"
    try:
        result = _request(token, "GET", url)
        return result
    except Exception:
        return None


def put_file(token: str, repo_url: str, branch: str, path: str, content: str, message: str) -> dict[str, Any]:
    """创建或更新仓库中的文件"""
    owner, repo = _parse_repo(repo_url)

    # 先检查文件是否已存在
    existing = get_file(token, repo_url, branch, path)
    sha = existing.get("sha") if existing else None

    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    url = f"{_api_base(owner, repo)}/contents/{path}"
    logger.info("写入 GitHub 文件: %s/%s (sha=%s)", repo_url, path, sha or "new")
    return _request(token, "PUT", url, body)


def _shell_quote(value: str) -> str:
    """单引号包裹 shell 参数，内部单引号用 '\\'' 转义，避免 secret/url 中的特殊字符破坏脚本。"""
    return "'" + str(value).replace("'", "'\\''") + "'"


def build_sync_workflow(branch: str, blog_url: str, webhook_secret: str) -> str:
    """生成博客同步 Actions 工作流 YAML 内容。工作流监听全仓库 push 变更。"""

    sync_endpoint = _shell_quote(blog_url.rstrip("/") + "/api/webhook/sync")
    secret_header = _shell_quote(f"X-Webhook-Secret: {webhook_secret}")
    return f"""\
# 自动生成于博客管理后台，请勿手动修改
name: 博客自动同步

on:
  push:
    branches:
      - {branch}

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: 触发博客同步
        run: |
          curl -X POST {sync_endpoint} \\
            -H {secret_header} \\
            -H "Content-Type: application/json" \\
            -d '{{"event":"push","source":"github_actions"}}' \\
            -s --connect-timeout 30 --max-time 30
          echo "✅ 博客同步已触发"
"""
