"""Bare-minimum requirement tests for real-world conditions.

These encode the invariants that MUST hold for the tool to be trustworthy on
real documents — not just toy single-span PDFs:

  R1  Editing a block does not change the text of any OTHER block.
  R2  Editing a block does not change pixels OUTSIDE that block's region.
  R3  Positionally-spaced tokens in one block do not jam together on edit.
  R4  An unchanged ("no-op") edit leaves the page visually ~identical.
"""

from __future__ import annotations

import fitz
import pytest

from pdfcore import PdfDocument, apply_edit, apply_span_edit, extract_blocks


@pytest.fixture()
def two_block_pdf(tmp_path):
    """Two well-separated text blocks (different lines, far apart)."""
    path = tmp_path / "two.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    page.insert_text((40, 80), "First editable line", fontname="helv", fontsize=12)
    page.insert_text((40, 320), "Second untouched line", fontname="helv", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def gapped_block_pdf(tmp_path):
    """One block containing two tokens separated by a positional gap."""
    path = tmp_path / "gapped.pdf"
    doc = fitz.open()
    page = doc.new_page(width=420, height=160)
    page.insert_text((40, 80), "ride.id", fontname="cour", fontsize=12)
    page.insert_text((120, 80), "rideId ,", fontname="cour", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


def _editable(doc):
    return [b for b in extract_blocks(doc.page(0)) if b.editable]


def _render_bytes(doc, scale=2.0):
    rp = doc.render_page(0, scale)
    return rp.samples, rp.width, rp.height, rp.stride


def _pixels_differ_outside(before, after, w, h, stride, keep_rect, scale):
    """Count differing pixels OUTSIDE keep_rect (PDF points, scaled to pixels)."""
    x0, y0, x1, y1 = (v * scale for v in keep_rect)
    diff = 0
    for py in range(h):
        for px in range(w):
            if x0 <= px <= x1 and y0 <= py <= y1:
                continue
            off = py * stride + px * 3
            if before[off : off + 3] != after[off : off + 3]:
                diff += 1
    return diff


# ---- R1: no collateral text damage ----------------------------------------

def test_editing_one_block_keeps_other_blocks_text(two_block_pdf):
    with PdfDocument.open(two_block_pdf) as doc:
        target = next(b for b in _editable(doc) if "First" in b.text)
        apply_edit(doc.page(0), target, "Changed first line")
        texts = [b.text for b in _editable(doc)]
        assert any("Second untouched line" in t for t in texts), "neighbor text changed!"
        assert not any("First editable line" in t for t in texts)


# ---- R2: no collateral pixel damage ---------------------------------------

def test_editing_does_not_disturb_pixels_outside_block(two_block_pdf):
    scale = 2.0
    with PdfDocument.open(two_block_pdf) as doc:
        before, w, h, stride = _render_bytes(doc, scale)
        target = next(b for b in _editable(doc) if "First" in b.text)
        # Pad the block's keep-region generously to allow for redraw jitter.
        pad = 4.0
        keep = (target.bbox[0] - pad, target.bbox[1] - pad,
                target.bbox[2] + pad, target.bbox[3] + pad)
        apply_edit(doc.page(0), target, "Changed first line")
        after, _, _, _ = _render_bytes(doc, scale)
        diff = _pixels_differ_outside(before, after, w, h, stride, keep, scale)
    # A few stray pixels from anti-aliasing at the edge are tolerable; a blown
    # layout (overlapping the second line) would be thousands.
    assert diff < 50, f"{diff} pixels changed outside the edited block"


# ---- R3: positionally-spaced tokens don't jam -----------------------------

def test_gapped_tokens_do_not_jam_on_edit(gapped_block_pdf):
    with PdfDocument.open(gapped_block_pdf) as doc:
        block = _editable(doc)[0]
        # The flattened text must keep the tokens separated.
        assert "ride.idrideId" not in block.text
        # A no-op edit (re-applying the block's own text) must not jam them.
        apply_edit(doc.page(0), block, block.as_runs())
        out = " ".join(b.text for b in _editable(doc))
        assert "ride.idrideId" not in out, f"tokens jammed: {out!r}"
        assert "ride.id" in out and "rideId" in out


# ---- R4: no-op edit is visually stable -------------------------------------

def test_noop_edit_is_visually_stable(two_block_pdf):
    scale = 2.0
    with PdfDocument.open(two_block_pdf) as doc:
        before, w, h, stride = _render_bytes(doc, scale)
        target = next(b for b in _editable(doc) if "First" in b.text)
        apply_edit(doc.page(0), target, target.as_runs())  # re-apply same content
        after, _, _, _ = _render_bytes(doc, scale)
        # Whole-page difference should be small for an unchanged edit.
        total = sum(
            1 for i in range(0, len(before), 3) if before[i : i + 3] != after[i : i + 3]
        )
    assert total < w * h * 0.02, f"no-op edit changed {total} pixels"


# ---- R5: tabular content is detected so the UI can warn --------------------

def test_tabular_block_is_flagged(gapped_block_pdf, two_block_pdf):
    """Column-aligned blocks must be detectable. Block-reflow editing cannot
    preserve their alignment, so the tool warns rather than silently breaking
    the layout. (Plain prose blocks must NOT be flagged.)"""
    with PdfDocument.open(gapped_block_pdf) as doc:
        gapped = _editable(doc)[0]
        assert gapped.tabular is True

    with PdfDocument.open(two_block_pdf) as doc:
        for b in _editable(doc):
            assert b.tabular is False


# ---- R7: editing a bold span must not double-bold it -----------------------

def _dark_in_band(doc, y0, y1, scale=3.0):
    rp = doc.render_page(0, scale)
    s = rp.samples
    n = 0
    for py in range(int(y0 * scale), int(y1 * scale)):
        row = py * rp.stride
        for px in range(rp.width):
            if s[row + px * 3] < 100:
                n += 1
    return n


def test_noop_edit_of_bold_span_does_not_thicken(bold_number_pdf):
    """Re-applying a bold span's own content must not add weight (no synthetic
    bold on top of an already-bold font)."""
    with PdfDocument.open(bold_number_pdf) as doc:
        ref_before = _dark_in_band(doc, 110, 150)  # untouched reference row
        span = next(s for s in _all_spans(doc) if s.bbox[1] < 80)  # top row
        edited_before = _dark_in_band(doc, 40, 80)
        assert span.bold is True

        apply_span_edit(doc.page(0), span, [(span.text, span.bold, span.italic)])
        edited_after = _dark_in_band(doc, 40, 80)
        ref_after = _dark_in_band(doc, 110, 150)

    # The reference row is untouched.
    assert abs(ref_after - ref_before) < ref_before * 0.05
    # The edited row must stay close to its original weight (and to the
    # reference), not balloon from a second bold pass.
    assert edited_after < edited_before * 1.15, "edited bold span got heavier"
    assert edited_after < ref_before * 1.2, "edited span heavier than reference"


# ---- R6: span-level edit preserves alignment of other columns --------------

def _all_spans(doc):
    spans = []
    for b in extract_blocks(doc.page(0)):
        if b.editable:
            spans.extend(b.spans)
    return spans


def test_span_edit_keeps_other_columns_in_place(gapped_block_pdf):
    """Editing one cell in place must NOT move the next column. This is the
    alignment guarantee that whole-block reflow cannot provide."""
    with PdfDocument.open(gapped_block_pdf) as doc:
        spans = _all_spans(doc)
        first = next(s for s in spans if "ride.id" in s.text)
        second = next(s for s in spans if "rideId" in s.text)
        second_x_before = round(second.bbox[0], 1)

        result = apply_span_edit(doc.page(0), first, "ride.identifier")
        assert result.ok

        after = _all_spans(doc)
        # The edited token changed...
        assert any("ride.identifier" in s.text for s in after)
        # ...but the second column's token stayed at the same x-position.
        second_after = next(s for s in after if "rideId" in s.text)
        assert abs(round(second_after.bbox[0], 1) - second_x_before) < 1.0, (
            "second column moved — alignment broken"
        )
