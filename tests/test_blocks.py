from __future__ import annotations

from pdfcore.blocks import extract_blocks
from pdfcore.document import PdfDocument


def test_extracts_two_text_blocks(simple_pdf):
    with PdfDocument.open(simple_pdf) as doc:
        blocks = extract_blocks(doc.page(0))
        text_blocks = [b for b in blocks if b.editable]
        assert len(text_blocks) == 2
        joined = " | ".join(b.text for b in text_blocks)
        assert "Invoice number 12345" in joined
        assert "Thank you for your business" in joined


def test_block_carries_styling(simple_pdf):
    with PdfDocument.open(simple_pdf) as doc:
        blocks = [b for b in extract_blocks(doc.page(0)) if b.editable]
        span = blocks[0].primary_span
        assert span is not None
        assert span.size > 0
        assert len(span.font_name) > 0
        assert all(0.0 <= c <= 1.0 for c in span.color)


def test_spans_capture_bold_and_italic(styled_pdf):
    with PdfDocument.open(styled_pdf) as doc:
        blocks = [b for b in extract_blocks(doc.page(0)) if b.editable]
        runs = blocks[0].as_runs()
        joined = {text.strip(): (bold, italic) for text, bold, italic in runs}
        # The bold word is flagged bold; the italic word is flagged italic.
        assert any(b for (b, _i) in joined.values())
        assert any(i for (_b, i) in joined.values())
        assert joined.get("strong", (False, False))[0] is True
        assert joined.get("slanted", (False, False))[1] is True


def test_empty_page_has_no_editable_blocks(empty_pdf):
    with PdfDocument.open(empty_pdf) as doc:
        blocks = extract_blocks(doc.page(0))
        assert all(not b.editable for b in blocks)
