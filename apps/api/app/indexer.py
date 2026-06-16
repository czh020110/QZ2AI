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


def _list_note_files(notes_dir: Path) -> list[str]:
    if not notes_dir.is_dir():
        return []
    return [str(path) for path in sorted(notes_dir.rglob("*.md")) if path.is_file()]


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

    input_files = _list_note_files(notes_dir)
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
