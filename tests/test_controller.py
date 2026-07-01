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


def test_each_page_renders_and_reads_independently(multipage_pdf):
    ctrl = Controller()
    ctrl.open(multipage_pdf)
    assert ctrl.page_count == 3
    for i in range(3):
        assert f"marker {i + 1}" in ctrl.page_text(i)
        rendered = ctrl.render(i, 1.0)
        assert rendered.width > 0 and rendered.height > 0


def test_find_via_controller(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)
    hits = ctrl.find(0, "12345")
    assert len(hits) == 1
    assert hits[0].span.text[hits[0].start:hits[0].end] == "12345"


def test_replace_in_span(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)
    match = ctrl.find(0, "12345")[0]
    result = ctrl.replace_in_span(0, match, "99999")
    assert result.ok
    full = ctrl.page_text(0)
    assert "99999" in full
    assert "12345" not in full


def test_replace_all_in_page_counts_and_terminates(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)
    # "number" appears once; replacing with text that contains it must still
    # terminate (origin-keyed progress guard).
    count = ctrl.replace_all_in_page(0, "number", "number-NO")
    assert count >= 1
    assert "number-NO" in ctrl.page_text(0)


def test_replace_all_no_match_returns_zero(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)
    assert ctrl.replace_all_in_page(0, "zzzzz", "x") == 0


def test_insert_text_via_controller_is_undoable(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)
    result = ctrl.insert_text(0, (72, 600, 400, 660), "Inserted via controller")
    assert result.ok
    assert "Inserted via controller" in ctrl.page_text(0)
    assert ctrl.can_undo()
    ctrl.undo()
    assert "Inserted via controller" not in ctrl.page_text(0)


def test_edit_targets_only_its_own_page(multipage_pdf):
    ctrl = Controller()
    ctrl.open(multipage_pdf)
    span = next(s for s in ctrl.spans(1) if "marker 2" in s.text)

    ctrl.edit_span(1, span, "Page marker EDITED")
    assert "EDITED" in ctrl.page_text(1)
    # Other pages are untouched.
    assert "marker 1" in ctrl.page_text(0)
    assert "EDITED" not in ctrl.page_text(0)
    assert "marker 3" in ctrl.page_text(2)


def test_delete_page_then_undo_restores(multipage_pdf):
    ctrl = Controller()
    ctrl.open(multipage_pdf)

    ctrl.delete_page(1)
    assert ctrl.page_count == 2
    assert "Page marker 1" in ctrl.page_text(0)
    assert "Page marker 3" in ctrl.page_text(1)
    assert ctrl.can_undo()

    ctrl.undo()
    assert ctrl.page_count == 3
    assert "Page marker 2" in ctrl.page_text(1)


def test_delete_only_page_rejected(simple_pdf):
    ctrl = Controller()
    ctrl.open(simple_pdf)

    try:
        ctrl.delete_page(0)
    except ValueError as exc:
        assert "only page" in str(exc)
    else:
        raise AssertionError("delete_page should reject a one-page document")
