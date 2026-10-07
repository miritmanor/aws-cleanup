"""Tiny string helpers with no opinion about presentation."""


def join_nonempty(parts, sep=", "):
    return sep.join(p for p in parts if p)
