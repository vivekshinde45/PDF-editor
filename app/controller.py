"""Mediates between the UI and the pdfcore engine.

Holds the open document, the current page index, the per-page block cache, and a
simple undo stack of saved document snapshots. After every edit it asks the
engine to re-extract blocks so the UI always works against ground truth.
"""

from __future__ import annotations

from pdfcore.blocks import Span, TextBlock, extract_blocks
from pdfcore.document import PdfDocument
from pdfcore.editor import EditResult, apply_edit, apply_span_edit


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

    def edit_span(self, index: int, span: Span, runs) -> EditResult:
        """Edit a single span in place (preserves neighbouring columns)."""
        assert self._doc is not None
        self._snapshot()
        result = apply_span_edit(self._doc.page(index), span, runs)
        if not result.ok:
            self._undo.pop()
        return result

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
