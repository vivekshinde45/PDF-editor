"""Controller-level tests: undo/redo bookkeeping and page-text extraction.

These exercise the Controller directly (no Qt needed) so the redo stack and
snapshot semantics are verified independently of the UI.
"""

from __future__ import annotations

from app.controller import Controller


def _value_span(ctrl, index, needle):
    return next(s for s in ctrl.spans(index) if needle in s.text)


def test_redo_unavailable_initially(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)
    assert not ctrl.can_undo()
    assert not ctrl.can_redo()


def test_redo_reapplies_undone_edit(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)
    span = _value_span(ctrl, 0, "12345")

    ctrl.edit_span(0, span, "Invoice number 99999")
    assert "99999" in " ".join(s.text for s in ctrl.spans(0))
    assert ctrl.can_undo()

    ctrl.undo()
    assert "12345" in " ".join(s.text for s in ctrl.spans(0))
    assert "99999" not in " ".join(s.text for s in ctrl.spans(0))
    assert ctrl.can_redo()

    ctrl.redo()
    assert "99999" in " ".join(s.text for s in ctrl.spans(0))
    assert not ctrl.can_redo()


def test_new_edit_clears_redo_stack(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)

    ctrl.edit_span(0, _value_span(ctrl, 0, "12345"), "Invoice number 99999")
    ctrl.undo()
    assert ctrl.can_redo()

    # A fresh edit must invalidate the redo history.
    ctrl.edit_span(0, _value_span(ctrl, 0, "12345"), "Invoice number 77777")
    assert not ctrl.can_redo()


def test_delete_then_undo_restores_text(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)

    ctrl.delete_span(0, _value_span(ctrl, 0, "12345"))
    assert "12345" not in " ".join(s.text for s in ctrl.spans(0))

    ctrl.undo()
    assert "12345" in " ".join(s.text for s in ctrl.spans(0))


def test_page_text_returns_document_text(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)
    text = ctrl.page_text(0)
    assert "12345" in text
    assert "business" in text.lower()
