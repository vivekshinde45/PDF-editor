from __future__ import annotations

from pdfcore.document import PdfDocument
from pdfcore.images import (
    delete_signature,
    duplicate_signature,
    extract_signature_images,
    insert_signature,
    move_signature,
    replace_signature,
    resize_signature,
)


def test_extract_signature_images(signature_pdf):
    with PdfDocument.open(signature_pdf) as doc:
        sigs = extract_signature_images(doc.page(0))
        assert len(sigs) == 1
        assert sigs[0].bbox == (80.0, 120.0, 200.0, 160.0)
        assert sigs[0].image


def test_move_signature_repositions_image(signature_pdf):
    with PdfDocument.open(signature_pdf) as doc:
        sig = extract_signature_images(doc.page(0))[0]
        result = move_signature(doc.page(0), sig, 30.0, 10.0)
        assert result.ok
        moved = extract_signature_images(doc.page(0))[0]
        assert abs(moved.bbox[0] - 110.0) < 1.0
        assert abs(moved.bbox[1] - 130.0) < 1.0


def test_delete_signature_removes_only_image(signature_pdf):
    with PdfDocument.open(signature_pdf) as doc:
        sig = extract_signature_images(doc.page(0))[0]
        result = delete_signature(doc.page(0), sig)
        assert result.ok
        assert extract_signature_images(doc.page(0)) == []
        assert "Agreement" in doc.page(0).get_text("text")


def test_duplicate_signature_creates_second_image(signature_pdf):
    with PdfDocument.open(signature_pdf) as doc:
        sig = extract_signature_images(doc.page(0))[0]
        result = duplicate_signature(doc.page(0), sig, 0.0, 30.0)
        assert result.ok
        assert len(extract_signature_images(doc.page(0))) == 2


def test_replace_and_insert_signature(signature_pdf, signature_image):
    with PdfDocument.open(signature_pdf) as doc:
        sig = extract_signature_images(doc.page(0))[0]
        assert replace_signature(doc.page(0), sig, signature_image).ok
        assert len(extract_signature_images(doc.page(0))) == 1

        assert insert_signature(doc.page(0), signature_image, 40.0, 170.0).ok
        assert len(extract_signature_images(doc.page(0))) == 2


def test_resize_signature_changes_width_and_preserves_aspect(signature_pdf):
    with PdfDocument.open(signature_pdf) as doc:
        sig = extract_signature_images(doc.page(0))[0]
        result = resize_signature(doc.page(0), sig, 240.0)
        assert result.ok
        resized = extract_signature_images(doc.page(0))[0]
        width = resized.bbox[2] - resized.bbox[0]
        height = resized.bbox[3] - resized.bbox[1]
        assert abs(width - 240.0) < 1.0
        assert abs((width / height) - sig.aspect) < 0.1
