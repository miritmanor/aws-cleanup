"""Chroma's ONNX all-MiniLM-L6-v2 behind LangChain's Embeddings interface. The model
downloads on first use into ~/.cache/chroma, not onto the data volume."""

from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
from langchain_core.embeddings import Embeddings


class OnnxMiniLmEmbeddings(Embeddings):
    """Names its model class outright: DefaultEmbeddingFunction may change model in a
    chromadb upgrade, which would silently mismatch an existing index."""

    def __init__(self):
        self._model = ONNXMiniLM_L6_V2()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Chroma returns numpy float32 arrays; LangChain expects plain floats.
        return [[float(x) for x in vector] for vector in self._model(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
