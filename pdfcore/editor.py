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
from .fonts import resolve_font, reusable_font, system_font_for

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


# A gap wider than this many font sizes marks a column boundary (not word space).
_COLUMN_GAP = 2.0


def apply_span_edit(
    page: fitz.Page, span: Span, new_text_or_runs, block_spans: list[Span] | None = None,
    override_size: float | None = None,
    override_color: tuple[float, float, float] | None = None,
) -> EditResult:
    """Edit a single span at its original position, reflowing the rest of its line.

    The edited text is drawn at the span's baseline. Words that follow it *on the
    same line* (within ``block_spans``) are shifted by the width change so a
    sentence closes up / makes room instead of leaving a gap or overlapping.

    Shifting STOPS at a column-sized gap: content that sits after a wide gap is a
    separate column and stays put, so table alignment is preserved. With no
    ``block_spans`` (or none following), this is a pure in-place edit.

    ``override_size`` / ``override_color`` change only the EDITED span's font
    size / colour; following spans keep their own. Both default to the span's own
    values, so callers that pass nothing get byte-identical v1 behaviour.
    """
    runs = _normalize_runs(new_text_or_runs) or [("", False, False)]
    text = _full_text(runs)
    size = override_size if override_size is not None else span.size
    color = override_color if override_color is not None else span.color

    buffer, fontname, measure_font, substituted = _choose_font(page, span.font_name, text)
    new_width = _measure_width(measure_font, runs, size)
    # The shift applied to following spans must reflect only the CHANGE in width,
    # so measure the old text with the SAME font we measure the new text with.
    # (Comparing against the original rendered bbox width would fold the embedded
    # vs. reused-font metric mismatch into ``delta`` and make neighbours drift
    # even when the text length is unchanged — the "off beat" bug.)
    old_width = _measure_width(measure_font, [(span.text, span.bold, span.italic)], size)
    delta = new_width - old_width

    # Only disturb the following spans when the width actually changed enough to
    # see (~half a point). For same-length edits, delta ≈ 0, so we leave the rest
    # of the line completely untouched and nothing shifts.
    following = _inline_following(span, block_spans or [], size) if abs(delta) > 0.5 else []

    # Redact the edited span and every span we're about to move, then redraw all.
    page.add_redact_annot(fitz.Rect(*span.bbox), fill=False, cross_out=False)
    for s in following:
        page.add_redact_annot(fitz.Rect(*s.bbox), fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0)

    if buffer is not None:
        page.insert_font(fontname=fontname, fontbuffer=buffer)
    edited_end = _draw_line(
        page, span.origin[0], span.origin[1], runs, fontname, measure_font,
        size, color, span.bold, span.italic,
    )

    rightmost = edited_end
    for s in following:
        s_buf, s_name, s_meas, _sub = _choose_font(page, s.font_name, s.text)
        if s_buf is not None:
            s_name = _reg_name(s.font_name)
            page.insert_font(fontname=s_name, fontbuffer=s_buf)
        end = _draw_line(
            page, s.origin[0] + delta, s.origin[1], [(s.text, s.bold, s.italic)],
            s_name, s_meas, s.size, s.color, s.bold, s.italic,
        )
        rightmost = max(rightmost, end)

    if rightmost > page.rect.x1 - _PADDING:
        return EditResult(
            ok=True,
            fidelity=Fidelity.OVERFLOW,
            message="Edited text runs past the page margin; shorten it.",
        )
    if substituted:
        return EditResult(
            ok=True,
            fidelity=Fidelity.FONT_SUBSTITUTED,
            message=(
                f"Original font '{span.font_name}' is missing glyphs for the new "
                f"text (or isn't reusable); a similar standard font was used."
            ),
        )
    return EditResult(ok=True, fidelity=Fidelity.EXACT)


def move_span(page: fitz.Page, span: Span, dx: float, dy: float) -> EditResult:
    """Move ``span`` by ``(dx, dy)`` PDF points: redact it and redraw at the new
    origin, reusing the original embedded font.

    Positive ``dx`` moves right, positive ``dy`` moves down (PDF page space).
    Only the location changes — the text, font, size, weight and colour are
    preserved. Other content on the page is untouched.
    """
    runs = [(span.text, span.bold, span.italic)]
    buffer, fontname, measure_font, substituted = _choose_font(page, span.font_name, span.text)

    page.add_redact_annot(fitz.Rect(*span.bbox), fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0)
    if buffer is not None:
        page.insert_font(fontname=fontname, fontbuffer=buffer)

    new_x = span.origin[0] + dx
    new_y = span.origin[1] + dy
    end = _draw_line(
        page, new_x, new_y, runs, fontname, measure_font,
        span.size, span.color, span.bold, span.italic,
    )

    # The baseline sits at new_y; the glyph tops reach ~one font size above it.
    if (new_x < _PADDING or new_y - span.size < 0
            or end > page.rect.x1 - _PADDING or new_y > page.rect.y1 - _PADDING):
        return EditResult(
            ok=True,
            fidelity=Fidelity.OVERFLOW,
            message="Moved text lands outside the page bounds.",
        )
    if substituted:
        return EditResult(
            ok=True,
            fidelity=Fidelity.FONT_SUBSTITUTED,
            message=(
                f"Original font '{span.font_name}' wasn't reusable; a similar "
                f"standard font was used. Glyph shapes may differ."
            ),
        )
    return EditResult(ok=True, fidelity=Fidelity.EXACT)


