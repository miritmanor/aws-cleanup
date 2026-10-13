"""The secret behind every token: random bytes on the data volume, made on first use.
Without it a token cannot be reversed by guessing, which a plain hash of a 12-digit id can."""

import logging
import os
import secrets

logger = logging.getLogger(__name__)

KEY_FILENAME = "agent_pseudonym.key"


def load_or_create_key(data_dir: str) -> bytes:
    """The key in data_dir, created (owner-only) if absent. A rescan keeps it, so tokens
    stay the same between scans; deleting the file changes every token."""
    path = os.path.join(data_dir, KEY_FILENAME)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path, encoding="ascii") as f:
            return bytes.fromhex(f.read().strip())
    key = secrets.token_bytes(32)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(key.hex())
    logger.info("created a new pseudonym key at %s", path)
    return key
