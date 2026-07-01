"""Shared fixtures: build small PDFs on the fly so tests are self-contained."""

from __future__ import annotations

import fitz
import pytest


@pytest.fixture()
def simple_pdf(tmp_path):
    """A one-page PDF with two distinct text blocks and a known layout."""
    path = tmp_path / "simple.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 in points

    # Two well-separated text insertions become two blocks.
    page.insert_text((72, 100), "Invoice number 12345", fontname="helv", fontsize=12)
    page.insert_text((72, 400), "Thank you for your business", fontname="tiro", fontsize=11)

    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def multipage_pdf(tmp_path):
    """A three-page PDF, each page carrying a distinct marker string."""
    path = tmp_path / "multipage.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page marker {i + 1}", fontname="helv", fontsize=14)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def multiline_pdf(tmp_path):
    """A one-page PDF whose single block spans three wrapped lines."""
    path = tmp_path / "multi.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_textbox(
        fitz.Rect(40, 40, 360, 160),
        "First line of the paragraph.\nSecond line here.\nThird line ends it.",
        fontname="helv",
        fontsize=11,
    )
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def tight_paragraph_pdf(tmp_path):
    """A multi-line paragraph whose block bbox tightly bounds the glyphs.

    This mirrors real-world PDFs (where get_text returns the tight glyph box),
    unlike multiline_pdf which uses an oversized box.
    """
    path = tmp_path / "tight.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    # Narrow box → the sentence lays out as exactly two tight lines. The
    # extracted block bbox is ~2 lines tall, which is the real-world condition.
    page.insert_textbox(
        fitz.Rect(40, 40, 210, 140),
        "Account holder name and current billing address.",
        fontname="helv",
        fontsize=11,
    )
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def graphics_pdf(tmp_path):
    """A page with a colored background band and a vector rectangle behind text."""
    path = tmp_path / "graphics.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.draw_rect(fitz.Rect(0, 40, 400, 90), color=None, fill=(0.85, 0.92, 1.0))
    page.draw_rect(fitz.Rect(300, 110, 360, 160), color=(0.8, 0.1, 0.1), fill=(0.95, 0.6, 0.6))
    page.insert_text((30, 75), "Header on blue band", fontname="helv", fontsize=14)
    doc.save(str(path))
    doc.close()
    return str(path)


_VERDANA = r"C:\Windows\Fonts\verdana.ttf"


@pytest.fixture()
def embedded_font_pdf(tmp_path):
    """A page with text in an embedded non-Base-14 font (stands in for OpenSans)."""
    import os

    if not os.path.exists(_VERDANA):
        pytest.skip("Verdana not available to embed")
    path = tmp_path / "embedded.pdf"
    doc = fitz.open()
    page = doc.new_page(width=420, height=200)
    page.insert_font(fontname="verd", fontfile=_VERDANA)
    page.insert_text((40, 80), "Original embedded text here", fontname="verd", fontsize=14)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def bold_number_pdf(tmp_path):
    """Two identical bold numbers (Base-14 bold). Editing one must not make it
    heavier than the other (no double-bold)."""
    path = tmp_path / "bold.pdf"
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((40, 70), "52,500", fontname="hebo", fontsize=18)   # editable
    page.insert_text((40, 140), "52,500", fontname="hebo", fontsize=18)  # reference
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def mixed_font_pdf(tmp_path):
    """A page with two same-stem fonts (Verdana + Verdana-Bold) drawing disjoint
    characters — used to prove glyph-id usage maps don't cross-contaminate."""
    import os

    reg, bold = r"C:\Windows\Fonts\verdana.ttf", r"C:\Windows\Fonts\verdanab.ttf"
    if not (os.path.exists(reg) and os.path.exists(bold)):
        pytest.skip("Verdana fonts not available")
    path = tmp_path / "mixed.pdf"
    doc = fitz.open()
    page = doc.new_page(width=300, height=160)
    page.insert_font(fontname="vr", fontfile=reg)
    page.insert_font(fontname="vb", fontfile=bold)
    page.insert_text((40, 60), "regular", fontname="vr", fontsize=14)   # r,e,g,u,l,a
    page.insert_text((40, 100), "BOLDXYZ", fontname="vb", fontsize=14)  # B,O,L,D,X,Y,Z
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def styled_pdf(tmp_path):
    """A block containing a bold word and an italic word (Base-14 styles)."""
    path = tmp_path / "styled.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    tw = fitz.TextWriter(page.rect)
    tw.append((40, 80), "plain ", font=fitz.Font("helv"), fontsize=14)
    tw.append((90, 80), "strong ", font=fitz.Font("hebo"), fontsize=14)  # bold
    tw.append((150, 80), "slanted", font=fitz.Font("heit"), fontsize=14)  # italic
    tw.write_text(page)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def empty_pdf(tmp_path):
    """A one-page PDF with no text at all."""
    path = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def signature_image(tmp_path):
    """A small transparent PNG shaped like a signature stroke."""
    from PIL import Image, ImageDraw

    path = tmp_path / "signature.png"
    image = Image.new("RGBA", (120, 40), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.line((8, 28, 36, 16, 62, 26, 112, 10), fill=(0, 0, 0, 255), width=4)
    image.save(path)
    return str(path)


@pytest.fixture()
def signature_pdf(tmp_path, signature_image):
    """A PDF containing text plus a raster signature image."""
    path = tmp_path / "signed.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=220)
    page.insert_text((40, 60), "Agreement", fontname="helv", fontsize=14)
    page.insert_image(fitz.Rect(80, 120, 200, 160), filename=signature_image)
    doc.save(str(path))
    doc.close()
    return str(path)
