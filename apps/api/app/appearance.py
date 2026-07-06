"""外观设置:字体、favicon、头像、社交联系方式。

配置存 data/appearance.json(单例 JSON,含列表与图片引用,不适合进 .env);
图片存 data/assets/,通过 /api/appearance/asset/{filename} 公开提供。
博客前端运行时 fetch /api/appearance 并注入,无需 Quartz 重建。
"""

import base64
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from .config import get_settings

logger = logging.getLogger("appearance")

# 公开路由(博客前端调用,无鉴权)
public_router = APIRouter(prefix="/api/appearance", tags=["appearance"])

ASSET_URL_PREFIX = "/api/appearance/asset/"
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2MB,base64 解码后上限
MAX_SOCIAL_LINKS = 8
MAX_BACKGROUND_PRESETS = 10
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")  # #RRGGBB,大小写不敏感
VALID_SHAPES = {"circle", "square", "rounded"}
VALID_ICON_TYPES = {"builtin", "custom"}
VALID_ACTIONS = {"jump", "copy"}  # jump=跳转(邮箱自动 mailto), copy=复制到剪贴板
VALID_BUILTIN_ICONS = {"github", "email", "twitter", "rss", "weibo", "zhihu", "bilibili", "linkedin"}
VALID_LOCALES = {"en-US", "zh-CN", "ja-JP"}  # 内置 UI 语言(博客与管理界面),en-US 为默认

# MIME → 扩展名映射(只允许常见图片类型)
MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
    "image/gif": "gif",
}

DEFAULT_APPEARANCE: dict[str, Any] = {
    "title_text": "My Blog",
    "title_font_family": "",
    "font_family": "",
    "favicon_url": "",
    "avatar_url": "",
    "avatar_shape": "circle",
    "avatar_link": "",
    "social_links": [],
    "background_presets": [],  # 背景色预设:[{id,name,light,dark}],每个一对深浅色
    "active_background_id": "",  # 当前应用预设 id,空串=不覆盖(用 Quartz 默认背景)
    "blog_locale": "en-US",      # 博客界面 UI 语言(Quartz 构建期注入,改后需 rebuild)
    "admin_locale": "en-US",     # 管理界面 UI 语言(前端运行时切换)
}

# 签名字体跨平台默认:macOS 用系统自带 HanziPen SC 手写体。
# 同时列出 family name(HanziPen SC)与 PostScript name(HanziPenSC-W3/W5),
# 兼容不同浏览器对 TTC 字体名的解析差异;HanziPen 未激活时回退到 Kaiti SC(楷体,默认安装)。
# Windows 用 Ink Free 手写体替代,都无时回退到系统 cursive。
DEFAULT_TITLE_FONT = "'HanziPen SC','HanziPenSC-W3','HanziPenSC-W5','Kaiti SC','Ink Free',cursive"


def _data_dir() -> Path:
    # 与 db_path(data/blog.db)同级,即 data/
    return Path(get_settings().db_path).parent


def _appearance_path() -> Path:
    return _data_dir() / "appearance.json"


def _asset_dir() -> Path:
    d = _data_dir() / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_raw() -> dict[str, Any]:
    p = _appearance_path()
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_APPEARANCE))
    try:
        data = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("appearance.json 解析失败,回退默认")
        return json.loads(json.dumps(DEFAULT_APPEARANCE))
    # 合并默认键,保证新增字段有默认值,且忽略未知字段
    merged = json.loads(json.dumps(DEFAULT_APPEARANCE))
    for k in DEFAULT_APPEARANCE:
        if k in data:
            merged[k] = data[k]
    return merged


def _asset_url(stored: str) -> str:
    """存储的文件名 → 公开 URL;空值返回空字符串"""
    if not stored:
        return ""
    return ASSET_URL_PREFIX + stored


