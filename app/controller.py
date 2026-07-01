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
from pdfcore.editor import delete_span as _delete_span
from pdfcore.editor import duplicate_span as _duplicate_span
from pdfcore.editor import move_span as _move_span
from pdfcore.images import SignatureImage, extract_signature_images
from pdfcore.images import delete_signature as _delete_signature
from pdfcore.images import duplicate_signature as _duplicate_signature
from pdfcore.images import insert_signature as _insert_signature
from pdfcore.images import move_signature as _move_signature
from pdfcore.images import replace_signature as _replace_signature
from pdfcore.images import resize_signature as _resize_signature
from pdfcore.insert import insert_text_box as _insert_text_box
from pdfcore.search import Match, count_occurrences, find_spans, replace_occurrences
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
        self._redo: list[bytes] = []

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
        self._redo.clear()

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._undo.clear()
        self._redo.clear()

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

    def signatures(self, index: int) -> list[SignatureImage]:
        """Raster image blocks on a page, treated as editable signatures."""
        assert self._doc is not None
        return extract_signature_images(self._doc.page(index))

    def page_text(self, index: int) -> str:
        """The page's full text in reading order (for copy / export)."""
        assert self._doc is not None
        return self._doc.page(index).get_text("text")

    # -- edit -----------------------------------------------------------

    def edit_block(self, index: int, block: TextBlock, runs) -> EditResult:
        """``runs`` is a plain string or a list of (text, bold, italic) tuples."""
        assert self._doc is not None
        self._snapshot()
        result = apply_edit(self._doc.page(index), block, runs)
        if not result.ok:
            self._undo.pop()  # nothing changed; discard the snapshot
        return result

    def edit_span(self, index: int, span: Span, runs, block_spans=None,
                  size: float | None = None, color=None) -> EditResult:
        """Edit a span. Prefers surgical content-stream editing (perfect
        fidelity — keeps the original font/spacing); falls back to redraw.

        Surgical is used only when the edit changes text without changing style
        (bold/italic) and the new text is locatable + drawable in the original
        font. Otherwise (style change, text in an XObject, missing glyph) the
        redraw path runs, which also reflows the line.

        ``size`` / ``color`` optionally change the span's font size / colour;
        because surgical editing cannot change those, supplying either forces the
        redraw path. Passing neither preserves the original v1 behaviour exactly.
        """
        assert self._doc is not None
        self._snapshot()

        norm = _normalize(runs)
        new_text = "".join(t for t, _b, _i in norm)
        style_changed = any(b != span.bold or i != span.italic for _t, b, i in norm)
        size_changed = size is not None and abs(size - span.size) > 1e-6
        color_changed = color is not None and tuple(color) != tuple(span.color)
        appearance_changed = style_changed or size_changed or color_changed

        if not appearance_changed and new_text != span.text:
            occ = self._occurrence(index, span)
            new_bytes = surgical_replace(
                self._doc.fitz_doc.tobytes(), index, span.text, new_text, occ
            )
            if new_bytes is not None:
                self._reload(new_bytes)
                return EditResult(ok=True, fidelity=Fidelity.EXACT)

        result = apply_span_edit(
            self._doc.page(index), span, runs, block_spans,
            override_size=size, override_color=color,
        )
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

    def delete_span(self, index: int, span: Span) -> EditResult:
        """Delete a span's text (redact only). Undoable."""
        assert self._doc is not None
        self._snapshot()
        result = _delete_span(self._doc.page(index), span)
        if not result.ok:
            self._undo.pop()
        return result

    def duplicate_span(self, index: int, span: Span,
                       dx: float = 8.0, dy: float = 12.0) -> EditResult:
        """Draw a second copy of a span, offset by (dx, dy) points. Undoable."""
        assert self._doc is not None
        self._snapshot()
        result = _duplicate_span(self._doc.page(index), span, dx, dy)
        if not result.ok:
            self._undo.pop()
        return result

    def insert_text(self, index: int, rect, text: str, *,
                    font_name: str = "helv", size: float = 12.0,
                    color=(0.0, 0.0, 0.0)) -> EditResult:
        """Insert a NEW text box at ``rect`` (PDF points). Undoable."""
        assert self._doc is not None
        self._snapshot()
        result = _insert_text_box(
            self._doc.page(index), rect, text,
            font_name=font_name, size=size, color=color,
        )
        if not result.ok:
            self._undo.pop()
        return result

    def move_signature(
        self, index: int, sig: SignatureImage, dx: float, dy: float
    ) -> EditResult:
        assert self._doc is not None
        if dx == 0 and dy == 0:
            return EditResult(ok=True, fidelity=Fidelity.EXACT)
        self._snapshot()
        result = _move_signature(self._doc.page(index), sig, dx, dy)
        if not result.ok:
            self._undo.pop()
        return result

    def delete_signature(self, index: int, sig: SignatureImage) -> EditResult:
        assert self._doc is not None
        self._snapshot()
        result = _delete_signature(self._doc.page(index), sig)
        if not result.ok:
            self._undo.pop()
        return result

    def duplicate_signature(
        self, index: int, sig: SignatureImage, dx: float = 8.0, dy: float = 12.0
    ) -> EditResult:
        assert self._doc is not None
        self._snapshot()
        result = _duplicate_signature(self._doc.page(index), sig, dx, dy)
        if not result.ok:
            self._undo.pop()
        return result

    def replace_signature(self, index: int, sig: SignatureImage, image_path: str) -> EditResult:
        assert self._doc is not None
        self._snapshot()
        result = _replace_signature(self._doc.page(index), sig, image_path)
        if not result.ok:
            self._undo.pop()
        return result

    def resize_signature(self, index: int, sig: SignatureImage, width: float) -> EditResult:
        assert self._doc is not None
        current_width = sig.bbox[2] - sig.bbox[0]
        if abs(width - current_width) < 1e-6:
            return EditResult(ok=True, fidelity=Fidelity.EXACT)
        self._snapshot()
        result = _resize_signature(self._doc.page(index), sig, width)
        if not result.ok:
            self._undo.pop()
        return result

    def insert_signature(self, index: int, image_path: str, x: float, y: float) -> EditResult:
        assert self._doc is not None
        self._snapshot()
        result = _insert_signature(self._doc.page(index), image_path, x, y)
        if not result.ok:
            self._undo.pop()
        return result

    def delete_page(self, index: int) -> None:
        """Delete a page from the document. Undoable."""
        assert self._doc is not None
        if not 0 <= index < self.page_count:
            raise IndexError(f"Page {index} out of range.")
        if self.page_count <= 1:
            raise ValueError("Cannot delete the only page in the document.")
        self._snapshot()
        self._doc.fitz_doc.delete_page(index)

    # -- find / replace -------------------------------------------------

    def find(self, index: int, query: str, case_sensitive: bool = False) -> list[Match]:
        """Matches of ``query`` within the editable spans on a page."""
        return find_spans(self.spans(index), query, case_sensitive)

    def replace_in_span(self, index: int, match: Match, replacement: str) -> EditResult:
        """Replace a single matched region (within its span) via the edit path."""
        span = match.span
        new_text = span.text[:match.start] + replacement + span.text[match.end:]
        return self.edit_span(index, span, new_text)

    def replace_all_in_page(self, index: int, query: str, replacement: str,
                            case_sensitive: bool = False) -> int:
        """Replace every occurrence of ``query`` on a page. Returns the count.

        Re-extracts spans each iteration (an edit may reload the document) and
        keys progress by span origin, so it terminates even when ``replacement``
        itself contains ``query``.
        """
        if not query:
            return 0
        total = 0
        processed: set[tuple[float, float]] = set()
        while True:
            target = None
            for s in self.spans(index):
                key = (round(s.origin[0], 1), round(s.origin[1], 1))
                if key in processed:
                    continue
                if count_occurrences(s.text, query, case_sensitive) > 0:
                    target = s
                    break
            if target is None:
                break
            n = count_occurrences(target.text, query, case_sensitive)
            new_text = replace_occurrences(target.text, query, replacement, case_sensitive)
            result = self.edit_span(index, target, new_text)
            processed.add((round(target.origin[0], 1), round(target.origin[1], 1)))
            if result.ok:
                total += n
        return total

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

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> None:
        if not self._undo or self._doc is None:
            return
        # Save the current state so redo can return to it, then revert.
        self._redo.append(self._doc.fitz_doc.tobytes())
        self._reload(self._undo.pop())

    def redo(self) -> None:
        if not self._redo or self._doc is None:
            return
        # Save the current state for undo, then re-apply the redone state.
        self._undo.append(self._doc.fitz_doc.tobytes())
        self._reload(self._redo.pop())

    # -- save -----------------------------------------------------------

    def save_as(self, out_path: str) -> None:
        assert self._doc is not None
        self._doc.save_as(out_path)

    # -- internals ------------------------------------------------------

    def _snapshot(self) -> None:
        """Push a full copy of the current document onto the undo stack.

        A new edit invalidates the redo history (standard undo/redo semantics).
        """
        assert self._doc is not None
        self._undo.append(self._doc.fitz_doc.tobytes())
        self._redo.clear()
