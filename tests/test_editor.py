from __future__ import annotations

from pdfcore.blocks import extract_blocks
from pdfcore.document import PdfDocument
from pdfcore.editor import Fidelity, apply_edit
from pdfcore.fonts import resolve_font


def _first_block_with(doc, needle):
    for b in extract_blocks(doc.page(0)):
        if b.editable and needle in b.text:
            return b
    raise AssertionError(f"no block containing {needle!r}")


def test_edit_replaces_text_on_reextraction(simple_pdf, tmp_path):
    with PdfDocument.open(simple_pdf) as doc:
        page = doc.page(0)
        block = _first_block_with(doc, "Invoice number 12345")

        result = apply_edit(page, block, "Invoice number 99999")
        assert result.ok

        # Re-extract from the mutated page: old text gone, new text present.
        full = " ".join(b.text for b in extract_blocks(doc.page(0)) if b.editable)
        assert "12345" not in full
        assert "99999" in full

        # And it survives a save/reopen round-trip.
        out = tmp_path / "edited.pdf"
        doc.save_as(str(out))
    with PdfDocument.open(str(out)) as doc2:
        full2 = " ".join(b.text for b in extract_blocks(doc2.page(0)) if b.editable)
        assert "99999" in full2


def test_overflow_flag_on_long_text(simple_pdf):
    with PdfDocument.open(simple_pdf) as doc:
        page = doc.page(0)
        block = _first_block_with(doc, "Invoice number 12345")
        result = apply_edit(page, block, "X " * 400)  # far too long for the box
        assert result.ok
        assert result.fidelity == Fidelity.OVERFLOW


def test_non_editable_block_is_rejected(simple_pdf):
    from pdfcore.blocks import TextBlock

    with PdfDocument.open(simple_pdf) as doc:
        page = doc.page(0)
        block = TextBlock(bbox=(0, 0, 10, 10), editable=False, reason_if_not="nope")
        result = apply_edit(page, block, "anything")
        assert not result.ok


def test_multiline_tight_block_does_not_lose_text(tight_paragraph_pdf):
    """Regression: a multi-line block has a TIGHT glyph bbox. Reinserting must
    not overflow into nothing — the original bug deleted the whole paragraph.
    """
    with PdfDocument.open(tight_paragraph_pdf) as doc:
        block = _first_block_with(doc, "Account holder")
        assert block.line_count >= 2

        # A replacement longer than the original needs more vertical space than
        # the tight box. The original bug dropped ALL text on overflow; the fix
        # must keep the text (growing the box downward), never silently vanish.
        longer = (
            "This replacement is several lines long and clearly needs far more "
            "vertical room than two lines so it will overflow the original box."
        )
        result = apply_edit(doc.page(0), block, longer)
        assert result.ok
        remaining = " ".join(b.text for b in extract_blocks(doc.page(0)) if b.editable)
        assert "replacement" in remaining.lower()
        assert remaining.strip() != ""  # text must not vanish


def test_multiline_block_reflows_and_roundtrips(multiline_pdf, tmp_path):
    with PdfDocument.open(multiline_pdf) as doc:
        page = doc.page(0)
        block = _first_block_with(doc, "First line")
        assert block.line_count >= 2  # exercises the insert_textbox (reflow) path

        result = apply_edit(page, block, "Replacement line one.\nReplacement line two.")
        assert result.ok

        full = " ".join(b.text for b in extract_blocks(doc.page(0)) if b.editable)
        assert "First line" not in full
        assert "Replacement line one." in full
        assert "Replacement line two." in full


def _nonwhite_pixel_count(page):
    import fitz

    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    samples = pix.samples
    count = 0
    for i in range(0, len(samples), 3):
        if samples[i] < 250 or samples[i + 1] < 250 or samples[i + 2] < 250:
            count += 1
    return count


def test_edit_preserves_background_graphics(graphics_pdf):
    """Editing text over a colored band must not leave a white patch.

    We compare colored (non-white) pixel coverage before and after the edit.
    A redaction that painted a white rectangle would REMOVE colored pixels;
    the count must stay close (the blue band + red box survive).
    """
    with PdfDocument.open(graphics_pdf) as doc:
        before = _nonwhite_pixel_count(doc.page(0))
        block = _first_block_with(doc, "Header on blue band")
        result = apply_edit(doc.page(0), block, "New header content")
        assert result.ok
        after = _nonwhite_pixel_count(doc.page(0))

    # The colored graphics dominate the colored-pixel count; if the redaction
    # had stamped a white box over the band we'd lose a large fraction of them.
    assert after > before * 0.9


