"""pdfcore — Qt-free PDF engine for the editor.

Layers above this (the Qt UI) depend on pdfcore; pdfcore depends on nothing
from the UI. This keeps the engine independently unit-testable.
"""

from .document import PdfDocument
from .blocks import Span, TextBlock, extract_blocks
from .editor import EditResult, Fidelity, apply_edit, apply_span_edit

__all__ = [
    "PdfDocument",
    "Span",
    "TextBlock",
    "extract_blocks",
    "EditResult",
    "Fidelity",
    "apply_edit",
    "apply_span_edit",
]
