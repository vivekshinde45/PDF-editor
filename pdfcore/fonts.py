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
    """True if the embedded font lacks a glyph for any non-space character.

    NOTE: subset Type0/Identity-H fonts often have NO usable Unicode cmap (the
    content stream addresses glyphs by ID), so this returns True for everything.
    Prefer ``reusable_font`` which augments such fonts from observed usage.
    """
    font = fitz.Font(fontbuffer=buffer)
    for ch in text:
        if ch.isspace():
            continue
        if font.has_glyph(ord(ch)) == 0:
            return True
    return False


def usage_unicode_to_gid(page: fitz.Page, font_name: str) -> dict[int, int]:
    """Map Unicode codepoint -> glyph id for ``font_name``, from the page's own
    text. This recovers a usable mapping for subset fonts that ship no cmap.

    Matches the font name EXACTLY (normalized): glyph ids are font-specific, so
    mixing usage from e.g. 'Verdana' and 'Verdana-Bold' would map characters to
    the wrong glyphs and render garbage.
    """
    target = _norm(font_name)
    mapping: dict[int, int] = {}
    for sp in page.get_texttrace():
        if _norm(sp.get("font", "")) != target:
            continue
        for ch in sp.get("chars", ()):
            # ch = (unicode, glyph_id, origin, bbox)
            mapping.setdefault(ch[0], ch[1])
    return mapping


def _augment_with_cmap(buffer: bytes, uni_to_gid: dict[int, int]) -> bytes:
    """Return the font program with a Unicode cmap built from ``uni_to_gid``."""
    import io
    import logging

    from fontTools.ttLib import TTFont, newTable
    from fontTools.ttLib.tables._c_m_a_p import cmap_format_4

    logging.getLogger("fontTools").setLevel(logging.ERROR)  # silence timestamp notes
    tt = TTFont(io.BytesIO(buffer))
    order = tt.getGlyphOrder()
    sub = cmap_format_4(4)
    sub.platformID, sub.platEncID, sub.format, sub.language = 3, 1, 4, 0
    sub.cmap = {u: order[g] for u, g in uni_to_gid.items() if 0 < g < len(order)}
    table = newTable("cmap")
    table.tableVersion = 0
    table.tables = [sub]
    tt["cmap"] = table
    out = io.BytesIO()
    tt.save(out)
    return out.getvalue()


_SYSTEM_INDEX: dict[str, str] | None = None


def _system_font_index() -> dict[str, str]:
    """Map normalized font name -> file path for installed fonts (cached)."""
    global _SYSTEM_INDEX
    if _SYSTEM_INDEX is not None:
        return _SYSTEM_INDEX

    import glob
    import os

    from fontTools.ttLib import TTFont

    dirs = [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
        os.path.expanduser(r"~\AppData\Local\Microsoft\Windows\Fonts"),
    ]
    index: dict[str, str] = {}
    for d in dirs:
        for path in glob.glob(os.path.join(d, "*.ttf")) + glob.glob(os.path.join(d, "*.otf")):
            try:
                tt = TTFont(path, fontNumber=0, lazy=True)
                name = tt["name"]
                fam = name.getDebugName(1) or ""
                sub = name.getDebugName(2) or ""
                full = name.getDebugName(4) or ""
                ps = name.getDebugName(6) or ""
                tt.close()
            except Exception:
                continue
            cands = [full, ps]
            if fam and sub:
                cands += [f"{fam}-{sub}", f"{fam} {sub}"]
            if fam:
                cands.append(fam)
            for c in cands:
                if c:
                    index.setdefault(_norm(c), path)
    _SYSTEM_INDEX = index
    return index


def system_font_for(font_name: str, text: str) -> bytes | None:
    """Return an installed font of the SAME family that can draw ``text``.

    Used when the embedded subset lacks a glyph: rather than substituting a
    generic Base-14 font (wrong family/weight), reuse the real system font of
    the same name so the family and weight still match.
    """
    path = _system_font_index().get(_norm(font_name))
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            buffer = fh.read()
    except OSError:
        return None
    font = fitz.Font(fontbuffer=buffer)
    if all(font.has_glyph(ord(ch)) for ch in text if not ch.isspace()):
        return buffer
    return None


def reusable_font(page: fitz.Page, font_name: str, text: str) -> bytes | None:
    """Return a font program that renders ``text`` in the ORIGINAL embedded font.

    - If the embedded font's own cmap already covers ``text``, returns it as-is.
    - If it lacks a cmap (subset Identity-H) but every character is used
      elsewhere on the page, returns the font augmented with a cmap so it can be
      drawn — preserving the exact font and weight.
    - Returns None if a character isn't available in the embedded subset at all
      (caller should substitute and warn for that text).
    """
    buffer = extract_embedded_font(page, font_name)
    if buffer is None:
        return None

    wanted = [ch for ch in text if not ch.isspace()]
    font = fitz.Font(fontbuffer=buffer)
    if all(font.has_glyph(ord(ch)) for ch in wanted):
        return buffer  # native cmap already covers it

    uni_to_gid = usage_unicode_to_gid(page, font_name)
    if any(ord(ch) not in uni_to_gid for ch in wanted):
        return None  # genuinely outside the embedded subset
    try:
        return _augment_with_cmap(buffer, uni_to_gid)
    except Exception:
        return None


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
