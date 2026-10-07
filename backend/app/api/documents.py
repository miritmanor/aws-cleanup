"""Upload, list and delete the agent's documents. Indexing is its own request
(POST /documents/reindex); one bad file fails the whole batch, writing nothing."""

from fastapi import APIRouter, File, HTTPException, UploadFile

from aws_resource_audit.errors import AuditError

from .. import deps
from ..agent import index
from ..agent.documents import DocumentStore, check_name, check_size
from ..agent.rag import indexed_chunk_counts
from ..schemas import DocumentUploadResult, UploadedDocument

router = APIRouter(prefix="/api/agent", tags=["documents"])


def _store() -> DocumentStore:
    return DocumentStore(deps.documents_dir())


def _listing() -> list[UploadedDocument]:
    """The corpus with each document's indexed chunk count; None means it arrived
    after the last rebuild."""
    counts = indexed_chunk_counts(index.persist_dir())
    return [
        UploadedDocument(
            **entry,
            chunks=None if counts is None else counts.get(entry["name"], 0),
        )
        for entry in _store().list()
    ]


@router.get("/documents", response_model=list[UploadedDocument],
            summary="Documents the agent can search")
def list_documents():
    return _listing()


@router.post("/documents", response_model=DocumentUploadResult,
             summary="Upload documents describing this AWS account")
async def upload_documents(files: list[UploadFile] = File(...)):
    """Store one or more documents, replacing same-named ones. Everything is validated
    in memory first, so a failed upload changes nothing."""
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")

    payloads = []
    for upload in files:
        data = await upload.read()
        name = check_name(upload.filename or "")
        check_size(name, len(data))
        payloads.append((name, data))

    names = [name for name, _ in payloads]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise AuditError(
            f"the same document was uploaded twice in one request: "
            f"{', '.join(duplicates)}. Upload it once.")

    store = _store()
    replaced = [name for name, data in payloads if store.save(name, data)[1]]
    return DocumentUploadResult(documents=_listing(), replaced=replaced)


@router.delete("/documents/{name}", status_code=204,
               summary="Remove a document from the corpus")
def delete_document(name: str):
    """Delete the file. Its chunks stay searchable until a reindex, which is
    why the frontend fires one after a delete and not only after an upload."""
    if not _store().delete(name):
        raise HTTPException(status_code=404, detail=f"{name}: no such document.")


@router.post("/documents/reindex", response_model=list[UploadedDocument],
             summary="Rebuild the document index from the corpus")
def reindex_documents():
    """Re-embed every document synchronously, then report what is indexed. The first
    call is slow while the embedding model loads."""
    index.rebuild()
    return _listing()
