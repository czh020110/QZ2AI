from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.vector_stores.chroma import ChromaVectorStore

from .config import get_settings
from .embed_client import get_embed_model
from .indexer import _load_docstore, get_chroma_collection


def retrieve(question: str) -> list[dict]:
    settings = get_settings()
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

    return [
        {
            "text": result.node.text,
            "title": result.node.metadata.get("title", ""),
            "source_url": result.node.metadata.get("source_url"),
        }
        for result in retriever.retrieve(question)
    ]
