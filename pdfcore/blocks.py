"""Extract editable text blocks from a page.

Uses PyMuPDF's structured text extraction. Each page becomes a list of
``TextBlock``s; each block carries its ordered ``Span``s (text + styling +
position). Blocks that have no extractable text spans (e.g. image blocks, or
text drawn as vector outlines) are marked non-editable with a reason so the UI
can disable editing on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import fitz


Rect = tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points


def _int_color_to_rgb01(color: int) -> tuple[float, float, float]:
    """Convert PyMuPDF's packed sRGB int to (r, g, b) floats in 0..1."""
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    return (r / 255.0, g / 255.0, b / 255.0)


# PyMuPDF span "flags" bitfield (font-style detection).
_FLAG_ITALIC = 1 << 1   # 2
_FLAG_BOLD = 1 << 4     # 16


@dataclass(frozen=True)
class Span:
    text: str
    font_name: str
    size: float
    color: tuple[float, float, float]  # rgb 0..1
    bbox: Rect
    origin: tuple[float, float]  # baseline origin (x, y) in PDF points
    bold: bool = False
    italic: bool = False


@dataclass
class TextBlock:
    bbox: Rect
    spans: list[Span] = field(default_factory=list)
    editable: bool = True
    reason_if_not: str | None = None
    line_count: int = 0

    @property
    def text(self) -> str:
        """The block's full text, with positional gaps reconstructed as spaces."""
        return "".join(t for t, _b, _i in self.as_runs())

    @property
    def primary_span(self) -> Span | None:
        """The first span — drives the properties panel defaults."""
        return self.spans[0] if self.spans else None

    @property
    def tabular(self) -> bool:
        """True if the block has column-aligned content.

        Detects wide positional gaps between same-line spans (the signature of
        tables/aligned columns). Block-reflow editing converts those gaps to
        spaces and cannot preserve column alignment, so the UI warns first.
        """
        prev: Span | None = None
        for s in self.spans:
            if prev is not None:
                same_line = abs(s.origin[1] - prev.origin[1]) <= 0.5 * (prev.size or 1.0)
                gap = s.bbox[0] - prev.bbox[2]
                if same_line and gap > 2.0 * (prev.size or 1.0):
                    return True
            prev = s
        return False

    def as_runs(self) -> list[tuple[str, bool, bool]]:
        """The block's text as (text, bold, italic) runs, preserving styling.

        Inserts spaces between spans that are separated by a horizontal gap (so
        positionally-spaced tokens, e.g. inline code or aligned columns, don't
        jam together when re-inserted) and a single space between source lines.
        """
        runs: list[tuple[str, bool, bool]] = []
        prev: Span | None = None
        for s in self.spans:
            if prev is not None:
                sep = _separator(prev, s)
                if sep:
                    runs.append((sep, False, False))
            runs.append((s.text, s.bold, s.italic))
            prev = s
        return runs


def _separator(prev: Span, cur: Span) -> str:
    """Reconstruct the whitespace between two spans from their geometry.

    Different baseline → one space (a wrapped line; reflow re-wraps anyway).
    Same line with a horizontal gap → that gap expressed as N spaces, so
    positionally-separated tokens don't jam together. Touching spans → no space.
    """
    size = prev.size or cur.size or 1.0
    if abs(cur.origin[1] - prev.origin[1]) > 0.5 * size:
        return " "
    gap = cur.bbox[0] - prev.bbox[2]
    space_w = 0.5 * size  # rough average glyph advance for a space
    if gap <= 0.5 * space_w:
        return ""  # spans are effectively contiguous
    return " " * max(1, round(gap / space_w))


def extract_blocks(page: fitz.Page) -> list[TextBlock]:
    """Return the text blocks on ``page`` in reading order."""
    data = page.get_text("dict")
    blocks: list[TextBlock] = []

    for raw in data.get("blocks", []):
        bbox = tuple(raw["bbox"])  # type: ignore[assignment]

        # Block type 1 == image. No text to edit.
        if raw.get("type", 0) != 0:
            blocks.append(
                TextBlock(
                    bbox=bbox,
                    editable=False,
                    reason_if_not="Image block — no editable text.",
                )
            )
            continue

        spans: list[Span] = []
        lines = raw.get("lines", [])
        for line in lines:
            for sp in line.get("spans", []):
                text = sp.get("text", "")
                if text == "":
                    continue
                flags = int(sp.get("flags", 0))
                spans.append(
                    Span(
                        text=text,
                        font_name=sp.get("font", ""),
                        size=float(sp.get("size", 0.0)),
                        color=_int_color_to_rgb01(int(sp.get("color", 0))),
                        bbox=tuple(sp["bbox"]),  # type: ignore[arg-type]
                        origin=tuple(sp.get("origin", (sp["bbox"][0], sp["bbox"][3]))),  # type: ignore[arg-type]
                        bold=bool(flags & _FLAG_BOLD),
                        italic=bool(flags & _FLAG_ITALIC),
                    )
                )

        if not spans:
            blocks.append(
                TextBlock(
                    bbox=bbox,
                    editable=False,
                    reason_if_not="No extractable text (possibly vector-drawn).",
                )
            )
        else:
            blocks.append(
                TextBlock(
                    bbox=bbox,
                    spans=spans,
                    editable=True,
                    line_count=len(lines),
                )
            )

    return blocks