def _strip_to_filename(value: str) -> str:
    """从前端回传的 URL 或文件名中提取纯文件名,防止路径穿越"""
    if not value:
        return ""
    v = str(value).strip()
    if v.startswith(ASSET_URL_PREFIX):
        v = v[len(ASSET_URL_PREFIX):]
    elif v.startswith("/"):
        v = v.lstrip("/")
    elif "/" in v:
        v = v.rsplit("/", 1)[-1]
    # 去掉危险字符,只保留文件名部分
    v = v.replace("..", "").strip("/")
    return v


def _valid_hex(s: Any) -> str:
    """校验 #RRGGBB 颜色值,合法返回小写归一值,非法返回空串"""
    v = str(s or "").strip()
    return v.lower() if HEX_COLOR_RE.match(v) else ""


def _valid_locale(s: Any) -> str:
    """校验 locale,合法返回原值,非法或缺失返回 en-US"""
    v = str(s or "").strip()
    return v if v in VALID_LOCALES else "en-US"


def _public_view(cfg: dict[str, Any]) -> dict[str, Any]:
    """把存储视图(文件名)转成公开视图(完整 URL),供前端直接使用"""
    links = []
    for s in cfg.get("social_links", []) or []:
        icon_type = s.get("icon_type", "builtin")
        icon = s.get("icon", "")
        links.append({
            "id": s.get("id", ""),
            "name": s.get("name", ""),
            "url": s.get("url", ""),
            "icon_type": icon_type,
            "icon": icon if icon_type == "builtin" else icon,
            "icon_url": _asset_url(icon) if icon_type == "custom" else "",
            "shape": s.get("shape", "circle"),
            "action": s.get("action", "jump") if s.get("action", "jump") in VALID_ACTIONS else "jump",
        })
    # 背景预设:仅输出 active 指向的预设(前端直接用,无需查表);无 active 或指向不存在则 null
    presets = cfg.get("background_presets", []) or []
    active_id = cfg.get("active_background_id", "") or ""
    background = None
    for p in presets:
        if active_id and p.get("id") == active_id:
            background = {
                "name": p.get("name", ""),
                "light": p.get("light", ""),
                "dark": p.get("dark", ""),
            }
            break
    return {
        "title_text": cfg.get("title_text", "My Blog"),
        "title_font_family": cfg.get("title_font_family", "") or DEFAULT_TITLE_FONT,
        "font_family": cfg.get("font_family", ""),
        "favicon_url": _asset_url(cfg.get("favicon_url", "")),
        "avatar_url": _asset_url(cfg.get("avatar_url", "")),
        "avatar_shape": cfg.get("avatar_shape", "circle"),
        "avatar_link": cfg.get("avatar_link", ""),
        "social_links": links,
        "background": background,
        "blog_locale": cfg.get("blog_locale", "en-US"),
        "admin_locale": cfg.get("admin_locale", "en-US"),
    }


def get_appearance_public() -> dict[str, Any]:
    return _public_view(_read_raw())


def get_appearance_admin() -> dict[str, Any]:
    """管理视图:公开视图 + 完整背景预设列表与 active id,供后台编辑预设。

    公开视图只暴露 active 预设的 background 字段(博客前端只需生效项);
    后台需要完整预设列表才能增删改,故单独提供。
    """
    cfg = _read_raw()
    view = _public_view(cfg)
    view["background_presets"] = cfg.get("background_presets", []) or []
    view["active_background_id"] = cfg.get("active_background_id", "") or ""
    view["admin_locale"] = cfg.get("admin_locale", "en-US")
    return view


def has_blog_locale() -> bool:
    """appearance.json 是否已写入 blog_locale 字段。

    用于区分"首次设置语言":旧配置无此字段时,即使用户选默认 en-US 也需触发 rebuild,
    让 entrypoint 的 sed 把 config locale 从旧值(zh-CN)覆盖为 en-US,否则博客仍显示旧语言。
    """
    p = _appearance_path()
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return "blog_locale" in data


