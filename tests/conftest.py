"""Project-wide pytest fixtures.

GUI tests (``tests/test_gui_*.py``) use ``pytest-qt``'s ``qtbot`` fixture
and rely on ``QT_QPA_PLATFORM=offscreen`` being set in the environment.
The CI workflow sets that globally for the test step; locally, run GUI
tests under ``QT_QPA_PLATFORM=offscreen uv run pytest`` to keep them
from popping windows. This conftest deliberately does NOT mutate
``os.environ["QT_QPA_PLATFORM"]`` because pytest-qt creates the
QApplication eagerly and the value must already be set before the
plugin loads.

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

KNOWN LIMITATION: the conftest patches the standard-library entry points
at the module level (``os.rename``, ``shutil.copy``, ``pathlib.Path.mkdir``,
etc.). A future module that imports a writable name into its own namespace
via ``from os import rename`` would bind the original function at import
time and bypass our monkeypatch. The executor slice (which performs the
real filesystem mutations) MUST NOT rebind these names; instead, the
executor should call through a project-owned ``plex_renamer.executor.guards``
shim that wraps every writable call site with the same prefix check. The
conftest then becomes a defence-in-depth layer rather than the sole guard.

The :func:`safe_tmp_path` fixture is provided for tests that exercise the
cleanup pass. On macOS, pytest's stock ``tmp_path`` resolves through
``/private/var/folders/...`` — a path the always-disallowed list now
refuses outright. Tests that need to call :func:`cleanup_sources` (or
``apply_plan(..., cleanup=True)``) use ``safe_tmp_path`` instead so the
fixture root lives under the user's home dir, which is allowed for
descendants beyond the home root itself.
"""

from __future__ import annotations

import builtins
import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path, PurePath

import pytest

READONLY_PREFIX = "/Volumes/Cage/Media/CleverGet"
_READONLY_PREFIX_PARTS: tuple[str, ...] = PurePath(READONLY_PREFIX).parts

# open() modes that imply writing to the filesystem. We treat every mode
# whose semantics include a write (binary OR text variant) as guarded.
_WRITABLE_MODES: frozenset[str] = frozenset(
    {
        # Pure binary.
        "wb",
        "ab",
        "xb",
        "rb+",
        "wb+",
        "ab+",
        # Pure text (default).
        "w",
        "a",
        "x",
        "r+",
        "w+",
        "a+",
        # Explicit text-mode variants (with 't').
        "wt",
        "at",
        "xt",
        "rt+",
        "wt+",
        "at+",
        "xt+",
    }
)


