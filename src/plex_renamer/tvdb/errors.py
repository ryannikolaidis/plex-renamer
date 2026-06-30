"""Exceptions raised by the TVDB client."""

from __future__ import annotations


class TVDBError(RuntimeError):
    """Base class for any non-recoverable TVDB error."""


class TVDBAuthError(TVDBError):
    """Raised when the API key (or pin) is missing / rejected."""


class TVDBNotFound(TVDBError):
    """Raised when an id is not present in TVDB."""


class TVDBRateLimitError(TVDBError):
    """Raised on HTTP 429 responses."""
