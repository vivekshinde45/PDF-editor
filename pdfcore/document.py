"""Open, render, and save a PDF.

This module wraps a single PyMuPDF document. It exposes only what the rest of
the app needs: page count, a rendered raster of a page, and a safe save. It
never imports Qt — the render output is plain bytes that the UI converts to a
QImage.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

import fitz  # PyMuPDF


class PdfError(Exception):
    """Raised for open/save problems the UI should surface to the user."""


@dataclass(frozen=True)
class RenderedPage:
    """A rasterized page, ready to hand to the UI.

    ``samples`` is tightly packed RGB888 (3 bytes/pixel, no alpha). ``stride``
    is the number of bytes per row. ``scale`` is the zoom applied relative to
    the page's native 72-DPI point size, so the UI can map screen coordinates
    back to PDF points by dividing by ``scale``.
    """

    width: int
    height: int
    stride: int
    samples: bytes
    scale: float


class PdfDocument:
    """A mutable, in-memory PDF.

    Edits made via the editor module mutate the underlying ``fitz.Document``.
    Re-rendering a page therefore always reflects the current state, so the UI
    can render-after-edit to stay in sync with ground truth.
    """

    def __init__(self, doc: fitz.Document, path: str | None):
        self._doc = doc
        self.path = path

    # -- construction ---------------------------------------------------

    @classmethod
    def open(cls, path: str, password: str | None = None) -> "PdfDocument":
        if not os.path.exists(path):
            raise PdfError(f"File not found: {path}")
        try:
            doc = fitz.open(path)
        except Exception as exc:  # pragma: no cover - depends on file
            raise PdfError(f"Could not open PDF: {exc}") from exc

        if doc.needs_pass:
            if password is None:
                doc.close()
                raise PdfError("PDF is encrypted; a password is required.")
            if not doc.authenticate(password):
                doc.close()
                raise PdfError("Incorrect password.")

        if doc.page_count == 0:
            doc.close()
            raise PdfError("PDF has no pages.")

        return cls(doc, path)

    # -- read -----------------------------------------------------------

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    @property
    def fitz_doc(self) -> fitz.Document:
        """Escape hatch for sibling engine modules (blocks, editor)."""
        return self._doc

    def page(self, index: int) -> fitz.Page:
        if not 0 <= index < self.page_count:
            raise PdfError(f"Page {index} out of range (0..{self.page_count - 1}).")
        return self._doc.load_page(index)

    def render_page(self, index: int, scale: float = 1.0) -> RenderedPage:
        """Render a page to packed RGB888 bytes at the given zoom factor."""
        page = self.page(index)
        matrix = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return RenderedPage(
            width=pix.width,
            height=pix.height,
            stride=pix.stride,
            samples=bytes(pix.samples),
            scale=scale,
        )

    # -- write ----------------------------------------------------------

    def save_as(self, out_path: str) -> None:
        """Save a full-rewrite copy with fonts embedded/subset.

        Writes to a temp file in the destination directory, then atomically
        replaces the target. On any failure the destination is untouched.
        """
        out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
        os.close(fd)
        try:
            self._doc.save(
                tmp,
                garbage=4,      # drop unused objects
                deflate=True,   # compress streams
                clean=True,     # sanitize content
            )
            os.replace(tmp, out_path)
        except Exception as exc:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise PdfError(f"Could not save PDF: {exc}") from exc

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> "PdfDocument":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
