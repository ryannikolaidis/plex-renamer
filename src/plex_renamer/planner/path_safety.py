"""Cross-platform path-safety helpers.

The planner emits paths that must survive moving the library between
macOS, Linux, and Windows volumes. We:

* NFC-normalize every component so disk encoding is stable.
* Strip Windows-reserved characters (``<>:"/\\|?*``) — replace with ``_``.
* Suffix Windows-reserved device names (``CON``, ``PRN``, ``AUX``,
  ``NUL``, ``COM1``-``COM9``, ``LPT1``-``LPT9``) with ``_`` so the path
  is legal on NTFS.
* Warn when the full target path exceeds 240 characters.

This module also owns the always-disallowed prefix list used by the
executor's cleanup pass. The list lives here, not in ``executor/``,
because the planner can warn the user up-front when an output target
would itself land under a guarded prefix.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

PATH_LENGTH_WARN_THRESHOLD = 240
"""Above this many characters, we surface a warning on the RenameOp."""

# Characters Windows forbids in filenames. The forward slash is a separator
# on every platform we target so we don't strip it from a single component
# — components don't contain ``/`` by construction.
_WINDOWS_FORBIDDEN_RE = re.compile(r'[<>:"\\|?*]')

# Reserved device names. Compared case-insensitively against the stem (the
# part before the first dot). ``COM1.txt`` is reserved; ``CONsole.txt`` is
# fine.
_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

# Paths that cleanup MUST refuse to touch even when every other check
# passes. Listed as POSIX strings; the comparator normalizes both sides.
#
# Two flavors:
#
# * ALWAYS_DISALLOWED_POSIX_SUBTREES — refuse the prefix AND every
#   descendant. ``/var`` blocks ``/var/folders/abc/T/scratch.mp4`` (a
#   macOS per-user temp path); ``/private`` blocks
#   ``/private/var/folders/...`` (the realpath form of the same temp
#   dir); ``/tmp`` blocks ``/tmp/scratch.mp4``; etc. These are
#   directories that NEVER contain media we should delete.
# * ALWAYS_DISALLOWED_POSIX_EXACT_PLUS_ONE — refuse the prefix and
#   refuse exactly-one-component-deeper, but allow deeper descendants.
#   ``/Users`` refuses ``/Users`` and ``/Users/ryan`` (the home dir
#   itself is sacred) but allows ``/Users/ryan/media/scratch.mp4``.
#   Same shape for ``/Volumes``: refuse the mount, refuse the
#   ``/Volumes/Disk`` mount root, allow content beneath.
ALWAYS_DISALLOWED_POSIX_SUBTREES: frozenset[str] = frozenset(
    {
        "/private",
        "/System",
        "/Library",
        "/Applications",
        "/tmp",
        "/var",
    }
)

ALWAYS_DISALLOWED_POSIX_EXACT_PLUS_ONE: frozenset[str] = frozenset(
    {
        "/Users",
        "/Volumes",
    }
)

# Back-compat alias for callers / tests that look up the legacy union.
ALWAYS_DISALLOWED_POSIX: frozenset[str] = (
    ALWAYS_DISALLOWED_POSIX_SUBTREES | ALWAYS_DISALLOWED_POSIX_EXACT_PLUS_ONE | {"/"}
)

# Windows-style always-disallowed list. The comparator uses
# :class:`PureWindowsPath` for these so case-insensitive matching works.
ALWAYS_DISALLOWED_WINDOWS: frozenset[str] = frozenset(
    {
        "C:\\",
        "C:\\Users",
        "C:\\Windows",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
    }
)


def sanitize_component(name: str) -> str:
    """Return a Windows-and-macOS-safe version of a single path component.

    Steps:

    1. NFC-normalize.
    2. Replace forbidden Windows chars with ``_``.
    3. Suffix reserved device names with ``_``.
    4. Trim trailing dots and spaces (Windows strips these silently).
    5. If the component reduces to empty, return ``_``.
    """
    if not name:
        return "_"
    nfc = unicodedata.normalize("NFC", name)
    stripped = _WINDOWS_FORBIDDEN_RE.sub("_", nfc)
    # Reserved device name check operates on the stem (before first dot).
    stem, _, suffix = stripped.partition(".")
    if stem.upper() in _RESERVED_NAMES:
        stem = f"{stem}_"
        stripped = f"{stem}.{suffix}" if suffix else stem
    # Trailing dots/spaces — Windows strips them from filenames.
    stripped = stripped.rstrip(". ")
    if not stripped:
        return "_"
    return stripped


def sanitize_path(path: Path) -> Path:
    """Apply :func:`sanitize_component` to every component of ``path``.

    Preserves drive / anchor verbatim (the anchor isn't a renamable
    component). On POSIX, the root ``/`` stays as the anchor.
    """
    if not path.parts:
        return path
    anchor = path.anchor
    components = path.parts[1:] if anchor else path.parts
    safe = [sanitize_component(c) for c in components]
    if anchor:
        return Path(anchor, *safe)
    return Path(*safe)


def path_length_warning(path: Path) -> str | None:
    """Return a warning string when ``path`` exceeds the configured threshold.

    Returns ``None`` when the path is fine.
    """
    s = str(path)
    if len(s) > PATH_LENGTH_WARN_THRESHOLD:
        return f"path exceeds {PATH_LENGTH_WARN_THRESHOLD} chars ({len(s)}): {s}"
    return None


def is_always_disallowed(path: Path) -> bool:
    """Return True if ``path`` matches the always-disallowed cleanup list.

    The check is platform-agnostic: we check both POSIX and Windows
    canonical forms. Callers pass a real (resolved) path; comparison is
    structural (parts-based), not string-based.

    POSIX semantics:

    * ``/`` matches verbatim.
    * Subtree-disallowed prefixes (``/var``, ``/private``, ``/System``,
      ``/Library``, ``/Applications``, ``/tmp``): the path is refused
      when it equals the prefix OR is any descendant of it. This is
      what catches ``/var/folders/<user>/T/scratch.mp4`` and the
      realpath form ``/private/var/folders/...`` that macOS resolves
      temp dirs through.
    * Exact-plus-one prefixes (``/Users``, ``/Volumes``): refused when
      equal to the prefix or exactly one component deeper
      (``/Users/ryan``, ``/Volumes/Disk``). Content beneath those
      (``/Users/ryan/media/x.mp4``) is allowed; the cleanup pass's
      depth + descendant-of-input-root checks cover the home directory
      from there.
    """
    # Normalize input to absolute POSIX-style for matching.
    s = str(path)

    # Windows-style match: try parsing both as a PureWindowsPath when the
    # string looks Windows-shaped.
    if len(s) >= 2 and s[1] == ":":
        win = PureWindowsPath(s)
        win_norm = str(win)
        for guarded in ALWAYS_DISALLOWED_WINDOWS:
            if win_norm.lower() == guarded.lower():
                return True
        # C:\Users\<any> — exactly one component beyond "C:\Users".
        return (
            len(win.parts) == 3
            and win.parts[0].lower() == "c:\\"
            and win.parts[1].lower() == "users"
        )

    pp = PurePosixPath(s)
    if str(pp) == "/":
        return True

    # Subtree-disallowed: refuse the prefix and every descendant.
    for guarded in ALWAYS_DISALLOWED_POSIX_SUBTREES:
        guarded_parts = PurePosixPath(guarded).parts
        if len(pp.parts) >= len(guarded_parts) and pp.parts[: len(guarded_parts)] == guarded_parts:
            return True

    # Exact-plus-one: refuse the prefix and refuse exactly one component deeper.
    for guarded in ALWAYS_DISALLOWED_POSIX_EXACT_PLUS_ONE:
        guarded_parts = PurePosixPath(guarded).parts
        # Equal to prefix.
        if pp.parts == guarded_parts:
            return True
        # Exactly one component deeper.
        if (
            len(pp.parts) == len(guarded_parts) + 1
            and pp.parts[: len(guarded_parts)] == guarded_parts
        ):
            return True

    return False


def has_at_least_three_components(path: Path) -> bool:
    """Return True if ``path`` has 3 or more components below the FS root.

    ``/`` -> False (0). ``/Users`` -> False (1). ``/Users/ryan`` -> False
    (2). ``/Users/ryan/scratch`` -> True (3).
    """
    s = str(path)
    if len(s) >= 2 and s[1] == ":":
        # Windows: drive + N components. ``C:\`` -> parts=("C:\\",) len=1.
        win = PureWindowsPath(s)
        return len(win.parts) >= 4  # drive + 3 components
    pp = PurePosixPath(s)
    if pp.parts and pp.parts[0] == "/":
        return len(pp.parts) >= 4  # "/" + 3 components
    return len(pp.parts) >= 3


def is_strict_descendant(path: Path, root: Path) -> bool:
    """Return True iff ``path`` is strictly under ``root`` (not equal)."""
    try:
        rel = PurePath(path).relative_to(PurePath(root))
    except ValueError:
        return False
    return rel != PurePath(".")


__all__ = [
    "ALWAYS_DISALLOWED_POSIX",
    "ALWAYS_DISALLOWED_POSIX_EXACT_PLUS_ONE",
    "ALWAYS_DISALLOWED_POSIX_SUBTREES",
    "ALWAYS_DISALLOWED_WINDOWS",
    "PATH_LENGTH_WARN_THRESHOLD",
    "has_at_least_three_components",
    "is_always_disallowed",
    "is_strict_descendant",
    "path_length_warning",
    "sanitize_component",
    "sanitize_path",
]
