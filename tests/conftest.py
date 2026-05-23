"""Project-wide pytest fixtures.

The :func:`_enforce_readonly_reference_dir` autouse fixture is load-bearing:
the user's reference media tree at ``/Volumes/Cage/Media/CleverGet`` is
READ-ONLY. No code path in this repo may write to, move, rename, or delete
anything under that prefix. The fixture monkeypatches the writable surface
of ``os``, ``shutil``, ``pathlib``, and the builtin ``open`` so any test
that crosses the boundary raises ``RuntimeError`` instead of executing the
mutation.

The fixture runs for EVERY test. It is intentionally fail-loud: if a test
needs to write to a path that happens to share the prefix, it should mock
the path itself or use a tmp directory.
"""

from __future__ import annotations

import builtins
import os
import shutil
from pathlib import Path

import pytest

READONLY_PREFIX = "/Volumes/Cage/Media/CleverGet"

# open() modes that imply writing to the filesystem.
_WRITABLE_MODES: frozenset[str] = frozenset(
    {"w", "a", "x", "r+", "w+", "a+", "wb", "ab", "xb", "rb+", "wb+", "ab+"}
)


def _is_under_readonly_prefix(path: object) -> bool:
    """Return True if ``path`` resolves to something under the read-only prefix.

    Accepts str, bytes, os.PathLike, or Path. Does NOT call ``Path.resolve``
    because resolve() walks the filesystem and can itself fail; we use a
    string-prefix check on the user-supplied value normalized via
    ``os.fspath``.
    """
    if path is None:
        return False
    try:
        s = os.fspath(path)  # accepts str, bytes, PathLike
    except TypeError:
        return False
    if isinstance(s, bytes):
        try:
            s = s.decode()
        except UnicodeDecodeError:
            return False
    if not isinstance(s, str):
        return False
    # Absolute vs relative: we only guard absolute paths under the prefix.
    # A relative path "foo/bar" never matches /Volumes/Cage/Media/CleverGet.
    return s.startswith(READONLY_PREFIX)


def _raise_readonly(op: str, path: object, *_, **__) -> None:
    """Raise a RuntimeError naming the operation and offending path."""
    raise RuntimeError(
        f"read-only reference dir violation: {op} on {path!r} (under {READONLY_PREFIX})"
    )


@pytest.fixture(autouse=True)
def _enforce_readonly_reference_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block every writable filesystem call against the read-only prefix.

    The list of patched entry points is locked at the project level (see the
    slice 2 brief and ``.agents.md``). Adding new patched calls is fine;
    removing any of the locked ones is a regression.
    """
    # ----- os ---------------------------------------------------------------
    _wrap_os_unary(monkeypatch, "rename", legacy_two_arg=True)
    _wrap_os_unary(monkeypatch, "remove")
    _wrap_os_unary(monkeypatch, "removedirs")
    _wrap_os_unary(monkeypatch, "rmdir")

    # ----- shutil ----------------------------------------------------------
    _wrap_shutil_two_arg(monkeypatch, "copy")
    _wrap_shutil_two_arg(monkeypatch, "copy2")
    _wrap_shutil_two_arg(monkeypatch, "copyfile")
    _wrap_shutil_two_arg(monkeypatch, "copytree")
    _wrap_shutil_two_arg(monkeypatch, "move")
    _wrap_shutil_unary(monkeypatch, "rmtree")

    # ----- pathlib.Path ----------------------------------------------------
    _wrap_path_method(monkeypatch, "write_text")
    _wrap_path_method(monkeypatch, "write_bytes")
    _wrap_path_method(monkeypatch, "unlink")
    _wrap_path_method(monkeypatch, "mkdir")
    _wrap_path_method(monkeypatch, "rmdir")
    _wrap_path_method(monkeypatch, "touch")

    # ----- builtin open() --------------------------------------------------
    _wrap_open(monkeypatch)


# --- Wrappers ---------------------------------------------------------------


def _wrap_os_unary(
    monkeypatch: pytest.MonkeyPatch, fn_name: str, *, legacy_two_arg: bool = False
) -> None:
    """Wrap ``os.<fn_name>``. ``legacy_two_arg=True`` covers ``os.rename(src, dst)``."""
    original = getattr(os, fn_name)

    if legacy_two_arg:

        def guarded(src, dst, *args, **kwargs):
            if _is_under_readonly_prefix(src) or _is_under_readonly_prefix(dst):
                _raise_readonly(f"os.{fn_name}", (src, dst))
            return original(src, dst, *args, **kwargs)

    else:

        def guarded(path, *args, **kwargs):  # type: ignore[misc]
            if _is_under_readonly_prefix(path):
                _raise_readonly(f"os.{fn_name}", path)
            return original(path, *args, **kwargs)

    monkeypatch.setattr(os, fn_name, guarded)


def _wrap_shutil_two_arg(monkeypatch: pytest.MonkeyPatch, fn_name: str) -> None:
    original = getattr(shutil, fn_name)

    def guarded(src, dst, *args, **kwargs):
        if _is_under_readonly_prefix(src) or _is_under_readonly_prefix(dst):
            _raise_readonly(f"shutil.{fn_name}", (src, dst))
        return original(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, fn_name, guarded)


def _wrap_shutil_unary(monkeypatch: pytest.MonkeyPatch, fn_name: str) -> None:
    original = getattr(shutil, fn_name)

    def guarded(path, *args, **kwargs):
        if _is_under_readonly_prefix(path):
            _raise_readonly(f"shutil.{fn_name}", path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, fn_name, guarded)


def _wrap_path_method(monkeypatch: pytest.MonkeyPatch, method_name: str) -> None:
    """Wrap ``pathlib.Path.<method_name>`` so it raises on read-only paths."""
    original = getattr(Path, method_name)

    def guarded(self, *args, **kwargs):
        if _is_under_readonly_prefix(self):
            _raise_readonly(f"Path.{method_name}", self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, method_name, guarded)


def _wrap_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrap ``builtins.open`` to refuse writable modes against the read-only prefix."""
    original_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        # Some callers pass bytes / Path / int (fd). For int (fd) we can't
        # easily check; we permit those through.
        if isinstance(file, int):
            return original_open(file, mode, *args, **kwargs)
        if mode in _WRITABLE_MODES and _is_under_readonly_prefix(file):
            _raise_readonly(f"open({mode!r})", file)
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
