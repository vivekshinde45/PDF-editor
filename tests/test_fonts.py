"""Tests for embedded-font reuse, including subset-font augmentation."""

from __future__ import annotations

import fitz

from pdfcore.document import PdfDocument
import os

import pytest

from pdfcore.fonts import (
    resolve_font,
    reusable_font,
    system_font_for,
    usage_unicode_to_gid,
)


def test_resolve_font_flags_substitution():
    assert resolve_font("ABCDEF+CustomFont").substituted is True
    assert resolve_font("Helvetica").substituted is False
    assert resolve_font("Times-Bold").fontname == "tibo"


def test_usage_map_recovers_unicode_to_gid(embedded_font_pdf):
    with PdfDocument.open(embedded_font_pdf) as doc:
        page = doc.page(0)
        mapping = usage_unicode_to_gid(page, "Verdana")
        # Characters actually drawn on the page are mapped to glyph ids.
        assert ord("e") in mapping and mapping[ord("e")] > 0


def test_reusable_font_covers_used_chars(embedded_font_pdf):
    with PdfDocument.open(embedded_font_pdf) as doc:
        page = doc.page(0)
        # "text" uses characters present on the page → reusable (original font).
        buf = reusable_font(page, "Verdana", "tee")
        assert buf is not None
        assert fitz.Font(fontbuffer=buf).has_glyph(ord("t")) > 0


def test_reusable_font_none_when_char_absent(embedded_font_pdf):
    with PdfDocument.open(embedded_font_pdf) as doc:
        page = doc.page(0)
        # A CJK char is neither in the font's cmap nor used on the page.
        assert reusable_font(page, "Verdana", "中") is None


def test_system_font_fallback_same_family():
    """When the embedded subset lacks a glyph, the same family is found in the
    system fonts (real Verdana-Bold) rather than a generic substitute."""
    if not os.path.exists(r"C:\Windows\Fonts\verdanab.ttf"):
        pytest.skip("Verdana-Bold not installed")
    import fitz

    buf = system_font_for("Verdana-Bold", "1,720,008")  # includes a 7
    assert buf is not None
    f = fitz.Font(fontbuffer=buf)
    assert f.has_glyph(ord("7")) > 0
    assert "verdana" in f.name.lower()


def test_system_font_for_unknown_family_is_none():
    assert system_font_for("TotallyMadeUpFontName-XYZ", "abc") is None


def test_usage_map_does_not_cross_contaminate(mixed_font_pdf):
    """Regression: a font's glyph-id usage map must not pick up another font
    that merely shares a name stem (Verdana vs Verdana-Bold). Mixing glyph ids
    across fonts renders garbage."""
    with PdfDocument.open(mixed_font_pdf) as doc:
        page = doc.page(0)
        regular = usage_unicode_to_gid(page, "Verdana")
        bold = usage_unicode_to_gid(page, "Verdana-Bold")

    # 'r' is only in the regular text; 'X' only in the bold text.
    assert ord("r") in regular and ord("X") not in regular
    assert ord("X") in bold and ord("r") not in bold
