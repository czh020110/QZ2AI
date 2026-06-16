from .config import get_settings


def get_embed_model():
    settings = get_settings()
    from llama_index.embeddings.openai import OpenAIEmbedding

    return OpenAIEmbedding(
        model=settings.embedding_model,
        api_base=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        timeout=settings.embedding_timeout_seconds,
        max_retries=settings.http_max_retries,
        embed_batch_size=settings.embedding_batch_size,
    )