def delete_span(page: fitz.Page, span: Span) -> EditResult:
    """Delete ``span``'s text: redact its rectangle and draw nothing.

    Uses the same no-fill redaction as the edit path, so backgrounds/graphics
    under the text are preserved (only the glyphs are removed). Other content on
    the page is untouched.
    """
    page.add_redact_annot(fitz.Rect(*span.bbox), fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0)
    return EditResult(ok=True, fidelity=Fidelity.EXACT)


def duplicate_span(page: fitz.Page, span: Span, dx: float, dy: float) -> EditResult:
    """Draw a second copy of ``span`` offset by ``(dx, dy)`` points.

    The original is left in place (no redaction); the copy reuses the original
    embedded font, size, weight and colour. Positive dx/dy offset right/down.
    """
    runs = [(span.text, span.bold, span.italic)]
    buffer, fontname, measure_font, substituted = _choose_font(page, span.font_name, span.text)
    # No redaction here, so the font can be registered before drawing.
    if buffer is not None:
        page.insert_font(fontname=fontname, fontbuffer=buffer)

    new_x = span.origin[0] + dx
    new_y = span.origin[1] + dy
    end = _draw_line(
        page, new_x, new_y, runs, fontname, measure_font,
        span.size, span.color, span.bold, span.italic,
    )

    if (new_x < _PADDING or new_y - span.size < 0
            or end > page.rect.x1 - _PADDING or new_y > page.rect.y1 - _PADDING):
        return EditResult(
            ok=True,
            fidelity=Fidelity.OVERFLOW,
            message="Duplicated text lands outside the page bounds.",
        )
    if substituted:
        return EditResult(
            ok=True,
            fidelity=Fidelity.FONT_SUBSTITUTED,
            message=(
                f"Original font '{span.font_name}' wasn't reusable; a similar "
                f"standard font was used for the copy."
            ),
        )
    return EditResult(ok=True, fidelity=Fidelity.EXACT)


def _reg_name(font_name: str) -> str:
    return "ed" + re.sub(r"[^a-z0-9]", "", font_name.lower())[:18] or "edfont"


def _measure_width(measure_font, runs, size) -> float:
    """Width of ``runs`` rendered on a single line."""
    space_w = measure_font.text_length(" ", fontsize=size)
    x = 0.0
    pending = False
    for tok in _tokenize(runs):
        if tok[0] == "space" or tok[0] == "break":
            pending = True
            continue
        if pending and x > 0:
            x += space_w
        pending = False
        x += measure_font.text_length(tok[1], fontsize=size)
    return x


def _inline_following(span: Span, spans: list[Span], size: float) -> list[Span]:
    """Spans after ``span`` on the same line, up to the first column-sized gap."""
    same_line = sorted(
        (s for s in spans
         if s is not span
         and abs(s.origin[1] - span.origin[1]) <= 0.5 * size
         and s.bbox[0] >= span.bbox[2] - 0.1),
        key=lambda s: s.bbox[0],
    )
    out: list[Span] = []
    prev_end = span.bbox[2]
    for s in same_line:
        if s.bbox[0] - prev_end > _COLUMN_GAP * size:
            break  # a new column begins here — leave it (and the rest) in place
        out.append(s)
        prev_end = s.bbox[2]
    return out


def _draw_line(page, bx, by, runs, fontname, measure_font, size, color, base_bold, base_italic) -> float:
    """Draw runs on one line starting at baseline (bx, by). Returns the end x."""
    space_w = measure_font.text_length(" ", fontsize=size)
    x = bx
    pending = False
    for tok in _tokenize(runs):
        if tok[0] == "space" or tok[0] == "break":
            pending = True
            continue
        word, bold, italic = tok[1], tok[2], tok[3]
        if pending and x > bx:
            x += space_w
        pending = False
        synth_bold = bold and not base_bold
        synth_italic = italic and not base_italic
        morph = (
            (fitz.Point(x, by), fitz.Matrix(1, 0, _ITALIC_SHEAR, 1, 0, 0))
            if synth_italic else None
        )
        page.insert_text(
            (x, by), word, fontname=fontname, fontsize=size, color=color, fill=color,
            render_mode=2 if synth_bold else 0,
            border_width=_BOLD_BORDER if synth_bold else 1, morph=morph,
        )
        x += measure_font.text_length(word, fontsize=size)
    return x


def _choose_font(page, font_name, text):
    """Return (buffer, insert_fontname, measure_font, substituted).

    Prefers the original embedded font; substitutes a Base-14 lookalike only when
    the embedded font is unavailable or lacks glyphs for ``text``. When the
    embedded font is reused, ``buffer`` is its program bytes (the caller registers
    it after redaction); otherwise ``buffer`` is None.
    """
    buffer = reusable_font(page, font_name, text)
    if buffer is not None:
        return buffer, _EMBED_FONTNAME, fitz.Font(fontbuffer=buffer), False

    # Embedded subset can't draw it — reuse the SAME family from system fonts
    # (real Verdana-Bold etc.) before resorting to a generic Base-14 substitute.
    sysbuf = system_font_for(font_name, text)
    if sysbuf is not None:
        return sysbuf, _EMBED_FONTNAME, fitz.Font(fontbuffer=sysbuf), False

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
