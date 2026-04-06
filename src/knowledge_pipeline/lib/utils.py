# Shared utility functions.

import hashlib


def hash_file(path) -> str:
    """SHA-256 hash of a file (first 16 hex chars)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]
