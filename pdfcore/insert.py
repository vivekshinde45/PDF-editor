"""Insert a NEW text box on a page (distinct from editing existing text).

This is purely additive: it draws fresh text into an empty area and never
touches existing spans or the edit/redraw path. Uses PyMuPDF's ``insert_textbox``
so text wraps within the given rectangle.
"""

from __future__ import annotations

import fitz

from .editor import EditResult, Fidelity
from .fonts import resolve_font


def insert_text_box(
    page: fitz.Page,
    rect: tuple[float, float, float, float],
    text: str,
    *,
    font_name: str = "helv",
    size: float = 12.0,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> EditResult:
    """Draw ``text`` inside ``rect`` (PDF points), wrapping to fit.

    ``font_name`` may be a Base-14 name (e.g. ``helv``) or a document font name;
    unknown names resolve to a Base-14 lookalike. Returns OVERFLOW (text kept,
    but clipped by the box) when the text doesn't fit the rectangle.
    """
    if not text:
        return EditResult(ok=False, fidelity=Fidelity.EXACT, message="No text to insert.")

    resolved = resolve_font(font_name)
    box = fitz.Rect(*rect)
    # insert_textbox returns the leftover height; negative means it didn't fit.
    leftover = page.insert_textbox(
        box, text, fontname=resolved.fontname, fontsize=size, color=color, align=0,
    )
    if leftover < 0:
        return EditResult(
            ok=True,
            fidelity=Fidelity.OVERFLOW,
            message=("The text is taller than the box, so some of it is clipped. "
                     "Draw a larger box or use a smaller size."),
        )
    if resolved.substituted:
        return EditResult(
            ok=True,
            fidelity=Fidelity.FONT_SUBSTITUTED,
            message=f"Font '{font_name}' isn't available; a standard font was used.",
        )
    return EditResult(ok=True, fidelity=Fidelity.EXACT)
