"""Tests for surgical content-stream editing.

Builds a Type0/Identity-H PDF (embedded CID font) so the surgical glyph-code
path is exercised — the same structure as real-world documents.
"""

from __future__ import annotations

import os

import fitz
import pytest

from pdfcore.surgical import surgical_replace

_VERDANA_BOLD = r"C:\Windows\Fonts\verdanab.ttf"


@pytest.fixture()
def cid_pdf_bytes():
    if not os.path.exists(_VERDANA_BOLD):
        pytest.skip("Verdana-Bold not available")
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_font(fontname="vb", fontfile=_VERDANA_BOLD)
    # Distinct numbers so each is locatable by text.
    page.insert_text((40, 60), "392,400", fontname="vb", fontsize=12)
    page.insert_text((40, 100), "196,200", fontname="vb", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _text(data, page=0):
    d = fitz.open(stream=data, filetype="pdf")
    t = d.load_page(page).get_text()
    d.close()
    return t


def _font_of(data, needle, page=0):
    d = fitz.open(stream=data, filetype="pdf")
    out = None
    for b in d.load_page(page).get_text("dict")["blocks"]:
        if b.get("type", 0) != 0:
            continue
        for line in b["lines"]:
            for s in line["spans"]:
                if needle in s["text"]:
                    out = s["font"]
    d.close()
    return out


def test_surgical_equal_length_preserves_font(cid_pdf_bytes):
    out = surgical_replace(cid_pdf_bytes, 0, "392,400", "400,000")
    assert out is not None
    assert "400,000" in _text(out)
    assert "392,400" not in _text(out)
    # Same embedded font object as the original (not substituted).
    assert "Verdana" in (_font_of(out, "400,000") or "")


def test_surgical_only_targets_named_occurrence(cid_pdf_bytes):
    out = surgical_replace(cid_pdf_bytes, 0, "196,200", "000,000")
    assert out is not None
    t = _text(out)
    assert "000,000" in t
    assert "392,400" in t  # the other number is untouched


def test_surgical_returns_none_for_missing_text(cid_pdf_bytes):
    # Text that isn't on the page → caller should fall back.
    assert surgical_replace(cid_pdf_bytes, 0, "NOPE-NOT-HERE", "x") is None


def test_surgical_returns_none_for_unavailable_glyph(cid_pdf_bytes):
    # A CJK char has no glyph in this font's page usage → fall back.
    assert surgical_replace(cid_pdf_bytes, 0, "392,400", "中文字符七八九") is None
