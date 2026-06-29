"""Headless smoke tests for the UI layer (Qt offscreen platform).

These don't assert pixel output; they verify the window constructs, a document
loads, block selection populates the panel, and an edit round-trips through the
controller without raising.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_window_constructs(qapp):
    from app.main_window import MainWindow

    win = MainWindow()
    assert win.windowTitle() == "PDF Editor"
    assert not win.btn_save.isEnabled()  # nothing open yet


def test_open_select_edit_cycle(qapp, simple_pdf):
    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(simple_pdf)
    win.page_spin.setMaximum(win.ctrl.page_count)
    win.page_spin.setValue(1)
    win._load_page()
    win._refresh_actions()

    assert win.btn_save.isEnabled()

    spans = win.ctrl.spans(0)
    assert spans
    target = next(s for s in spans if "12345" in s.text)

    win._on_span_clicked(target)
    assert win.edit.isEnabled()
    assert "12345" in win.edit.toPlainText()

    # Simulate selecting on the canvas then editing.
    win.view._selected = target
    win.edit.setPlainText("Invoice number 99999")
    win._on_apply()

    full = " ".join(s.text for s in win.ctrl.spans(0))
    assert "99999" in full
    assert "12345" not in full
    assert win.ctrl.can_undo()


def test_extract_runs_captures_bold(qapp, simple_pdf):
    from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor

    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(simple_pdf)
    win._load_page()

    win.edit.setEnabled(True)
    win.edit.setPlainText("normal bold")
    # Bold just the word "bold" (chars 7..11).
    cur = win.edit.textCursor()
    cur.setPosition(7)
    cur.setPosition(11, QTextCursor.MoveMode.KeepAnchor)
    fmt = QTextCharFormat()
    fmt.setFontWeight(QFont.Weight.Bold)
    cur.mergeCharFormat(fmt)

    runs = win._extract_runs()
    # Expect a non-bold prefix then a bold "bold" run.
    assert runs[0] == ("normal ", False, False)
    assert ("bold", True, False) in runs


def test_loading_span_preserves_its_style(qapp, styled_pdf):
    """Selecting a bold (or italic) span loads it with that style intact."""
    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(styled_pdf)
    win._load_page()

    bold_span = next(s for s in win.ctrl.spans(0) if s.bold)
    win.view._selected = bold_span
    win._on_span_clicked(bold_span)

    runs = win._extract_runs()
    assert all(bold for _t, bold, _i in runs), "bold span lost its style on load"
