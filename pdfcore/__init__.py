"""pdfcore — Qt-free PDF engine for the editor.

Layers above this (the Qt UI) depend on pdfcore; pdfcore depends on nothing
from the UI. This keeps the engine independently unit-testable.
"""

from .document import PdfDocument
from .blocks import Span, TextBlock, extract_blocks
from .editor import EditResult, Fidelity, apply_edit, apply_span_edit
from .images import SignatureImage, extract_signature_images, resize_signature

__all__ = [
    "PdfDocument",
    "Span",
    "TextBlock",
    "SignatureImage",
    "extract_blocks",
    "extract_signature_images",
    "resize_signature",
    "EditResult",
    "Fidelity",
    "apply_edit",
    "apply_span_edit",
]
