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


def test_move_span_relocates_and_reselects(qapp, simple_pdf):
    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(simple_pdf)
    win._load_page()
    win._refresh_actions()

    target = next(s for s in win.ctrl.spans(0) if "12345" in s.text)
    ox, oy = target.origin
    win.view.select(target)
    win._on_span_clicked(target)

    # Drive the move the way the canvas would (drag / arrow key) emits it.
    win._on_span_moved(target, 30.0, 20.0)

    # Text survives, lands near the new origin, the move is undoable, and the
    # moved span is re-selected so further nudges keep working.
    moved = next(s for s in win.ctrl.spans(0) if "12345" in s.text)
    assert abs(moved.origin[0] - (ox + 30.0)) < 3.0
    assert abs(moved.origin[1] - (oy + 20.0)) < 3.0
    assert win.ctrl.can_undo()
    assert win.view.selected is not None and "12345" in win.view.selected.text


def test_delete_button_removes_selected_span(qapp, simple_pdf):
    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(simple_pdf)
    win._load_page()
    win._refresh_actions()

    target = next(s for s in win.ctrl.spans(0) if "12345" in s.text)
    win.view.select(target)
    win._on_span_clicked(target)
    assert win.btn_delete.isEnabled()

    win._on_delete()
    full = " ".join(s.text for s in win.ctrl.spans(0))
    assert "12345" not in full
    assert win.ctrl.can_undo()


def test_redo_button_state_tracks_history(qapp, simple_pdf):
    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(simple_pdf)
    win._load_page()
    win._refresh_actions()
    assert not win.btn_redo.isEnabled()

    target = next(s for s in win.ctrl.spans(0) if "12345" in s.text)
    win.view.select(target)
    win.edit.setPlainText("Invoice number 99999")
    win._on_apply()

    win._on_undo()
    assert win.btn_redo.isEnabled()
    win._on_redo()
    assert not win.btn_redo.isEnabled()
    assert "99999" in " ".join(s.text for s in win.ctrl.spans(0))


def test_copy_button_puts_text_on_clipboard(qapp, simple_pdf):
    from PySide6.QtGui import QGuiApplication

    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(simple_pdf)
    win._load_page()

    target = next(s for s in win.ctrl.spans(0) if "12345" in s.text)
    win.view.select(target)
    win._on_span_clicked(target)
    win._on_copy()

    assert "12345" in QGuiApplication.clipboard().text()


def test_navigation_changes_current_page(qapp, multipage_pdf):
    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(multipage_pdf)
    win.page_spin.setMaximum(win.ctrl.page_count)
    win.thumbs.populate(win.ctrl)
    win.page_spin.setValue(1)
    win._load_page()

    assert win._page_index == 0
    win._on_next_page()
    assert win._page_index == 1
    assert win.thumbs.currentRow() == 1  # thumbnail stays in sync
    win._on_prev_page()
    assert win._page_index == 0
    # Home/End jump to first/last.
    win.page_spin.setValue(win.ctrl.page_count)
    assert win._page_index == 2


def test_thumbnail_click_navigates(qapp, multipage_pdf):
    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(multipage_pdf)
    win.page_spin.setMaximum(win.ctrl.page_count)
    win.thumbs.populate(win.ctrl)
    win._load_page()

    # Picking a thumbnail drives the current page.
    win._on_thumbnail_selected(2)
    assert win._page_index == 2


def test_thumbnails_render_at_small_scale(qapp, multipage_pdf):
    from app.main_window import MainWindow
    from app.thumbnail_bar import _THUMB_SCALE

    win = MainWindow()
    win.ctrl.open(multipage_pdf)
    win.thumbs.populate(win.ctrl)

    assert win.thumbs.count() == 3
    # Thumbnails are rendered much smaller than the editing canvas (2x).
    assert _THUMB_SCALE < 1.0
    thumb = win.ctrl.render(0, _THUMB_SCALE)
    full = win.ctrl.render(0, 2.0)
    assert thumb.width < full.width


def test_edit_on_second_page_via_ui(qapp, multipage_pdf):
    from app.main_window import MainWindow

    win = MainWindow()
    win.ctrl.open(multipage_pdf)
    win.page_spin.setMaximum(win.ctrl.page_count)
    win.thumbs.populate(win.ctrl)
    win._load_page()

    win._on_next_page()  # go to page 2 (index 1)
    target = next(s for s in win.ctrl.spans(1) if "marker 2" in s.text)
    win.view.select(target)
    win.edit.setPlainText("Page marker EDITED")
    win._on_apply()

    assert "EDITED" in win.ctrl.page_text(1)
    assert "EDITED" not in win.ctrl.page_text(0)


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
