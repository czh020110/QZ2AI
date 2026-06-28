from pathlib import Path
import re

from llama_index.core import SimpleDirectoryReader
from llama_index.core.ingestion import DocstoreStrategy, IngestionPipeline
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.vector_stores.chroma import ChromaVectorStore

from .config import get_settings
from .embed_client import get_embed_model
from .errors import AppError


def get_chroma_collection():
    settings = get_settings()
    import chromadb

    client = chromadb.PersistentClient(path=settings.chroma_dir)
    return client.get_or_create_collection(settings.chroma_collection)


def _load_docstore(docstore_path: str) -> SimpleDocumentStore:
    path = Path(docstore_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return SimpleDocumentStore.from_persist_path(str(path))
    return SimpleDocumentStore()


def _list_note_files(notes_dir: Path, assets_folders: set[str] | None = None) -> list[str]:
    if not notes_dir.is_dir():
        return []
    skip = assets_folders or set()
    result = []
    for path in sorted(notes_dir.rglob("*.md")):
        if not path.is_file():
            continue
        # 跳过附件文件夹内的文件
        if skip and any(part in skip for part in path.relative_to(notes_dir).parts):
            continue
        result.append(str(path))
    return result


def _extract_title(text: str, doc_id: str) -> str:
    frontmatter_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if frontmatter_match:
        for line in frontmatter_match.group(1).splitlines():
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip('"').strip("'")
                if title:
                    return title

    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            if title:
                return title

    path = Path(doc_id)
    return path.parent.name if path.name == "index.md" and path.parent.name else path.stem


def _build_source_url(doc_id: str) -> str:
    path = Path(doc_id)
    if path.name == "index.md":
        return "/" if str(path.parent) == "." else f"/{path.parent.as_posix()}"
    return f"/{path.with_suffix('').as_posix()}"


_slug_map: dict[str, str] | None = None


def _build_slug_map() -> dict[str, str]:
    """构建 quartz-slug → file_path 的映射（spaces→-, 转小写）。"""
    collection = get_chroma_collection()
    result = collection.get(include=["metadatas"], limit=10000)
    mapping: dict[str, str] = {}
    for m in result["metadatas"]:
        url = m.get("source_url", "")
        fp = m.get("file_path", "")
        if url and fp:
            key = url.lstrip("/").replace(" ", "-").lower()
            mapping.setdefault(key, fp)
    return mapping


def get_note_context(slug: str) -> dict:
    """根据前端 slug 读取笔记文件，返回 frontmatter 和标题大纲。"""
    global _slug_map
    if _slug_map is None:
        _slug_map = _build_slug_map()

    key = slug.lstrip("/").lower()
    file_path = _slug_map.get(key, "")
    if not file_path:
        return {}

    settings = get_settings()
    full_path = Path(settings.notes_dir) / file_path
    if not full_path.is_file():
        return {}

    text = full_path.read_text(encoding="utf-8")
    frontmatter: dict = {}
    body = text
    fm_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if fm_match:
        body = text[fm_match.end():]
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                frontmatter[k.strip()] = v.strip().strip('"').strip("'")

    headings = []
    for line in body.splitlines():
        if line.startswith("### "):
            headings.append(f"### {line[4:].strip()}")
        elif line.startswith("## "):
            headings.append(f"## {line[3:].strip()}")
        elif line.startswith("# "):
            headings.append(f"# {line[2:].strip()}")

    return {"frontmatter": frontmatter, "headings": headings}


def _prepare_documents(notes_dir: Path, input_files: list[str]):
    documents = SimpleDirectoryReader(
        input_files=input_files,
        filename_as_id=True,
        required_exts=[".md"],
    ).load_data()

    for document in documents:
        source_path = Path(document.metadata["file_path"])
        doc_id = source_path.relative_to(notes_dir).as_posix()
        title = _extract_title(document.text, doc_id)
        source_url = _build_source_url(doc_id)

        document.doc_id = doc_id
        document.metadata = {
            "doc_id": doc_id,
            "file_path": doc_id,
            "file_name": source_path.name,
            "title": title,
            "source_url": source_url,
        }
        document.excluded_embed_metadata_keys = ["doc_id", "file_path", "source_url"]
        document.excluded_llm_metadata_keys = ["doc_id", "file_path"]

    return documents


def _existing_hashes_by_doc_id(docstore: SimpleDocumentStore) -> dict[str, str]:
    return {doc_id: doc_hash for doc_hash, doc_id in docstore.get_all_document_hashes().items()}


def _delete_missing_documents(collection, docstore: SimpleDocumentStore, deleted_doc_ids: list[str]) -> None:
    for doc_id in deleted_doc_ids:
        collection.delete(where={"ref_doc_id": doc_id})
        docstore.delete_document(doc_id, raise_error=False)


def reindex() -> dict:
    settings = get_settings()
    notes_dir = Path(settings.notes_dir)
    if not notes_dir.is_dir():
        raise AppError(500, "notes_unavailable", f"笔记目录不存在：{notes_dir}")

    input_files = _list_note_files(notes_dir, settings.assets_folder_set)
    docstore = _load_docstore(settings.docstore_path)
    previous_hashes = _existing_hashes_by_doc_id(docstore)
    current_doc_ids = {
        Path(file_path).relative_to(notes_dir).as_posix()
        for file_path in input_files
    }
    deleted_doc_ids = sorted(set(previous_hashes) - current_doc_ids)

    collection = get_chroma_collection()
    _delete_missing_documents(collection, docstore, deleted_doc_ids)

    if not input_files:
        docstore.persist(settings.docstore_path)
        return {
            "status": "ok",
            "processed": 0,
            "skipped": 0,
            "deleted": len(deleted_doc_ids),
            "detail": "未发现 Markdown 笔记，已完成缺失文档清理",
        }

    documents = _prepare_documents(notes_dir, input_files)
    processed = sum(1 for document in documents if previous_hashes.get(document.doc_id) != document.hash)
    skipped = len(documents) - processed

    vector_store = ChromaVectorStore(chroma_collection=collection)
    pipeline = IngestionPipeline(
        transformations=[MarkdownNodeParser.from_defaults(), get_embed_model()],
        vector_store=vector_store,
        docstore=docstore,
        docstore_strategy=DocstoreStrategy.UPSERTS,
    )
    pipeline.run(documents=documents)
    docstore.persist(settings.docstore_path)

    return {
        "status": "ok",
        "processed": processed,
        "skipped": skipped,
        "deleted": len(deleted_doc_ids),
        "detail": f"索引完成：processed={processed} skipped={skipped} deleted={len(deleted_doc_ids)}",
    }
