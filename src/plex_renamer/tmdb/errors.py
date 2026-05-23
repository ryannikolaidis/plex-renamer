"""Exception types raised by the TMDB client, cache, and fallback resolver.

These are deliberately narrow: callers (the planner, the GUI, the CLI) can
distinguish "the request failed because the key is bad" from "TMDB returned
nothing for this title" from "we got rate-limited." Hiding rate-limit errors
behind a retry loop is explicitly NOT this module's behavior — the caller
decides how to respond.
"""

from __future__ import annotations


class TMDBError(Exception):
    """Base class for every error raised by the TMDB layer."""


class TMDBAuthError(TMDBError):
    """The TMDB API key was missing, malformed, or rejected (HTTP 401)."""


class TMDBNotFound(TMDBError):
    """TMDB returned 404 for a direct ID lookup.

    Search endpoints do NOT raise this — they return an empty list instead.
    """


class TMDBRateLimitError(TMDBError):
    """TMDB returned 429.

    The client does not retry. The caller decides whether to back off, fall
    back, or surface the failure to the user.
    """
