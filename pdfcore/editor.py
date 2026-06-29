"""Apply a text edit to a block: redact the original, reinsert reflowed runs.

Strategy (per the design spec): truly remove the original glyphs in the block's
rectangle, then reinsert the new text. The new text is a list of *runs* — each a
(text, bold) pair — so individual words can be bolded. Untouched content
elsewhere on the page is not modified.

Font fidelity: we reuse the ORIGINAL embedded font program when it covers every
character being inserted, so there is no visible font change. We only fall back
to a similar standard font (and warn) when the embedded font is missing glyphs
for the new text or cannot be extracted.

Bold is synthesized by stroking the glyph outline (render_mode fill+stroke), so
it works with the embedded font even when no bold variant is embedded.

Returns an ``EditResult`` with a fidelity flag (exact / font substituted /
overflow). Text is never silently dropped: overflowing text flows below the
block rather than vanishing.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

import fitz

from .blocks import Span, TextBlock
from .fonts import extract_embedded_font, font_missing_glyphs, resolve_font

# A run of text with uniform bold/italic flags.
Run = tuple[str, bool, bool]

# Synthetic-bold stroke width, as a fraction of the font size.
_BOLD_BORDER = 0.05
# Synthetic-italic horizontal shear (slants glyph tops to the right).
_ITALIC_SHEAR = 0.22
# A registered name for the reused embedded font on the page.
_EMBED_FONTNAME = "edit_embed"
_PADDING = 1.0


class Fidelity(enum.Enum):
    EXACT = "exact"
    FONT_SUBSTITUTED = "font_substituted"
    OVERFLOW = "overflow"


@dataclass(frozen=True)
class EditResult:
    ok: bool
    fidelity: Fidelity
    message: str | None = None


def _normalize_runs(new_text_or_runs) -> list[Run]:
    """Accept a plain string, (text, bold) pairs, or (text, bold, italic) triples."""
    if isinstance(new_text_or_runs, str):
        return [(new_text_or_runs, False, False)]
    out: list[Run] = []
    for run in new_text_or_runs:
        text = run[0]
        if text == "":
            continue
        bold = bool(run[1]) if len(run) > 1 else False
        italic = bool(run[2]) if len(run) > 2 else False
        out.append((text, bold, italic))
    return out


def _full_text(runs: list[Run]) -> str:
    return "".join(t for t, _b, _i in runs)


def apply_edit(page: fitz.Page, block: TextBlock, new_text_or_runs: str | list[Run]) -> EditResult:
    """Replace ``block``'s text with new runs on ``page``.

    ``new_text_or_runs`` is either a plain string (one non-bold run) or a list of
    ``(text, bold)`` runs. The block's font/size/color come from its primary span.
    """
    if not block.editable or block.primary_span is None:
        return EditResult(
            ok=False,
            fidelity=Fidelity.EXACT,
            message=block.reason_if_not or "Block is not editable.",
        )

    runs = _normalize_runs(new_text_or_runs)
    if not runs:
        runs = [("", False, False)]
    span = block.primary_span
    rect = fitz.Rect(*block.bbox)
    text = _full_text(runs)

    # Choose a font: reuse the embedded program if it covers the new text.
    buffer, fontname, measure_font, substituted = _choose_font(page, span.font_name, text)

    # 1. Remove the original glyphs (no cosmetic fill — preserves backgrounds).
    page.add_redact_annot(rect, fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0)

    # Register the reused embedded font AFTER redaction: apply_redactions
    # regenerates the page resources and would otherwise drop the registration.
    if buffer is not None:
        page.insert_font(fontname=fontname, fontbuffer=buffer)

    # 2. Re-insert the runs. Single-line blocks stay on one line (no wrap);
    # multi-line blocks wrap and grow downward so text is never dropped.
    wrap = block.line_count > 1
    overflowed = _layout_runs(
        page, block, runs, fontname, measure_font, span.size, span.color, wrap,
        base_bold=span.bold, base_italic=span.italic,
    )

    if overflowed:
        return EditResult(
            ok=True,
            fidelity=Fidelity.OVERFLOW,
            message=(
                "New text is longer than the original block, so it flows beyond "
                "the original bounds (and may overlap content below). The text "
                "was kept — shorten it or (later) enable shrink-to-fit."
            ),
        )
    if substituted:
        return EditResult(
            ok=True,
            fidelity=Fidelity.FONT_SUBSTITUTED,
            message=(
                f"Original font '{span.font_name}' is missing glyphs for the new "
                f"text (or isn't reusable), so a similar standard font was used "
                f"for it. Glyph shapes may differ."
            ),
        )
    return EditResult(ok=True, fidelity=Fidelity.EXACT)


def apply_span_edit(page: fitz.Page, span: Span, new_text_or_runs) -> EditResult:
    """Edit a single span IN PLACE, leaving every other span untouched.

    This is the alignment-preserving path: only the span's own rectangle is
    redacted and the new text is drawn at the span's original baseline, so
    neighbouring cells/columns do not move. Used for tables and any layout where
    reflowing the whole block would break alignment.
    """
    block = TextBlock(bbox=span.bbox, spans=[span], editable=True, line_count=1)
    return apply_edit(page, block, new_text_or_runs)


def _choose_font(page, font_name, text):
    """Return (buffer, insert_fontname, measure_font, substituted).

    Prefers the original embedded font; substitutes a Base-14 lookalike only when
    the embedded font is unavailable or lacks glyphs for ``text``. When the
    embedded font is reused, ``buffer`` is its program bytes (the caller registers
    it after redaction); otherwise ``buffer`` is None.
    """
    buffer = extract_embedded_font(page, font_name)
    if buffer is not None and not font_missing_glyphs(buffer, text):
        return buffer, _EMBED_FONTNAME, fitz.Font(fontbuffer=buffer), False

    resolved = resolve_font(font_name)
    return None, resolved.fontname, fitz.Font(resolved.fontname), resolved.substituted


def _tokenize(runs: list[Run]):
    """Flatten runs into tokens: ('word', text, bold, italic) | ('space',) | ('break',)."""
    tokens = []
    for text, bold, italic in runs:
        for piece in re.findall(r"\S+|\s+", text):
            if piece.strip() == "":
                if "\n" in piece:
                    tokens.append(("break",))
                else:
                    tokens.append(("space",))
            else:
                tokens.append(("word", piece, bold, italic))
    return tokens


def _layout_runs(
    page, block, runs, fontname, measure_font, fontsize, color, wrap,
    base_bold=False, base_italic=False,
) -> bool:
    """Place runs word-by-word from the original baseline. Returns True on overflow.

    Synthetic bold/italic are applied only to ADD a style the chosen font does
    not already have (``base_bold`` / ``base_italic`` describe the font's native
    style). This avoids double-bolding text that is already drawn with a bold
    font — which would thicken and visually compress it.

    Wrapped layout grows downward to the page margin so text is never dropped;
    single-line layout extends rightward.
    """
    span = block.primary_span
    left = block.bbox[0]
    right = block.bbox[2]
    baseline_x, baseline_y = span.origin
    line_height = fontsize * 1.2
    space_w = measure_font.text_length(" ", fontsize=fontsize)
    page_bottom = page.rect.y1 - _PADDING

    x = baseline_x
    y = baseline_y
    overflow = False
    pending_space = False

    for tok in _tokenize(runs):
        if tok[0] == "break":
            x, y = left, y + line_height
            pending_space = False
            continue
        if tok[0] == "space":
            pending_space = True
            continue

        word, bold, italic = tok[1], tok[2], tok[3]
        # Only synthesize a style the font doesn't already provide.
        synth_bold = bold and not base_bold
        synth_italic = italic and not base_italic
        w = measure_font.text_length(word, fontsize=fontsize)
        gap = space_w if (pending_space and x > left) else 0.0
        pending_space = False

        if wrap and x > left and (x + gap + w) > right:
            x, y = left, y + line_height  # wrap to next line
            gap = 0.0
        else:
            x += gap

        if y > page_bottom:
            overflow = True  # ran off the page

        # Synthetic italic: shear the glyphs about this word's baseline point.
        morph = None
        if synth_italic:
            morph = (fitz.Point(x, y), fitz.Matrix(1, 0, _ITALIC_SHEAR, 1, 0, 0))

        render_mode = 2 if synth_bold else 0
        page.insert_text(
            (x, y),
            word,
            fontname=fontname,
            fontsize=fontsize,
            color=color,
            fill=color,
            render_mode=render_mode,
            border_width=_BOLD_BORDER if synth_bold else 1,
            morph=morph,
        )
        x += w

    # Overflow if the last baseline dropped well below the original block bottom
    # (tolerate ~one line of natural growth) or text ran off the page.
    if y > block.bbox[3] + fontsize:
        overflow = True
    if not wrap and x > right + 0.5:
        overflow = True  # single line wider than the block
    return overflow
