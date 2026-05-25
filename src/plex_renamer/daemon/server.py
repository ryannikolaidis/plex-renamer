"""JSON-RPC 2.0 daemon loop.

Reads newline-delimited JSON-RPC requests from ``stdin``, dispatches to
the handlers in :mod:`plex_renamer.daemon.methods`, and writes responses
to ``stdout``. Each request and response is one JSON object per line.

Wire format
-----------

Request::

    {"jsonrpc": "2.0", "id": <int|str>, "method": "<name>", "params": {...}}

Response::

    {"jsonrpc": "2.0", "id": <same>, "result": {...}}
    {"jsonrpc": "2.0", "id": <same>, "error": {"code": <int>, "message": "..."}}

Streaming methods (currently only ``apply_plan``) emit zero or more
progress notifications first::

    {"jsonrpc": "2.0", "method": "progress", "params": {"id": <req_id>, ...}}

followed by exactly one ``result`` response that closes the request. The
shell tracks the request by ``id`` and matches the progress notifications
to the original request via ``params.id``.

Shutdown
--------

Two ways to end the loop cleanly:

1. Send ``{"jsonrpc": "2.0", "id": <n>, "method": "shutdown"}``. The
   server responds with ``{"result": {"ok": true}}`` and returns.
2. Close the daemon's stdin (EOF). The loop returns with no extra output.

SIGINT (Ctrl+C) is caught by Python's default ``KeyboardInterrupt``
handler and the loop exits with code 0. SIGTERM is NOT installed —
on SIGTERM the OS terminates the process without an orderly
shutdown. Shells should send ``shutdown`` or close stdin before
terminating the child.

Parse errors emit a JSON-RPC error with ``id: null`` (per the spec).
Unknown methods, bad params, and handler exceptions emit errors with
the original ``id`` preserved so the client can resolve its pending
promise.
"""

from __future__ import annotations

import io
import json
import os
import runpy
import sys
from typing import Any, BinaryIO, TextIO

