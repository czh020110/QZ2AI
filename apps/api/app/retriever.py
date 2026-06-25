import logging

from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.vector_stores.chroma import ChromaVectorStore

from .config import get_settings
from .embed_client import get_embed_model
from .errors import AppError
from .indexer import _load_docstore, get_chroma_collection
from .reranker_client import rerank_contexts

logger = logging.getLogger("api")


def retrieve(question: str) -> list[dict]:
    settings = get_settings()

    try:
        collection = get_chroma_collection()
        if collection.count() == 0:
            return []

        storage_context = StorageContext.from_defaults(
            vector_store=ChromaVectorStore(chroma_collection=collection),
            docstore=_load_docstore(settings.docstore_path),
        )
        index = VectorStoreIndex(
            nodes=[],
            storage_context=storage_context,
            embed_model=get_embed_model(),
        )
        retriever = index.as_retriever(similarity_top_k=settings.similarity_top_k)

        contexts = [
            {
                "text": result.node.text,
                "title": result.node.metadata.get("title", ""),
                "source_url": result.node.metadata.get("source_url"),
            }
            for result in retriever.retrieve(question)
        ]
        return rerank_contexts(question, contexts)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("检索阶段失败: %s", exc)
        raise AppError(502, "retrieval_unavailable", "检索服务暂时不可用", str(exc)) from exc