def _is_under_readonly_prefix(path: object) -> bool:
    """Return True if ``path`` resolves to something under the read-only prefix.

    Accepts str, bytes, os.PathLike, or Path. Uses a :class:`PurePath`-based
    "is within" check (the candidate's parts must START with the prefix's
    parts) so a sibling path like ``/Volumes/Cage/Media/CleverGetExtra``
    does NOT false-match a string ``startswith`` check.

    Does NOT call ``Path.resolve``: resolve() walks the filesystem and can
    itself fail; we use the user-supplied value via ``os.fspath``.
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
    # We only guard absolute paths under the prefix; a relative path never
    # matches /Volumes/Cage/Media/CleverGet.
    candidate = PurePath(s)
    if not candidate.is_absolute():
        return False
    candidate_parts = candidate.parts
    prefix_len = len(_READONLY_PREFIX_PARTS)
    if len(candidate_parts) < prefix_len:
        return False
    return candidate_parts[:prefix_len] == _READONLY_PREFIX_PARTS


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
    _wrap_os_two_arg(monkeypatch, "rename")
    _wrap_os_two_arg(monkeypatch, "replace")
    _wrap_os_two_arg(monkeypatch, "symlink")
    _wrap_os_two_arg(monkeypatch, "link")
    _wrap_os_unary(monkeypatch, "remove")
    _wrap_os_unary(monkeypatch, "removedirs")
    _wrap_os_unary(monkeypatch, "rmdir")
    _wrap_os_unary(monkeypatch, "mkdir")
    _wrap_os_unary(monkeypatch, "makedirs")

    # ----- shutil ----------------------------------------------------------
    _wrap_shutil_two_arg(monkeypatch, "copy")
    _wrap_shutil_two_arg(monkeypatch, "copy2")
    _wrap_shutil_two_arg(monkeypatch, "copyfile")
    _wrap_shutil_two_arg(monkeypatch, "copytree")
    _wrap_shutil_two_arg(monkeypatch, "move")
    _wrap_shutil_unary(monkeypatch, "rmtree")
    _wrap_shutil_chown(monkeypatch)

    # ----- pathlib.Path ----------------------------------------------------
    _wrap_path_method(monkeypatch, "write_text")
    _wrap_path_method(monkeypatch, "write_bytes")
    _wrap_path_method(monkeypatch, "unlink")
    _wrap_path_method(monkeypatch, "mkdir")
    _wrap_path_method(monkeypatch, "rmdir")
    _wrap_path_method(monkeypatch, "touch")
    _wrap_path_two_arg_method(monkeypatch, "rename")
    _wrap_path_two_arg_method(monkeypatch, "replace")
    _wrap_path_two_arg_method(monkeypatch, "symlink_to")
    _wrap_path_two_arg_method(monkeypatch, "hardlink_to")

    # ----- builtin open() --------------------------------------------------
    _wrap_open(monkeypatch)


# --- Wrappers ---------------------------------------------------------------


def _wrap_os_unary(monkeypatch: pytest.MonkeyPatch, fn_name: str) -> None:
    """Wrap a unary ``os`` function so it raises on read-only paths."""
    original = getattr(os, fn_name)

    def guarded(path, *args, **kwargs):
        if _is_under_readonly_prefix(path):
            _raise_readonly(f"os.{fn_name}", path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, fn_name, guarded)


def _wrap_os_two_arg(monkeypatch: pytest.MonkeyPatch, fn_name: str) -> None:
    """Wrap a two-arg ``os`` function (rename, replace, symlink, link)."""
    original = getattr(os, fn_name)

    def guarded(src, dst, *args, **kwargs):
        if _is_under_readonly_prefix(src) or _is_under_readonly_prefix(dst):
            _raise_readonly(f"os.{fn_name}", (src, dst))
        return original(src, dst, *args, **kwargs)

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


def _wrap_shutil_chown(monkeypatch: pytest.MonkeyPatch) -> None:
    """``shutil.chown(path, user=None, group=None)`` — guard the path arg."""
    original = shutil.chown

    def guarded(path, *args, **kwargs):
        if _is_under_readonly_prefix(path):
            _raise_readonly("shutil.chown", path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "chown", guarded)


def _wrap_path_method(monkeypatch: pytest.MonkeyPatch, method_name: str) -> None:
    """Wrap ``pathlib.Path.<method_name>`` so it raises on read-only paths."""
    original = getattr(Path, method_name)

    def guarded(self, *args, **kwargs):
        if _is_under_readonly_prefix(self):
            _raise_readonly(f"Path.{method_name}", self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, method_name, guarded)


def _wrap_path_two_arg_method(monkeypatch: pytest.MonkeyPatch, method_name: str) -> None:
    """Wrap a two-arg ``pathlib.Path`` method (rename/replace/symlink_to/hardlink_to).

    These methods take ``self`` and a target; either side under the read-only
    prefix is a violation.
    """
    original = getattr(Path, method_name)

    def guarded(self, target, *args, **kwargs):
        if _is_under_readonly_prefix(self) or _is_under_readonly_prefix(target):
            _raise_readonly(f"Path.{method_name}", (self, target))
        return original(self, target, *args, **kwargs)

    monkeypatch.setattr(Path, method_name, guarded)


@pytest.fixture
def gui_settings(tmp_path: Path) -> Generator[object]:
    """Provide an isolated :class:`Settings` for GUI tests.

    The config file lands in ``tmp_path / config.json`` so each test
    starts from a clean slate; the path is captured on the returned
    object so tests can re-read or assert against it.
    """
    from plex_renamer.config.settings import Settings  # noqa: PLC0415 — local import

    cfg = tmp_path / "config.json"
    # Use a guaranteed-nonexistent .env so first-run hydration finds
    # nothing. ``Settings.load`` short-circuits if the file doesn't exist.
    fake_env = tmp_path / "nonexistent.env"
    settings = Settings.load(config_path=cfg, dotenv_path=fake_env)
    yield settings


@pytest.fixture
def safe_tmp_path() -> Generator[Path]:
    """Provide a tmp path under the user's home dir, not under /private/var.

    The cleanup pass refuses any path under ``/var``, ``/private``,
    ``/tmp``, etc. (regardless of depth). On macOS, pytest's ``tmp_path``
    resolves through ``/private/var/folders/...`` and is therefore not
    usable as ``input_root`` for cleanup tests. We create a per-test
    directory under ``~/.cache/plex-renamer-tests/`` which lands at
    ``/Users/<user>/.cache/...`` — allowed because it's three components
    deep below ``/Users``.
    """
    home_cache = Path.home() / ".cache" / "plex-renamer-tests"
    home_cache.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(dir=str(home_cache)))
    try:
        yield tmpdir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


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
