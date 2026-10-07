"""The document index: a Chroma collection of the user's uploads, rebuilt only on request
(never during a turn). Chunks are masked before embedding, like scan rows."""

import logging
import os

from aws_resource_audit import console

from . import PAYLOAD_LOGGER
from ..masking import mask_value
from .documents import DocumentStore
from .embeddings import get_embeddings
from .loaders import extract_text
from .settings import AgentSettings

logger = logging.getLogger(__name__)
payload = logging.getLogger(PAYLOAD_LOGGER)

# A new collection name, so an old persisted index cannot serve stale vectors.
_COLLECTION_NAME = "account_documents"


def _chunks_for(path: str, splitter):
    """One document read and split, or [] with a warning: one bad upload never empties the index."""
    from langchain_core.documents import Document

    from aws_resource_audit.errors import AuditError

    name = os.path.basename(path)
    try:
        text = extract_text(path)
    except AuditError as e:
        console.warn(f"agent: skipping {name} - {e}")
        return []
    return [
        Document(page_content=mask_value(piece), metadata={"source": name, "chunk": i})
        for i, piece in enumerate(splitter.split_text(text))
    ]


def _load_and_chunk(files):
    """Every document, chunked by plain length with overlap (uploads have no reliable structure)."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=150)
    chunks = []
    for path in files:
        chunks.extend(_chunks_for(path, splitter))
    return chunks


def open_index(settings: AgentSettings, persist_dir: str):
    """The Chroma collection, opened but not built. `persist_dir` is resolved by the
    caller against the data volume."""
    from langchain_chroma import Chroma

    return Chroma(
        collection_name=_COLLECTION_NAME,
        embedding_function=get_embeddings(settings),
        persist_directory=persist_dir,
    )


def rebuild_index(store, doc_store: DocumentStore) -> int:
    """Re-embed the whole corpus into `store`; returns the chunk count. Deletes first,
    so a failed rebuild leaves an empty index, never a half-old one."""
    files = doc_store.files()
    console.detail(f"agent: rebuilding document index ({len(files)} files)")
    existing_ids = store.get()["ids"]
    if existing_ids:
        store.delete(ids=existing_ids)
    chunks = _load_and_chunk(files)
    if chunks:
        store.add_documents(chunks)
    return len(chunks)


def search_documents_index(store, query: str, k: int = 4):
    """The top-k chunks for `query`, with the document each came from - what
    tools.py::search_documents hands back to the model."""
    results = store.similarity_search(query, k=k)
    hits = [{"document": doc.metadata.get("source", ""), "text": doc.page_content}
            for doc in results]
    # Here rather than in tools.py: this is where the results exist, and an
    # empty list is the case worth being able to see.
    payload.debug("rag query %r k=%d -> %d hit(s): %r", query, k, len(hits), hits)
    return hits


def indexed_chunk_counts(persist_dir: str):
    """Chunks per document name, or None if nothing is indexed. Reads the persisted
    collection without loading the embedder; any failure means "not indexed"."""
    try:
        import chromadb

        collection = chromadb.PersistentClient(path=persist_dir).get_collection(
            _COLLECTION_NAME)
        records = collection.get(include=["metadatas"])
    except Exception:                                        # noqa: BLE001
        # Every failure reads as "not indexed" to the caller, which is the
        # honest answer but an undiagnosable one without this.
        logger.debug("could not read the document index at %s", persist_dir,
                     exc_info=True)
        return None

    counts: dict[str, int] = {}
    for meta in records.get("metadatas") or []:
        name = (meta or {}).get("source")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts
