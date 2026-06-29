"""Mediates between the UI and the pdfcore engine.

Holds the open document, the current page index, the per-page block cache, and a
simple undo stack of saved document snapshots. After every edit it asks the
engine to re-extract blocks so the UI always works against ground truth.
"""

from __future__ import annotations

import fitz

from pdfcore.blocks import Span, TextBlock, extract_blocks
from pdfcore.document import PdfDocument
from pdfcore.editor import EditResult, Fidelity, apply_edit, apply_span_edit
from pdfcore.editor import move_span as _move_span
from pdfcore.surgical import surgical_replace


def _normalize(runs) -> list[tuple[str, bool, bool]]:
    """Normalize a plain string or (text, bold[, italic]) tuples to triples."""
    if isinstance(runs, str):
        return [(runs, False, False)]
    out = []
    for r in runs:
        if r[0] == "":
            continue
        out.append((r[0], bool(r[1]) if len(r) > 1 else False,
                    bool(r[2]) if len(r) > 2 else False))
    return out


class Controller:
    def __init__(self) -> None:
        self._doc: PdfDocument | None = None
        self._undo: list[bytes] = []

    # -- lifecycle ------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._doc is not None

    @property
    def page_count(self) -> int:
        return self._doc.page_count if self._doc else 0

    @property
    def source_path(self) -> str | None:
        return self._doc.path if self._doc else None

    def open(self, path: str, password: str | None = None) -> None:
        if self._doc is not None:
            self._doc.close()
        self._doc = PdfDocument.open(path, password)
        self._undo.clear()

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._undo.clear()

    # -- read -----------------------------------------------------------

    def render(self, index: int, scale: float):
        assert self._doc is not None
        return self._doc.render_page(index, scale)

    def blocks(self, index: int) -> list[TextBlock]:
        assert self._doc is not None
        return extract_blocks(self._doc.page(index))

    def spans(self, index: int) -> list[Span]:
        """All editable spans on a page (the span-level edit targets)."""
        return [s for b in self.blocks(index) if b.editable for s in b.spans]

    # -- edit -----------------------------------------------------------

    def edit_block(self, index: int, block: TextBlock, runs) -> EditResult:
        """``runs`` is a plain string or a list of (text, bold, italic) tuples."""
        assert self._doc is not None
        self._snapshot()
        result = apply_edit(self._doc.page(index), block, runs)
        if not result.ok:
            self._undo.pop()  # nothing changed; discard the snapshot
        return result

    def edit_span(self, index: int, span: Span, runs, block_spans=None) -> EditResult:
        """Edit a span. Prefers surgical content-stream editing (perfect
        fidelity — keeps the original font/spacing); falls back to redraw.

        Surgical is used only when the edit changes text without changing style
        (bold/italic) and the new text is locatable + drawable in the original
        font. Otherwise (style change, text in an XObject, missing glyph) the
        redraw path runs, which also reflows the line.
        """
        assert self._doc is not None
        self._snapshot()

        norm = _normalize(runs)
        new_text = "".join(t for t, _b, _i in norm)
        style_changed = any(b != span.bold or i != span.italic for _t, b, i in norm)

        if not style_changed and new_text != span.text:
            occ = self._occurrence(index, span)
            new_bytes = surgical_replace(
                self._doc.fitz_doc.tobytes(), index, span.text, new_text, occ
            )
            if new_bytes is not None:
                self._reload(new_bytes)
                return EditResult(ok=True, fidelity=Fidelity.EXACT)

        result = apply_span_edit(self._doc.page(index), span, runs, block_spans)
        if not result.ok:
            self._undo.pop()
        return result

    def move_span(self, index: int, span: Span, dx: float, dy: float) -> EditResult:
        """Move a span by (dx, dy) PDF points. Redacts the original and redraws
        it at the new position (reusing the original font). Undoable."""
        assert self._doc is not None
        if dx == 0 and dy == 0:
            return EditResult(ok=True, fidelity=Fidelity.EXACT)
        self._snapshot()
        result = _move_span(self._doc.page(index), span, dx, dy)
        if not result.ok:
            self._undo.pop()
        return result

    def _occurrence(self, index: int, span: Span) -> int:
        """How many editable spans with the same text precede ``span`` on the page."""
        count = 0
        for b in self.blocks(index):
            if not b.editable:
                continue
            for s in b.spans:
                if s is span:
                    return count
                if s.text == span.text:
                    count += 1
        return count

    def _reload(self, pdf_bytes: bytes) -> None:
        path = self._doc.path if self._doc else None
        if self._doc is not None:
            self._doc.close()
        self._doc = PdfDocument(fitz.open(stream=pdf_bytes, filetype="pdf"), path)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo(self) -> None:
        if not self._undo:
            return
        import fitz

        data = self._undo.pop()
        path = self._doc.path if self._doc else None
        if self._doc is not None:
            self._doc.close()
        self._doc = PdfDocument(fitz.open(stream=data, filetype="pdf"), path)

    # -- save -----------------------------------------------------------

    def save_as(self, out_path: str) -> None:
        assert self._doc is not None
        self._doc.save_as(out_path)

    # -- internals ------------------------------------------------------

    def _snapshot(self) -> None:
        """Push a full copy of the current document onto the undo stack."""
        assert self._doc is not None
        self._undo.append(self._doc.fitz_doc.tobytes())
