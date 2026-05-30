"""Worker that runs ``apply_plan_iter`` on a Qt thread.

The engine's :func:`plex_renamer.executor.copy.apply_plan_iter` yields
per-op events interleaved with the actual file copies (see PR #21 on
the WPF side). The Qt GUI used to call the synchronous
:func:`apply_plan` on the main thread, which froze the window during
multi-minute video-file copies. This worker re-uses the same streaming
entry point, runs it on a worker thread, and emits Qt signals per
event so the main thread can update a progress widget live.

Threading contract
------------------

* The worker is a :class:`QObject` designed to be moved to a
  :class:`QThread` via ``moveToThread``. Its :meth:`run` slot is the
  thread's entry point.
* ``apply_plan_iter`` is pure with respect to the GUI's models — it
  only touches the filesystem and the journal — so it is safe to run
  off the main thread. Anything that mutates Qt models (rebuilding
  the source/target panels, marking proposed ops, etc.) MUST happen
  on the main thread, before this worker spins up.
* Qt's signal-slot mechanism crosses thread boundaries by default
  (``Qt.AutoConnection`` becomes ``Qt.QueuedConnection`` when sender
  and receiver are on different threads), so emitting ``op_event``
  from the worker arrives on the main thread's event loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from plex_renamer.executor.copy import apply_plan_iter
from plex_renamer.planner.models import RenamePlan


class ApplyWorker(QObject):
    """QObject worker that iterates :func:`apply_plan_iter` off the GUI thread.

    Constructor parameters mirror the executor's :func:`apply_plan`
    signature so the GUI hands the worker exactly what the synchronous
    path would have received.

    Signals:

    * :attr:`op_event` — emitted once per executor event other than the
      terminal ``done``. Payload is the event dict from the engine:
      ``{"event": "op_started" | "op_verified" | "op_failed",
        "op_index", "total_ops", "source", "target", ...}``.
    * :attr:`apply_finished` — emitted exactly once when the iterator
      yields ``done``. Payload is the executor's ``ApplyResult`` (an
      object with ``succeeded`` / ``failed`` / ``cleanup_ran`` /
      ``journal_path`` attributes), to be translated into the GUI's
      :class:`RunReport` by the receiver via
      :meth:`Orchestrator.build_run_report`.
    * :attr:`apply_failed` — emitted if the iterator raises before
      reaching ``done``. Carries the exception message. Always
      mutually exclusive with :attr:`apply_finished`.
    """

    op_event = Signal(dict)
    apply_finished = Signal(object)  # ApplyResult-shape
    apply_failed = Signal(str)

    def __init__(
        self,
        plan: RenamePlan,
        journal_dir: Path,
        cleanup: bool,
        verify_hash: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._plan = plan
        self._journal_dir = journal_dir
        self._cleanup = cleanup
        self._verify_hash = verify_hash

    @Slot()
    def run(self) -> None:
        """Drive the executor iterator and emit events.

        Connected to :meth:`QThread.started` so it runs on the worker
        thread. Returns when the iterator's ``done`` event is observed
        OR when an exception escapes the iterator.
        """
        try:
            for event in apply_plan_iter(
                self._plan,
                journal_dir=self._journal_dir,
                cleanup=self._cleanup,
                verify_hash=self._verify_hash,
            ):
                if event.get("event") == "done":
                    result = event.get("result")
                    if result is None:
                        self.apply_failed.emit("apply_plan_iter yielded 'done' without a result")
                        return
                    self.apply_finished.emit(result)
                    return
                # Forward op_started / op_verified / op_failed to the
                # main thread for progress-widget updates.
                self.op_event.emit(event)
            # Iterator exhausted without 'done' — defensive.
            self.apply_failed.emit("apply_plan_iter exhausted without a 'done' event")
        except Exception as exc:  # noqa: BLE001 — surface every failure to the GUI
            self.apply_failed.emit(f"{type(exc).__name__}: {exc}")


__all__ = ["ApplyWorker"]


def _typecheck() -> None:
    """Static-typecheck hint helpers; not called at runtime."""
    _: dict[str, Any] = {}
    del _
