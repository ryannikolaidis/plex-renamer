"""Test corpus generator.

Builds a mock filesystem (empty files) covering every observed and plausible
input pattern the parser needs to handle. This is a development tool, not a
runtime dependency: it lets the parser test suite exercise the full token
catalog without depending on the user's real CleverGet reference tree.

The reference tree at ``/Volumes/Cage/Media/CleverGet`` is READ-ONLY; the
generator never touches it. The generator only writes under a caller-provided
output directory.

Run as a script::

    python -m plex_renamer.test_corpus.generator /tmp/plex_corpus
"""

from __future__ import annotations

# The ``generator`` module is imported lazily via ``__getattr__`` so that
# ``python -m plex_renamer.test_corpus.generator <out_dir>`` does not trigger
# a ``RuntimeWarning`` about the module being present in ``sys.modules``
# before its top-level execution.
from plex_renamer.test_corpus.patterns import CORPUS_PATTERNS, CorpusEntry

__all__ = ["CORPUS_PATTERNS", "CorpusEntry", "build_corpus"]


def __getattr__(name: str) -> object:
    if name == "build_corpus":
        from plex_renamer.test_corpus.generator import build_corpus

        return build_corpus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
