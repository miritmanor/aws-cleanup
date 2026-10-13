"""RevealStream: un-tokenizing a streamed reply. A token can arrive split across chunks,
so the tail that might be the start of one is held back until the next chunk decides it."""

import re

from .patterns import KINDS

_HEX = re.compile(r"[0-9a-f]*")


def _could_be_token_start(tail):
    """True if `tail` is a token so far: a prefix of "KIND-", or "KIND-" plus hex."""
    for kind in KINDS:
        head = kind + "-"
        if head.startswith(tail):
            return True
        if tail.startswith(head) and _HEX.fullmatch(tail[len(head):]):
            return True
    return False


class RevealStream:
    """feed() each chunk, then flush() once; the concatenated output is the revealed reply."""

    _MAX_HOLD = 32

    def __init__(self, pseudonymizer):
        self._pseudonymizer = pseudonymizer
        self._pending = ""

    def feed(self, chunk):
        text = self._pending + chunk
        cut = self._hold_from(text)
        self._pending = text[cut:]
        return self._pseudonymizer.reveal(text[:cut])

    def flush(self):
        text, self._pending = self._pending, ""
        return self._pseudonymizer.reveal(text)

    def _hold_from(self, text):
        """Where the held-back tail starts: the earliest word start that could still grow into a token."""
        for start in range(max(0, len(text) - self._MAX_HOLD), len(text)):
            if start and (text[start - 1].isalnum() or text[start - 1] in "_-"):
                continue
            if _could_be_token_start(text[start:]):
                return start
        return len(text)
