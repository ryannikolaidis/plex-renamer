"""Local relevance-ranking + fuzzy-query fallback for TMDB search results.

TMDB's ``search/tv`` and ``search/movie`` endpoints return results in
their own internal popularity-ish order, which is frequently NOT the
order a user would expect when they type a short query. For ``Lazarus``
TMDB returns ``The Lazarus Project (2008)`` ahead of ``Lazarus (2021)``
because the older show has more aggregate metadata; the user typing
``Lazarus`` expects the exact title to outrank the longer one.

Two pieces live here:

* :func:`rank_candidates` runs a rapidfuzz-based local re-rank against
  the user's query. Exact normalized matches and prefix matches get a
  boost so ``Lazarus`` outranks ``The Lazarus Project`` when the query
  is ``Lazarus``.
* :func:`cleaned_query_variants` returns an ordered list of fallback
  queries to try when the original query produces zero results. The
  original always comes first; the cleaned variants strip suffixes
  that don't belong in a show name (trailing ``_2`` or ``-2`` rename
  markers, parenthesized regional tags, a leading ``The `` article).
  Variants are deduplicated so the caller can iterate and stop on the
  first non-empty result set.

Neither function calls TMDB. They are pure utilities; the orchestrator
runs the network calls and feeds the results in.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from plex_renamer.tmdb.models import Candidate

# Boost added to the WRatio score (0-100) when the candidate title
# starts with the query (case-insensitive). Picked so an exact-title
# match (``Lazarus`` vs ``Lazarus``: WRatio ~100, prefix bonus +20)
# clearly outranks a longer-title prefix match (``Lazarus`` vs
# ``The Lazarus Project``: WRatio ~60, no prefix bonus because "the"
# breaks the prefix).
_PREFIX_BONUS = 20.0
# Smaller boost when the query appears as a whole word in the title
# but not as the prefix. Keeps "Lazarus Rising" above "The Lazarus
# Project" without overwhelming the prefix bonus.
_WORD_BONUS = 8.0


def _normalize(s: str) -> str:
    """Whitespace-collapse + lowercase for comparison only."""
    return " ".join(s.lower().strip().split())


def _score(query: str, title: str) -> float:
    """Return a relevance score for ``title`` against ``query``.

    Combines rapidfuzz ``WRatio`` (which handles partial matches and
    token reordering) with a prefix bonus so exact-title matches
    outrank "title contains query as one of several tokens" matches.
    """
    q = _normalize(query)
    t = _normalize(title)
    if not q or not t:
        return 0.0
    base = float(fuzz.WRatio(q, t))
    if t == q:
        return base + _PREFIX_BONUS + _WORD_BONUS
    if t.startswith(q):
        return base + _PREFIX_BONUS
    # Whole-word containment, e.g. query "Lazarus" matching title
    # "Lazarus Rising". The boundary check avoids false matches like
    # "Lazarus" against "Lazaruse".
    if re.search(rf"\b{re.escape(q)}\b", t):
        return base + _WORD_BONUS
    return base


def rank_candidates(query: str, candidates: list[Candidate]) -> list[Candidate]:
    """Return ``candidates`` sorted by descending relevance to ``query``.

    The sort is stable so candidates with equal scores preserve the
    incoming TMDB order. Empty inputs return the input list unchanged.
    """
    if not query or not candidates:
        return list(candidates)
    scored = [(c, _score(query, c.title)) for c in candidates]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [c for c, _ in scored]


def cleaned_query_variants(query: str) -> list[str]:
    """Return progressively-cleaned queries to try when the original misses.

    The list always starts with the original query, so a caller running
    "try variants until one returns results" can iterate from index 0
    uniformly. Each variant is appended only if it differs from every
    earlier entry (deduplicated by exact string equality after strip).

    Cleanups applied in order:

    * Strip trailing ``_<digits>`` or ``-<digits>`` (e.g. ``Lazarus_2``
      from a duplicate-named test folder).
    * Strip a trailing parenthesized suffix (e.g. ``Lazarus (US)``).
    * Strip a leading ``The `` (case-insensitive).
    * Combine the strip-trailing-number AND strip-leading-The variants
      so e.g. ``The Lazarus_2`` -> ``Lazarus``.
    """
    out: list[str] = []

    def push(candidate: str) -> None:
        clean = candidate.strip()
        if clean and clean not in out:
            out.append(clean)

    push(query)

    # Strip trailing _<digits> or -<digits>.
    stripped_num = re.sub(r"[_-]\d+\s*$", "", query).strip()
    push(stripped_num)

    # Strip trailing parenthesized content. ``Lazarus (US)`` -> ``Lazarus``.
    stripped_paren = re.sub(r"\s*\([^)]*\)\s*$", "", query).strip()
    push(stripped_paren)

    # Strip a leading "The ".
    stripped_the = re.sub(r"^the\s+", "", query, flags=re.IGNORECASE).strip()
    push(stripped_the)

    # Combined: trailing-number then leading-The.
    combined = re.sub(r"^the\s+", "", stripped_num, flags=re.IGNORECASE).strip()
    push(combined)

    # Combined: trailing-paren then leading-The.
    combined2 = re.sub(r"^the\s+", "", stripped_paren, flags=re.IGNORECASE).strip()
    push(combined2)

    return out


__all__ = ["cleaned_query_variants", "rank_candidates"]
