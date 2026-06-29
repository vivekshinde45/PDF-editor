"""Font resolution for re-inserting edited text.

v1 does NOT extract and re-embed the original embedded font program (that is
deferred — see the design spec). Instead it maps the original font *name* to
one of PyMuPDF's built-in Base-14 fonts by inferring family (serif / sans /
mono) and style (bold / italic) from the name. If the original was not already
one of those standard fonts, we report a substitution so the UI can warn the
user that glyph shapes may differ slightly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz

# PyMuPDF Base-14 short names.
_SANS = {"": "helv", "b": "hebo", "i": "heit", "bi": "hebi"}
_SERIF = {"": "tiro", "b": "tibo", "i": "tiit", "bi": "tibi"}
_MONO = {"": "cour", "b": "cobo", "i": "coit", "bi": "cobi"}

# Names that already ARE the standard fonts → no visible substitution.
_STANDARD_HINTS = ("helvetica", "arial", "times", "courier")


@dataclass(frozen=True)
class ResolvedFont:
    """A font usable by ``page.insert_textbox``."""

    fontname: str          # PyMuPDF builtin short name, e.g. "helv"
    substituted: bool      # True if this differs from the original embedded font
    original_name: str     # the source span's font name, for reference


def _style_key(name: str) -> str:
    low = name.lower()
    bold = "bold" in low or low.endswith(("-b", "bd")) or ",b" in low
    italic = "italic" in low or "oblique" in low or low.endswith("-i") or ",i" in low
    return ("b" if bold else "") + ("i" if italic else "")


def _family_table(name: str) -> dict[str, str]:
    low = name.lower()
    if any(k in low for k in ("mono", "courier", "consol", "menlo")):
        return _MONO
    if any(k in low for k in ("times", "serif", "georgia", "roman", "minion", "garamond")):
        return _SERIF
    return _SANS  # default: sans-serif (Helvetica family)


def _base_name(name: str) -> str:
    """Strip a subset prefix like 'ABCDEF+Helvetica' → 'Helvetica'."""
    return name.split("+", 1)[1] if "+" in name else name


def _norm(name: str) -> str:
    """Normalize a font name for matching: drop subset prefix, non-alphanumerics."""
    return re.sub(r"[^a-z0-9]", "", _base_name(name).lower())


def _names_match(span_name: str, basename: str) -> bool:
    a, b = _norm(span_name), _norm(basename)
    if not a or not b:
        return False
    # The extracted basename often carries a style suffix ('Verdana Regular')
    # while the span name is bare ('Verdana'); accept either containing the other.
    return a in b or b in a


def extract_embedded_font(page: fitz.Page, font_name: str) -> bytes | None:
    """Return the embedded font program bytes whose basename matches ``font_name``.

    Returns None if the font isn't embedded as a reusable program (e.g. a
    non-embedded Base-14 reference, or a format we can't extract).
    """
    doc = page.parent
    fonts = page.get_fonts(full=True)
    target = _norm(font_name)

    # Prefer an exact normalized-name match so 'Helvetica' doesn't grab
    # 'Helvetica-Bold'; fall back to containment only if there's no exact hit.
    exact = [e for e in fonts if _norm(e[3]) == target]
    fuzzy = [e for e in fonts if e not in exact and _names_match(font_name, e[3])]

    for entry in exact + fuzzy:
        xref, ext = entry[0], entry[1]
        if ext not in ("ttf", "otf", "cff", "ttc"):
            return None  # not a directly reusable outline program
        try:
            _name, _ext, _ftype, buffer = doc.extract_font(xref)
        except Exception:
            return None
        return bytes(buffer) if buffer else None
    return None


def font_missing_glyphs(buffer: bytes, text: str) -> bool:
    """True if the embedded font lacks a glyph for any non-space character."""
    font = fitz.Font(fontbuffer=buffer)
    for ch in text:
        if ch.isspace():
            continue
        if font.has_glyph(ord(ch)) == 0:
            return True
    return False


def resolve_font(original_name: str) -> ResolvedFont:
    """Pick a Base-14 font approximating ``original_name``."""
    table = _family_table(original_name)
    fontname = table[_style_key(original_name)]

    low = original_name.lower()
    # Strip the common subset prefix like "ABCDEF+Helvetica".
    if "+" in low:
        low = low.split("+", 1)[1]
    is_standard = any(h in low for h in _STANDARD_HINTS)

    return ResolvedFont(
        fontname=fontname,
        substituted=not is_standard,
        original_name=original_name,
    )
