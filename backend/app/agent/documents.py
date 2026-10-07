"""The uploaded-document corpus on the data volume (never in the image). An upload
name is reduced to a basename and checked against a strict charset, never rewritten."""

import os
import re
from datetime import datetime, timezone

from aws_resource_audit.errors import AuditError

# Fixed in code: an extension is only accepted if loaders.py can parse it.
ACCEPTED_EXTENSIONS = (".pdf", ".docx", ".xlsx", ".csv", ".md", ".txt")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,127}$")


def check_name(filename: str) -> str:
    """`filename` reduced to a plain basename, or AuditError saying why not."""
    name = os.path.basename(filename or "").strip()
    if not _SAFE_NAME.match(name):
        raise AuditError(
            f"{filename!r}: not a usable file name. Use letters, digits, "
            f"spaces and . _ - ( ), starting with a letter or digit.")
    if os.path.splitext(name)[1].lower() not in ACCEPTED_EXTENSIONS:
        raise AuditError(
            f"{name}: unsupported file type. Accepted: "
            f"{', '.join(ACCEPTED_EXTENSIONS)}.")
    return name


def check_size(name: str, size: int) -> None:
    """AuditError if `size` is over the cap, or the file is empty."""
    if size <= 0:
        raise AuditError(f"{name}: the file is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise AuditError(
            f"{name}: {size / 1024 / 1024:.1f} MB exceeds the "
            f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB limit for one document.")


class DocumentStore:
    """Every uploaded document, as files in one flat directory: the name is the identity."""

    def __init__(self, directory: str):
        self.directory = directory

    def _ensure(self) -> str:
        os.makedirs(self.directory, exist_ok=True)
        return self.directory

    def path(self, name: str) -> str:
        return os.path.join(self.directory, check_name(name))

    def exists(self, name: str) -> bool:
        return os.path.isfile(self.path(name))

    def files(self) -> list[str]:
        """Full paths of every document, sorted; no directory yet means an empty corpus."""
        if not os.path.isdir(self.directory):
            return []
        return sorted(
            os.path.join(self.directory, name)
            for name in os.listdir(self.directory)
            if os.path.splitext(name)[1].lower() in ACCEPTED_EXTENSIONS
            and os.path.isfile(os.path.join(self.directory, name))
        )

    def list(self) -> list[dict]:
        """Name, size and upload time (mtime) per document, for the Documents section."""
        out = []
        for path in self.files():
            info = os.stat(path)
            out.append({
                "name": os.path.basename(path),
                "size": info.st_size,
                "uploaded": datetime.fromtimestamp(info.st_mtime, timezone.utc),
            })
        return out

    def save(self, filename: str, data: bytes) -> tuple[str, bool]:
        """Write one document; returns (stored name, replaced?). The caller validates the batch first."""
        name = check_name(filename)
        check_size(name, len(data))
        self._ensure()
        path = os.path.join(self.directory, name)
        replaced = os.path.exists(path)
        with open(path, "wb") as f:
            f.write(data)
        return name, replaced

    def delete(self, name: str) -> bool:
        """Remove one document; False if it was not there (the API returns 404)."""
        path = self.path(name)
        if not os.path.isfile(path):
            return False
        os.remove(path)
        return True
