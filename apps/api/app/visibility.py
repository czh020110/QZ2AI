"""内容展示范围:管理笔记目录树、公开范围与构建清单。

配置存 data/content_visibility.json;构建清单存 data/public_content_manifest.json。
后台只暴露目录/Markdown 元信息,不读取或返回笔记正文。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_settings

logger = logging.getLogger("visibility")

VALID_MODES = {"legacy", "custom", "all"}
MARKDOWN_EXTS = {".md", ".markdown"}
MAX_TREE_DEPTH = 8
MAX_TREE_NODES = 5000

HIDDEN_DIR_NAMES = {
    ".git",
    ".github",
    ".obsidian",
    ".trash",
    "trash",
    "private",
    "templates",
    "node_modules",
    "__pycache__",
}
SENSITIVE_FILE_NAMES = {".env", "id_rsa", "id_ed25519"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".sqlite", ".sqlite3", ".db"}

DEFAULT_VISIBILITY: dict[str, Any] = {
    "version": 1,
    "mode": "legacy",
    "selected": [],
    "excluded": [],
    "updated_at": "",
}


def _data_dir() -> Path:
    return Path(get_settings().db_path).parent


def _visibility_path() -> Path:
    return _data_dir() / "content_visibility.json"


def _manifest_path() -> Path:
    return _data_dir() / "public_content_manifest.json"


def _notes_root(settings=None) -> Path:
    return Path((settings or get_settings()).notes_dir).resolve()


def _read_raw() -> dict[str, Any]:
    path = _visibility_path()
    if not path.exists():
        return dict(DEFAULT_VISIBILITY)
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("content_visibility.json 解析失败,回退 legacy")
        return dict(DEFAULT_VISIBILITY)
    cfg = dict(DEFAULT_VISIBILITY)
    for key in DEFAULT_VISIBILITY:
        if key in data:
            cfg[key] = data[key]
    if cfg.get("mode") not in VALID_MODES:
        cfg["mode"] = "legacy"
    if not isinstance(cfg.get("selected"), list):
        cfg["selected"] = []
    if not isinstance(cfg.get("excluded"), list):
        cfg["excluded"] = []
    return cfg


def _normalize_rel_path(value: Any, allow_root: bool = True) -> str | None:
    raw = str(value or "").strip().replace("\\", "/")
    if raw in {"", ".", "/"}:
        return "" if allow_root else None
    if raw.startswith("/") or raw.startswith("~") or ":" in raw.split("/", 1)[0]:
        return None
    raw = raw.strip("/")
    parts = [p for p in raw.split("/") if p]
    if not parts and allow_root:
        return ""
    if any(p in {".", ".."} for p in parts):
        return None
    return "/".join(parts)


def _safe_child(root: Path, rel_path: str) -> Path | None:
    try:
        target = (root / rel_path).resolve() if rel_path else root.resolve()
    except OSError:
        return None
    if target == root or root in target.parents:
        return target
    return None


def _is_blocked_part(part: str, is_dir: bool = False) -> bool:
    name = part.strip()
    if not name:
        return True
    if name.startswith("."):
        return True
    lower = name.lower()
    if is_dir and lower in HIDDEN_DIR_NAMES:
        return True
    if lower in SENSITIVE_FILE_NAMES:
        return True
    if Path(lower).suffix in SENSITIVE_SUFFIXES:
        return True
    return False


def _is_blocked_rel(rel_path: str, is_dir: bool = False) -> bool:
    parts = [p for p in rel_path.split("/") if p]
    for idx, part in enumerate(parts):
        part_is_dir = idx < len(parts) - 1 or is_dir
        if _is_blocked_part(part, part_is_dir):
            return True
    return False


def _is_markdown(path: Path) -> bool:
    return path.suffix.lower() in MARKDOWN_EXTS


def _legacy_selected(settings) -> list[str]:
    """legacy 模式不再依赖旧前缀；等价于根目录全量，由文件树展示范围统一控制。"""
    return [""]


def get_effective_selected(settings=None) -> list[str]:
    settings = settings or get_settings()
    cfg = _read_raw()
    mode = cfg.get("mode", "legacy")
    if mode == "all":
        return [""]
    if mode == "custom":
        selected = [_normalize_rel_path(p) for p in cfg.get("selected", [])]
        clean = [p for p in selected if p is not None]
        return clean or []
    return _legacy_selected(settings)


def get_strip_prefix(settings=None) -> str:
    """单目录公开时剥掉该目录前缀，让选中目录作为站点根；多选或选文件时不剥。"""
    settings = settings or get_settings()
    selected = get_effective_selected(settings)
    if len(selected) != 1:
        return ""
    rel = selected[0]
    if not rel:
        return ""
    root = _notes_root(settings)
    target = _safe_child(root, rel)
    return rel if target is not None and target.is_dir() else ""


def public_doc_id(rel_path: str, settings=None) -> str:
    rel = _normalize_rel_path(rel_path, allow_root=False) or rel_path
    prefix = get_strip_prefix(settings).strip("/")
    if prefix and (rel == prefix or rel.startswith(prefix + "/")):
        return rel[len(prefix):].lstrip("/")
    return rel


def _is_under(rel_path: str, selected: str) -> bool:
    if selected == "":
        return True
    return rel_path == selected or rel_path.startswith(selected.rstrip("/") + "/")


def _get_excluded(settings=None) -> list[str]:
    cfg = _read_raw()
    excluded = cfg.get("excluded", [])
    if not isinstance(excluded, list):
        return []
    return [str(e).strip() for e in excluded if str(e).strip()]


def _is_excluded(rel_path: str, settings=None) -> bool:
    excluded = _get_excluded(settings)
    if not excluded or not rel_path:
        return False
    for ex in excluded:
        if rel_path == ex or rel_path.startswith(ex.rstrip("/") + "/"):
            return True
    return False


def is_public_path(rel_path: str, settings=None) -> bool:
    settings = settings or get_settings()
    root = _notes_root(settings)
    rel = _normalize_rel_path(rel_path, allow_root=False)
    if rel is None or _is_blocked_rel(rel):
        return False
    if _is_excluded(rel, settings):
        return False
    target = _safe_child(root, rel)
    if target is None or not target.exists():
        return False
    if target.is_file() and target.suffix.lower() in MARKDOWN_EXTS:
        if settings.assets_folder_set and any(part in settings.assets_folder_set for part in rel.split("/")):
            return False
    return any(_is_under(rel, sel) for sel in get_effective_selected(settings))


def _iter_markdown_files(base: Path, root: Path, assets_folders: set[str]) -> list[Path]:
    if not base.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(base.rglob("*")):
        if path.is_symlink() or not path.is_file() or not _is_markdown(path):
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if assets_folders and any(part in assets_folders for part in parts):
            continue
        if _is_blocked_rel(rel):
            continue
        files.append(path)
    return files


def list_public_markdown_files(settings=None) -> list[str]:
    settings = settings or get_settings()
    root = _notes_root(settings)
    if not root.is_dir():
        return []
    result: dict[str, Path] = {}
    for rel in get_effective_selected(settings):
        if _is_blocked_rel(rel, is_dir=True):
            continue
        if _is_excluded(rel, settings):
            continue
        target = _safe_child(root, rel)
        if target is None or not target.exists() or target.is_symlink():
            continue
        if target.is_file():
            if not _is_markdown(target):
                continue
            rel_file = target.relative_to(root).as_posix()
            if settings.assets_folder_set and any(part in settings.assets_folder_set for part in rel_file.split("/")):
                continue
            if not _is_blocked_rel(rel_file):
                result[rel_file] = target
        elif target.is_dir():
            for file_path in _iter_markdown_files(target, root, settings.assets_folder_set):
                file_rel = file_path.relative_to(root).as_posix()
                if _is_excluded(file_rel, settings):
                    continue
                result[file_rel] = file_path
    return [str(result[k]) for k in sorted(result)]


def _valid_selected_paths(paths: list[Any], settings) -> list[str]:
    root = _notes_root(settings)
    selected: list[str] = []
    for item in paths:
        rel = _normalize_rel_path(item)
        if rel is None or _is_blocked_rel(rel, is_dir=True):
            continue
        target = _safe_child(root, rel)
        if target is None or not target.exists() or target.is_symlink():
            continue
        if target.is_file() and not _is_markdown(target):
            continue
        selected.append(rel)
    return _compress_selected(selected, root)


def _compress_selected(paths: list[str], root: Path) -> list[str]:
    unique = sorted(set(paths), key=lambda p: (p.count("/"), p))
    compressed: list[str] = []
    for rel in unique:
        if rel == "":
            return [""]
        if any(_is_under(rel, parent) and _safe_child(root, parent) and _safe_child(root, parent).is_dir() for parent in compressed):
            continue
        compressed.append(rel)
    return compressed


def get_visibility_admin() -> dict[str, Any]:
    cfg = _read_raw()
    settings = get_settings()
    view = dict(cfg)
    view["effective_selected"] = get_effective_selected(settings)
    return view


def save_visibility(data: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    mode = str(data.get("mode", "custom")).strip()
    if mode not in VALID_MODES:
        mode = "custom"
    selected = _valid_selected_paths(data.get("selected", []) or [], settings)
    if mode == "custom" and not selected:
        selected = []
    excluded_raw = data.get("excluded", []) or [] if mode == "custom" else []
    excluded = [_normalize_rel_path(p) for p in excluded_raw]
    excluded = sorted({p for p in excluded if p is not None and p != ""})
    cfg = {
        "version": 1,
        "mode": mode,
        "selected": selected if mode == "custom" else [],
        "excluded": excluded if mode == "custom" else [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _visibility_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    write_public_manifest(settings)
    return get_visibility_admin()


def build_content_tree(settings=None) -> dict[str, Any]:
    settings = settings or get_settings()
    root = _notes_root(settings)
    counter = {"count": 0, "truncated": False}

    def walk(path: Path, depth: int) -> dict[str, Any] | None:
        if counter["count"] >= MAX_TREE_NODES:
            counter["truncated"] = True
            return None
        rel = "" if path == root else path.relative_to(root).as_posix()
        if path.is_symlink() or _is_blocked_rel(rel, is_dir=path.is_dir()):
            return None
        if path.is_file() and not _is_markdown(path):
            return None
        counter["count"] += 1
        if path.is_file():
            return {"name": path.name, "path": rel, "type": "file", "ext": path.suffix.lower()}
        node: dict[str, Any] = {"name": path.name if rel else "", "path": rel, "type": "directory", "children": []}
        if depth >= MAX_TREE_DEPTH:
            node["truncated"] = True
            return node
        try:
            children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return node
        for child in children:
            child_node = walk(child, depth + 1)
            if child_node is not None:
                node["children"].append(child_node)
        return node

    root_node = walk(root, 0) if root.is_dir() else {"name": "", "path": "", "type": "directory", "children": []}
    return {
        "root": root_node or {"name": "", "path": "", "type": "directory", "children": []},
        "visibility": get_visibility_admin(),
        "limits": {"max_depth": MAX_TREE_DEPTH, "max_nodes": MAX_TREE_NODES},
        "truncated": bool(counter["truncated"]),
    }


def get_markdown_preview(rel_path: str, settings=None) -> dict[str, str] | None:
    settings = settings or get_settings()
    root = _notes_root(settings)
    rel = _normalize_rel_path(rel_path, allow_root=False)
    if rel is None or _is_blocked_rel(rel):
        return None
    target = _safe_child(root, rel)
    if target is None or not target.is_file() or target.is_symlink() or not _is_markdown(target):
        return None
    try:
        content = target.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return {"path": rel, "name": target.name, "content": content}


def write_public_manifest(settings=None) -> dict[str, Any]:
    settings = settings or get_settings()
    root = _notes_root(settings)
    selected = get_effective_selected(settings)
    excluded = _get_excluded(settings)
    directories: list[str] = []
    files: list[str] = []
    for rel in selected:
        if _is_blocked_rel(rel, is_dir=True):
            continue
        if _is_excluded(rel, settings):
            continue
        target = _safe_child(root, rel)
        if target is None or not target.exists() or target.is_symlink():
            continue
        if target.is_dir():
            directories.append(rel)
        elif target.is_file() and _is_markdown(target) and not _is_blocked_rel(rel):
            # 与 list_public_markdown_files 保持一致：assets 目录下的 Markdown 不进入发布清单
            if settings.assets_folder_set and any(part in settings.assets_folder_set for part in rel.split("/")):
                continue
            files.append(rel)
    manifest = {
        "version": 1,
        "mode": _read_raw().get("mode", "legacy"),
        "selected": selected,
        "excluded": excluded,
        "strip_prefix": get_strip_prefix(settings),
        "directories": sorted(set(directories)),
        "files": sorted(set(files)),
        "assets_folders": sorted(settings.assets_folder_set),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _manifest_path().write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    return manifest
