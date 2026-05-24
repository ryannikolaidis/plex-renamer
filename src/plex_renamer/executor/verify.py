"""Post-copy verification.

Two checks:

* **Size** (always): the target's byte size equals the source's. Catches
  most truncation / partial-copy failures cheaply.
* **SHA-256** (opt-in): a full content hash of both sides. Strictly
  stronger, but the read pass doubles the I/O. Off by default; CLI
  ``--verify-hash`` opts in.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_HASH_CHUNK = 1024 * 1024  # 1 MiB


def verify_size(source: Path, target: Path) -> bool:
    try:
        return source.stat().st_size == target.stat().st_size
    except FileNotFoundError:
        return False


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_hash(source: Path, target: Path) -> bool:
    """Return True iff source and target hash to the same digest."""
    return sha256_of(source) == sha256_of(target)


def verify_hash_with_digest(source: Path, target: Path) -> tuple[bool, str]:
    """Verify with sha256 and return ``(matched, target_digest)``.

    The target is the canonical digest to record in the journal — it is
    the file the executor just wrote and the one undo will need to verify
    later. We hash both files exactly once each; this avoids the
    second pass that ``verify_hash(...)`` + ``sha256_of(target)`` would
    require when the caller wants both the verification result and the
    target's digest.
    """
    target_digest = sha256_of(target)
    source_digest = sha256_of(source)
    return (source_digest == target_digest, target_digest)


__all__ = ["sha256_of", "verify_hash", "verify_hash_with_digest", "verify_size"]