def test_embedded_font_reused_no_substitution(embedded_font_pdf):
    """Editing a block whose embedded font covers the new ASCII text must NOT
    substitute — fidelity is exact and the font is preserved."""
    with PdfDocument.open(embedded_font_pdf) as doc:
        block = _first_block_with(doc, "embedded")
        result = apply_edit(doc.page(0), block, "Edited embedded text here")
        assert result.ok
        assert result.fidelity == Fidelity.EXACT


def test_missing_glyph_falls_back_to_substitution(embedded_font_pdf):
    """A character the embedded font lacks (CJK) forces substitution + warning."""
    with PdfDocument.open(embedded_font_pdf) as doc:
        block = _first_block_with(doc, "embedded")
        result = apply_edit(doc.page(0), block, "Edited 中文 text")
        assert result.ok
        assert result.fidelity == Fidelity.FONT_SUBSTITUTED


def _dark_pixels(page):
    import fitz

    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    s = pix.samples
    return sum(1 for i in range(0, len(s), 3) if s[i] < 100)


def test_bold_run_adds_ink(simple_pdf):
    """A bold run must render thicker (more dark ink) than the same text plain."""
    with PdfDocument.open(simple_pdf) as doc:
        block = _first_block_with(doc, "Invoice number 12345")
        apply_edit(doc.page(0), block, [("Sample heading text", False)])
        plain = _dark_pixels(doc.page(0))

    with PdfDocument.open(simple_pdf) as doc:
        block = _first_block_with(doc, "Invoice number 12345")
        apply_edit(doc.page(0), block, [("Sample heading text", True)])
        bold = _dark_pixels(doc.page(0))

    assert bold > plain * 1.1  # bold is meaningfully heavier


def test_move_span_relocates_text(simple_pdf):
    """Moving a span redraws it at origin+(dx,dy); the text survives at the new
    location and nothing is dropped."""
    from pdfcore.editor import move_span

    with PdfDocument.open(simple_pdf) as doc:
        block = _first_block_with(doc, "Invoice number 12345")
        span = block.spans[0]
        ox, oy = span.origin

        result = move_span(doc.page(0), span, 40.0, 25.0)
        assert result.ok

        moved = None
        for b in extract_blocks(doc.page(0)):
            for s in b.spans:
                if "12345" in s.text:
                    moved = s
        assert moved is not None, "moved text vanished"
        assert abs(moved.origin[0] - (ox + 40.0)) < 3.0
        assert abs(moved.origin[1] - (oy + 25.0)) < 3.0


def test_move_off_page_flags_overflow(simple_pdf):
    from pdfcore.editor import Fidelity as F
    from pdfcore.editor import move_span

    with PdfDocument.open(simple_pdf) as doc:
        block = _first_block_with(doc, "Invoice number 12345")
        # Shove it far past the right/bottom edge of the A4 page.
        result = move_span(doc.page(0), block.spans[0], 5000.0, 5000.0)
        assert result.ok
        assert result.fidelity == F.OVERFLOW


def test_unchanged_text_edit_leaves_neighbour_in_place():
    """Re-applying a span's own text (delta ≈ 0) must not shift an adjacent
    span on the same line — the 'text drifts after editing' regression."""
    import fitz

    from pdfcore.blocks import extract_blocks as extract
    from pdfcore.editor import apply_span_edit

    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 80), "Alpha", fontname="helv", fontsize=12)
    # Different colour → a distinct span on the SAME line, close enough to count
    # as inline-following (not a separate column).
    page.insert_text((80, 80), "Beta", fontname="helv", fontsize=12, color=(1, 0, 0))

    spans = [s for b in extract(page) if b.editable for s in b.spans]
    alpha = next(s for s in spans if "Alpha" in s.text)
    beta_x_before = next(s for s in spans if "Beta" in s.text).origin[0]

    apply_span_edit(page, alpha, alpha.text, spans)  # same text → delta ≈ 0

    spans2 = [s for b in extract(page) if b.editable for s in b.spans]
    beta_x_after = next(s for s in spans2 if "Beta" in s.text).origin[0]
    assert abs(beta_x_after - beta_x_before) < 0.6
    doc.close()


def test_font_resolution_flags_substitution():
    # A subsetted embedded font name → substituted.
    assert resolve_font("ABCDEF+CustomFont").substituted is True
    # A standard font name → not substituted.
    assert resolve_font("Helvetica").substituted is False
    # Style inference.
    assert resolve_font("Times-Bold").fontname == "tibo"
