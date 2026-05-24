"""JSON write-ahead journal for the executor.

Why write-ahead: every filesystem mutation is preceded by a journal
entry written to disk. A crash mid-batch leaves a journal that the next
run can read for recovery / undo. The on-disk format is a single JSON
file per batch, rewritten atomically via ``os.replace`` so a torn-write
is impossible.

Schema::

    {
      "version": 1,
      "batch_id": "<ULID-like>",
      "input_root": "/path/to/input",
      "library_root": "/path/to/library",
      "created_at": <unix epoch>,
      "cleanup_ran": false,
      "entries": [
        {
          "op_index": 0,
          "parent_op_index": null,
          "source": "...",
          "target": "...",
          "status": "pending" | "verified" | "failed" | "reverted",
          "timestamp": <epoch>,
          "bytes": <int>|None,
          "sha256": "<hex>"|None,
          "error": "<msg>"|None
        }
      ]
    }

Sidecars are stored as separate entries with ``parent_op_index`` set to
the plan op index of their parent. The sidecar's ``op_index`` is its
position within the parent's sidecar tuple (0, 1, 2, ...). Primaries
carry ``parent_op_index = null``. Entries are looked up by the
(op_index, parent_op_index) tuple so a sidecar never collides with a
primary op whose index happens to match.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from plex_renamer.config.paths import app_config_dir

JOURNAL_VERSION = 1
JOURNAL_SUBDIR = "journals"

JournalStatus = Literal["pending", "verified", "failed", "reverted"]


@dataclass
class JournalEntry:
    op_index: int
    source: str
    target: str
    status: JournalStatus = "pending"
    timestamp: float = field(default_factory=time.time)
    bytes: int | None = None
    sha256: str | None = None
    error: str | None = None
    parent_op_index: int | None = None
    """When set, this entry is a sidecar of the primary op at ``parent_op_index``.

    Primaries leave this ``None``. ``op_index`` is the plan-level op index for
    primaries and the sidecar's position within the parent's sidecar tuple
    (0, 1, 2, ...) for sidecars. Entries are looked up by the
    (op_index, parent_op_index) tuple so a sidecar's local position cannot
    collide with a later primary op's index.
    """


def _new_batch_id() -> str:
    """Generate a sortable, collision-resistant batch id.

    Format: ``<unix-seconds>-<8-hex>``. We don't depend on a ULID
    package; this gives us sortability and uniqueness across runs.
    """
    return f"{int(time.time())}-{secrets.token_hex(4)}"


def default_journal_dir() -> Path:
    return app_config_dir() / JOURNAL_SUBDIR


@dataclass
class Journal:
    """The journal for one apply-batch.

    Construct via :meth:`new` (creates a fresh batch) or
    :meth:`load` (reads an existing file). All writes go through
    :meth:`_persist`, which writes atomically.
    """

    path: Path
    batch_id: str
    input_root: str
    library_root: str
    entries: list[JournalEntry]
    cleanup_ran: bool = False
    created_at: float = field(default_factory=time.time)

    # ----- Constructors ---------------------------------------------------

    @classmethod
    def new(
        cls,
        input_root: Path,
        library_root: Path,
        journal_dir: Path | None = None,
        batch_id: str | None = None,
    ) -> Journal:
        bid = batch_id or _new_batch_id()
        jdir = journal_dir if journal_dir is not None else default_journal_dir()
        jdir.mkdir(parents=True, exist_ok=True)
        path = jdir / f"{bid}.json"
        j = cls(
            path=path,
            batch_id=bid,
            input_root=str(input_root),
            library_root=str(library_root),
            entries=[],
            cleanup_ran=False,
        )
        j._persist()
        return j

    @classmethod
    def load(cls, path: Path) -> Journal:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        version = data.get("version", 1)
        if version != JOURNAL_VERSION:
            raise ValueError(f"journal version mismatch: {version} vs {JOURNAL_VERSION}")
        entries = [JournalEntry(**e) for e in data.get("entries", [])]
        return cls(
            path=path,
            batch_id=data["batch_id"],
            input_root=data["input_root"],
            library_root=data["library_root"],
            entries=entries,
            cleanup_ran=bool(data.get("cleanup_ran", False)),
            created_at=float(data.get("created_at", time.time())),
        )

    # ----- Mutations ------------------------------------------------------

    def add_pending(
        self,
        op_index: int,
        source: Path,
        target: Path,
        *,
        parent_op_index: int | None = None,
    ) -> JournalEntry:
        entry = JournalEntry(
            op_index=op_index,
            source=str(source),
            target=str(target),
            status="pending",
            parent_op_index=parent_op_index,
        )
        self.entries.append(entry)
        self._persist()
        return entry

    def mark_verified(
        self,
        op_index: int,
        bytes_copied: int,
        sha256: str | None = None,
        *,
        parent_op_index: int | None = None,
    ) -> None:
        e = self._entry(op_index, parent_op_index)
        e.status = "verified"
        e.bytes = bytes_copied
        e.sha256 = sha256
        e.timestamp = time.time()
        self._persist()

    def mark_failed(
        self,
        op_index: int,
        error: str,
        *,
        parent_op_index: int | None = None,
    ) -> None:
        e = self._entry(op_index, parent_op_index)
        e.status = "failed"
        e.error = error
        e.timestamp = time.time()
        self._persist()

    def mark_reverted(
        self,
        op_index: int,
        *,
        parent_op_index: int | None = None,
    ) -> None:
        e = self._entry(op_index, parent_op_index)
        e.status = "reverted"
        e.timestamp = time.time()
        self._persist()

    def mark_cleanup(self, ran: bool) -> None:
        self.cleanup_ran = ran
        self._persist()

    def _entry(self, op_index: int, parent_op_index: int | None = None) -> JournalEntry:
        for e in self.entries:
            if e.op_index == op_index and e.parent_op_index == parent_op_index:
                return e
        raise KeyError(
            f"no journal entry for op_index={op_index}, parent_op_index={parent_op_index}"
        )

    # ----- Queries --------------------------------------------------------

    @property
    def all_verified(self) -> bool:
        return bool(self.entries) and all(e.status == "verified" for e in self.entries)

    @property
    def verified_entries(self) -> list[JournalEntry]:
        return [e for e in self.entries if e.status == "verified"]

    # ----- Persistence ----------------------------------------------------

    def _persist(self) -> None:
        data: dict[str, Any] = {
            "version": JOURNAL_VERSION,
            "batch_id": self.batch_id,
            "input_root": self.input_root,
            "library_root": self.library_root,
            "created_at": self.created_at,
            "cleanup_ran": self.cleanup_ran,
            "entries": [asdict(e) for e in self.entries],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        # Make sure parent exists; ``Journal.new`` does this, ``load`` may
        # not have, but the parent is the dir we read from so it exists.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2)
        os.replace(tmp, self.path)


__all__ = [
    "JOURNAL_VERSION",
    "Journal",
    "JournalEntry",
    "JournalStatus",
    "default_journal_dir",
]
