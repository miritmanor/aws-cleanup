"""Embeddings for the document index: a local open-source model, so no key and no
per-query cost."""

from .settings import AgentSettings


def get_embeddings(settings: AgentSettings):
    """A sentence-transformers Embeddings on CPU, imported lazily."""
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(model_name=settings.embedding_model)