from plex_renamer.daemon import methods, schemas


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Entry point for the ``plex-renamer-engined`` script.

    ``stdin`` and ``stdout`` default to the process's real handles but
    can be overridden by tests (and conveniently by the test harness
    when it routes through ``subprocess.PIPE``).
    """
    # Make sure stdout is unbuffered: every response line MUST flush so
    # the shell can read it immediately. ``sys.stdout`` is typically
    # block-buffered when redirected to a pipe, which would hang the
    # shell's response promise.
    in_stream = stdin if stdin is not None else _ensure_text_stream(sys.stdin, "r")
    out_stream = stdout if stdout is not None else _ensure_text_stream(sys.stdout, "w")

    # Optional test/bootstrap hook: a Python file path supplied via
    # ``PLEX_RENAMER_DAEMON_BOOTSTRAP`` runs before the loop starts.
    # Tests use this to swap the TMDB factory with a fake so the
    # daemon-as-subprocess doesn't need a live TMDB key. The bootstrap
    # script runs with the daemon's globals available; the typical
    # shape is to import ``methods`` and call ``set_collaborators``.
    #
    # Hard-disable the hook in frozen (PyInstaller) builds. The
    # production binary an end user runs from the installed location
    # must NOT honor an env var that executes arbitrary Python. Tests
    # exercising the daemon-as-subprocess use the source-tree
    # ``uv run plex-renamer-engined`` entry point where ``sys.frozen``
    # is unset.
    bootstrap = os.environ.get("PLEX_RENAMER_DAEMON_BOOTSTRAP")
    if bootstrap:
        if getattr(sys, "frozen", False):
            sys.stderr.write(
                "plex-renamer-engined: PLEX_RENAMER_DAEMON_BOOTSTRAP "
                "ignored in frozen build (test-only hook).\n"
            )
            sys.stderr.flush()
        else:
            sys.stderr.write(f"plex-renamer-engined: running bootstrap hook {bootstrap!r}\n")
            sys.stderr.flush()
            runpy.run_path(bootstrap)

    try:
        _serve(in_stream, out_stream)
    except KeyboardInterrupt:
        return 0
    return 0


def _serve(stdin: TextIO, stdout: TextIO) -> None:
    """The actual loop. Split out so tests can drive it directly."""
    while True:
        line = stdin.readline()
        if not line:
            # EOF — clean shutdown.
            return
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(stdout, schemas.make_error(None, schemas.ERR_PARSE, f"parse error: {exc}"))
            continue
        if not isinstance(request, dict):
            _write(
                stdout,
                schemas.make_error(
                    None, schemas.ERR_INVALID_REQUEST, "request must be a JSON object"
                ),
            )
            continue

        method = request.get("method")
        params = request.get("params") or {}
        req_id = request.get("id")

        # Special-case shutdown so callers don't depend on it being in
        # METHODS (which keeps the dispatch table focused on engine
        # methods).
        if method == "shutdown":
            _write(stdout, schemas.make_response(req_id, {"ok": True}))
            return

        if not isinstance(method, str) or method not in methods.METHODS:
            _write(
                stdout,
                schemas.make_error(
                    req_id, schemas.ERR_METHOD_NOT_FOUND, f"method not found: {method!r}"
                ),
            )
            continue

        handler = methods.METHODS[method]
        try:
            if method in methods.STREAMING_METHODS:
                _dispatch_streaming(stdout, req_id, method, handler, params)
            else:
                result = handler(params)
                _write(stdout, schemas.make_response(req_id, result))
        except ValueError as exc:
            _write(
                stdout,
                schemas.make_error(req_id, schemas.ERR_INVALID_PARAMS, str(exc)),
            )
        except Exception as exc:  # noqa: BLE001 — surface everything as JSON-RPC errors
            _write(
                stdout,
                schemas.make_error(
                    req_id,
                    schemas.ERR_APP,
                    f"{type(exc).__name__}: {exc}",
                ),
            )


def _dispatch_streaming(
    stdout: TextIO,
    req_id: Any,
    method: str,
    handler: Any,
    params: dict[str, Any],
) -> None:
    """Drive a streaming handler that yields progress + a final ``done``.

    The handler returns an iterator; every yielded dict that is NOT a
    ``{"event": "done", "result": ...}`` envelope is wrapped as a
    progress notification and emitted on stdout. The trailing ``done``
    envelope is unwrapped to the JSON-RPC ``result`` of the request.

    If the handler exhausts its iterator without emitting a ``done``
    envelope, we emit an internal-error response so the shell's promise
    doesn't hang forever.
    """
    iterator = handler(params)
    final_result: Any = None
    saw_done = False
    for event in iterator:
        if isinstance(event, dict) and event.get("event") == "done":
            final_result = event.get("result")
            saw_done = True
            break
        # Wrap as a progress notification carrying the request id so the
        # shell can match it to the pending promise. The request id is
        # written last so a future event payload can never accidentally
        # clobber it.
        notification_params: dict[str, Any] = {}
        if isinstance(event, dict):
            notification_params.update(event)
        notification_params["id"] = req_id
        _write(stdout, schemas.make_notification("progress", notification_params))
    if not saw_done:
        _write(
            stdout,
            schemas.make_error(
                req_id,
                schemas.ERR_INTERNAL,
                f"streaming method {method!r} returned without a 'done' event",
            ),
        )
        return
    _write(stdout, schemas.make_response(req_id, final_result))


def _write(stream: TextIO, message: dict[str, Any]) -> None:
    """Write a single JSON object as one newline-terminated line and flush."""
    stream.write(json.dumps(message, separators=(",", ":")))
    stream.write("\n")
    stream.flush()


def _ensure_text_stream(stream: TextIO | BinaryIO, mode: str) -> TextIO:
    """Coerce ``stream`` to a line-buffered text stream.

    PyInstaller-built Windows executables sometimes hand us a binary
    stdin/stdout; wrap them in ``io.TextIOWrapper`` so JSON-RPC's
    text-oriented framing works.
    """
    if isinstance(stream, io.TextIOBase):
        return stream
    if hasattr(stream, "buffer"):
        # Already a text wrapper from sys.stdin/sys.stdout when the
        # bootloader cooperated.
        return stream  # type: ignore[return-value]
    raw = getattr(stream, "buffer", stream)
    return io.TextIOWrapper(raw, encoding="utf-8", line_buffering=True, write_through=True)


if __name__ == "__main__":
    raise SystemExit(main())
