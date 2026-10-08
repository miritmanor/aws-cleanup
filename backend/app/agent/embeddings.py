"""Embeddings for the document index: a local open-source model, so no key and no
per-query cost. Chroma's own ONNX copy of it, so no torch (docs/decisions/0063)."""


def get_embeddings():
    """The embedder, imported lazily: an image built without the agent has no chromadb."""
    from .onnx_embeddings import OnnxMiniLmEmbeddings
    return OnnxMiniLmEmbeddings()