def save_appearance(data: dict[str, Any]) -> dict[str, Any]:
    """校验并保存外观配置,返回公开视图"""
    cfg: dict[str, Any] = {
        "title_text": str(data.get("title_text", "My Blog"))[:100] or "My Blog",
        "title_font_family": str(data.get("title_font_family", ""))[:200],
        "font_family": str(data.get("font_family", ""))[:200],
        "favicon_url": _strip_to_filename(data.get("favicon_url", "")),
        "avatar_url": _strip_to_filename(data.get("avatar_url", "")),
        "avatar_shape": data.get("avatar_shape", "circle") if data.get("avatar_shape", "circle") in VALID_SHAPES else "circle",
        "avatar_link": str(data.get("avatar_link", ""))[:500],
        "social_links": [],
        "background_presets": [],
        "active_background_id": "",
        "blog_locale": _valid_locale(data.get("blog_locale", "en-US")),
        "admin_locale": _valid_locale(data.get("admin_locale", "en-US")),
    }
    for s in (data.get("social_links", []) or [])[:MAX_SOCIAL_LINKS]:
        icon_type = s.get("icon_type", "builtin")
        if icon_type not in VALID_ICON_TYPES:
            continue
        shape = s.get("shape", "circle")
        if shape not in VALID_SHAPES:
            shape = "circle"
        action = s.get("action", "jump")
        if action not in VALID_ACTIONS:
            action = "jump"
        icon = str(s.get("icon", ""))
        if icon_type == "builtin":
            if icon not in VALID_BUILTIN_ICONS:
                icon = "github"
        else:  # custom:存纯文件名
            icon = _strip_to_filename(icon)
        cfg["social_links"].append({
            "id": str(s.get("id") or uuid.uuid4().hex[:8]),
            "name": str(s.get("name", ""))[:50],
            "url": str(s.get("url", ""))[:500],
            "icon_type": icon_type,
            "icon": icon,
            "shape": shape,
            "action": action,
        })
    # 背景预设:校验 hex 颜色,light/dark 至少一个合法才保留;两个都非法则丢弃
    for p in (data.get("background_presets", []) or [])[:MAX_BACKGROUND_PRESETS]:
        light = _valid_hex(p.get("light", ""))
        dark = _valid_hex(p.get("dark", ""))
        if not light and not dark:
            continue
        cfg["background_presets"].append({
            "id": str(p.get("id") or uuid.uuid4().hex[:8]),
            "name": str(p.get("name", ""))[:50],
            "light": light,
            "dark": dark,
        })
    # active 必须指向已保留的预设,否则置空(用 Quartz 默认背景)
    active_id = str(data.get("active_background_id", "") or "")
    if active_id and any(p["id"] == active_id for p in cfg["background_presets"]):
        cfg["active_background_id"] = active_id
    _appearance_path().write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8"
    )
    return _public_view(cfg)


def save_data_url_asset(data_url: str) -> str:
    """解析 data URL,校验类型与大小,保存到 data/assets/,返回文件名。

    data_url 格式:data:image/png;base64,xxxx
    """
    if not data_url or not data_url.startswith("data:"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持 data: URL 格式图片")
    try:
        header, b64 = data_url.split(",", 1)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "data URL 格式非法")
    mime = header[5:].split(";")[0].lower()
    if mime not in MIME_TO_EXT:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"不支持的图片类型: {mime}(仅 png/jpeg/webp/svg/ico/gif)",
        )
    try:
        content = base64.b64decode(b64)
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "base64 解码失败")
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"图片过大({len(content)} bytes),上限 {MAX_IMAGE_BYTES} bytes",
        )
    ext = MIME_TO_EXT[mime]
    filename = f"{uuid.uuid4().hex}.{ext}"
    (_asset_dir() / filename).write_bytes(content)
    return filename


@public_router.get("")
def get_appearance() -> dict[str, Any]:
    """公开外观配置(博客前端调用)"""
    return get_appearance_public()


@public_router.get("/asset/{filename}")
def get_asset(filename: str) -> FileResponse:
    # 防路径穿越:只允许纯文件名
    safe = Path(filename).name
    if safe != filename or ".." in filename or "/" in filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法文件名")
    path = _asset_dir() / safe
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")
    return FileResponse(str(path))
