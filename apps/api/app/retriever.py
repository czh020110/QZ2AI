from .config import get_settings

# 检索（阶段三实现）。从已持久化的 Chroma + docstore 重建 VectorStoreIndex，
# 不重新 embedding，返回 top_k chunk（含 text 与 source_url / title）。


def retrieve(question: str) -> list[dict]:
    """按问题召回相关 chunk。返回 [{text, title, source_url}, ...]。"""
    settings = get_settings()  # noqa: F841  阶段三使用 similarity_top_k 等
    raise NotImplementedError("检索将在阶段三实现")
