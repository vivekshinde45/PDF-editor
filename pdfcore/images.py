"""Extract and edit image blocks, used for signature editing.

Most signatures in ordinary PDFs are placed as raster image blocks.  These
helpers keep that support Qt-free and reuse the app's existing render/reload
model: remove the original image by redaction, then insert the same or a new
image at the requested rectangle.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz

from .blocks import Rect
from .editor import EditResult, Fidelity

_PADDING = 1.0
_DEFAULT_SIGNATURE_WIDTH = 160.0


@dataclass(frozen=True)
class SignatureImage:
    bbox: Rect
    width: int
    height: int
    ext: str
    image: bytes

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 1.0


def extract_signature_images(page: fitz.Page) -> list[SignatureImage]:
    """Return raster image blocks on ``page`` in reading order."""
    out: list[SignatureImage] = []
    for raw in page.get_text("dict").get("blocks", []):
        if raw.get("type") != 1:
            continue
        image = raw.get("image")
        if not image:
            continue
        out.append(
            SignatureImage(
                bbox=tuple(raw["bbox"]),  # type: ignore[arg-type]
                width=int(raw.get("width", 0)),
                height=int(raw.get("height", 0)),
                ext=str(raw.get("ext", "")),
                image=bytes(image),
            )
        )
    return out


def move_signature(page: fitz.Page, sig: SignatureImage, dx: float, dy: float) -> EditResult:
    if dx == 0 and dy == 0:
        return EditResult(ok=True, fidelity=Fidelity.EXACT)
    old = fitz.Rect(*sig.bbox)
    new = old + (dx, dy, dx, dy)
    _remove_image_at(page, old)
    page.insert_image(new, stream=sig.image, keep_proportion=False)
    return _bounds_result(page, new, "Moved signature lands outside the page bounds.")


def delete_signature(page: fitz.Page, sig: SignatureImage) -> EditResult:
    _remove_image_at(page, fitz.Rect(*sig.bbox))
    return EditResult(ok=True, fidelity=Fidelity.EXACT)


def duplicate_signature(
    page: fitz.Page, sig: SignatureImage, dx: float = 8.0, dy: float = 12.0
) -> EditResult:
    rect = fitz.Rect(*sig.bbox) + (dx, dy, dx, dy)
    page.insert_image(rect, stream=sig.image, keep_proportion=False)
    return _bounds_result(page, rect, "Duplicated signature lands outside the page bounds.")


def replace_signature(page: fitz.Page, sig: SignatureImage, image_path: str) -> EditResult:
    rect = fitz.Rect(*sig.bbox)
    _remove_image_at(page, rect)
    page.insert_image(rect, filename=image_path, keep_proportion=True)
    return EditResult(ok=True, fidelity=Fidelity.EXACT)


def resize_signature(page: fitz.Page, sig: SignatureImage, width: float) -> EditResult:
    old = fitz.Rect(*sig.bbox)
    width = max(1.0, width)
    height = width / sig.aspect
    rect = fitz.Rect(old.x0, old.y0, old.x0 + width, old.y0 + height)
    _remove_image_at(page, old)
    page.insert_image(rect, stream=sig.image, keep_proportion=False)
    return _bounds_result(page, rect, "Resized signature lands outside the page bounds.")


def insert_signature(
    page: fitz.Page,
    image_path: str,
    x: float,
    y: float,
    width: float = _DEFAULT_SIGNATURE_WIDTH,
) -> EditResult:
    pix = fitz.Pixmap(image_path)
    try:
        aspect = pix.width / pix.height if pix.height else 1.0
    finally:
        pix = None
    rect = fitz.Rect(x, y, x + width, y + (width / aspect))
    page.insert_image(rect, filename=image_path, keep_proportion=True)
    return _bounds_result(page, rect, "Inserted signature lands outside the page bounds.")


def _remove_image_at(page: fitz.Page, rect: fitz.Rect) -> None:
    page.add_redact_annot(rect, fill=False, cross_out=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE, graphics=0)


def _bounds_result(page: fitz.Page, rect: fitz.Rect, message: str) -> EditResult:
    if (
        rect.x0 < _PADDING
        or rect.y0 < _PADDING
        or rect.x1 > page.rect.x1 - _PADDING
        or rect.y1 > page.rect.y1 - _PADDING
    ):
        return EditResult(ok=True, fidelity=Fidelity.OVERFLOW, message=message)
    return EditResult(ok=True, fidelity=Fidelity.EXACT)
