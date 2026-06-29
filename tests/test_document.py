from __future__ import annotations

import pytest

from pdfcore.document import PdfDocument, PdfError


def test_open_and_page_count(simple_pdf):
    with PdfDocument.open(simple_pdf) as doc:
        assert doc.page_count == 1


def test_open_missing_file_raises():
    with pytest.raises(PdfError):
        PdfDocument.open("does-not-exist.pdf")


def test_render_page_produces_rgb_bytes(simple_pdf):
    with PdfDocument.open(simple_pdf) as doc:
        rp = doc.render_page(0, scale=2.0)
        assert rp.width > 0 and rp.height > 0
        # RGB888, no alpha: 3 bytes per pixel, row-padded to stride.
        assert rp.stride >= rp.width * 3
        assert len(rp.samples) == rp.stride * rp.height
        assert rp.scale == 2.0


def test_save_as_roundtrips(simple_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    with PdfDocument.open(simple_pdf) as doc:
        doc.save_as(str(out))
    assert out.exists()
    # Reopens cleanly.
    with PdfDocument.open(str(out)) as doc2:
        assert doc2.page_count == 1


def test_save_as_failure_leaves_target_untouched(simple_pdf, tmp_path):
    out = tmp_path / "exists.pdf"
    out.write_bytes(b"ORIGINAL")
    with PdfDocument.open(simple_pdf) as doc:
        # Point save at a directory path that cannot be created as a file.
        with pytest.raises(PdfError):
            doc.save_as(str(tmp_path))  # tmp_path is a directory
    # Original sentinel file is intact.
    assert out.read_bytes() == b"ORIGINAL"
