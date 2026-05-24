"""Post-run report renders counts and wires the Undo button to a signal
that carries the journal path. The actual ``undo_batch`` call is owned
by the orchestrator; the widget just emits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def test_run_report_renders_counts(qtbot, tmp_path) -> None:
    from plex_renamer.gui.models import RunReport
    from plex_renamer.gui.run_report import RunReportWidget

    widget = RunReportWidget()
    qtbot.addWidget(widget)
    journal = tmp_path / "j.json"
    journal.write_text("{}", encoding="utf-8")
    report = RunReport(
        succeeded=3,
        skipped=1,
        errored=2,
        journal_path=journal,
        error_messages=("boom on op 5", "boom on op 7"),
    )
    widget.set_report(report)

    assert widget._succeeded_label.text() == "3"
    assert widget._skipped_label.text() == "1"
    assert widget._errored_label.text() == "2"
    assert widget._errors_list.count() == 2
    assert widget._undo_btn.isEnabled() is True


def test_undo_button_disabled_without_journal(qtbot) -> None:
    from plex_renamer.gui.models import RunReport
    from plex_renamer.gui.run_report import RunReportWidget

    widget = RunReportWidget()
    qtbot.addWidget(widget)
    widget.set_report(RunReport())
    assert widget._undo_btn.isEnabled() is False


def test_undo_button_emits_journal_path(qtbot, tmp_path) -> None:
    from plex_renamer.gui.models import RunReport
    from plex_renamer.gui.run_report import RunReportWidget

    widget = RunReportWidget()
    qtbot.addWidget(widget)
    journal = tmp_path / "j.json"
    journal.write_text("{}", encoding="utf-8")
    widget.set_report(RunReport(succeeded=1, journal_path=journal))

    received: list[Path] = []
    widget.undo_requested.connect(received.append)

    widget._on_undo()

    assert received == [journal]
