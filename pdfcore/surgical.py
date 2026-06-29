"""Surgical content-stream text editing — the highest-fidelity edit path.

Instead of redacting and re-drawing text (which re-renders, losing the original
font metrics / spacing / justification), this edits the glyph codes *inside the
original text-showing operators* and leaves every positioning operator
untouched. The font, weight, size, character spacing and justification are
therefore physically the same objects the PDF already used — they cannot change.

Scope / limits (returns None to let the caller fall back to the redraw path):
- Works on the page's top-level content stream (not text inside XObjects).
- Locates the edit by matching the original text; ambiguous repeats are
  disambiguated by occurrence index.
- Every character of the new text must have a known glyph in the span's font
  (recovered from the document's own usage). Otherwise None.

Uses PyMuPDF (glyph/unicode info) + pikepdf (content-stream rewrite).
"""

from __future__ import annotations

import io

import fitz
import pikepdf
from pikepdf import ContentStreamInstruction, String, parse_content_stream, unparse_content_stream

from .fonts import _norm

# Show-text operators that carry glyph codes.
_SHOW = {"Tj", "'"}


def _font_maps(page: fitz.Page):
    """Return per-basefont {gid: unicode} and {unicode: gid} from page usage."""
    gid2uni: dict[str, dict[int, str]] = {}
    uni2gid: dict[str, dict[str, int]] = {}
    for sp in page.get_texttrace():
        bf = _norm(sp["font"])  # normalize: resource BaseFont names vary (space vs hyphen)
        g2u = gid2uni.setdefault(bf, {})
        u2g = uni2gid.setdefault(bf, {})
        for ch in sp.get("chars", ()):
            g2u.setdefault(ch[1], chr(ch[0]))
            u2g.setdefault(chr(ch[0]), ch[1])
    return gid2uni, uni2gid


def _glyph_index(pike_page, gid2uni, res2bf):
    """Flatten page show-text glyphs in stream order.

    Returns (flat, instrs) where flat is a list of dicts describing each glyph's
    location in the content stream.
    """
    instrs = list(parse_content_stream(pike_page))
    flat = []
    cur = ""
    for idx, ins in enumerate(instrs):
        op = str(ins.operator)
        if op == "Tf":
            cur = _norm(res2bf.get(str(ins.operands[0]), ""))
        elif op in _SHOW:
            raw = bytes(ins.operands[-1])
            m = gid2uni.get(cur, {})
            for bo in range(0, len(raw) - 1, 2):
                code = (raw[bo] << 8) | raw[bo + 1]
                flat.append({"ch": m.get(code, "?"), "i": idx, "elem": -1, "bo": bo, "font": cur})
        elif op == "TJ":
            m = gid2uni.get(cur, {})
            for ei, e in enumerate(ins.operands[0]):
                if isinstance(e, String):
                    raw = bytes(e)
                    for bo in range(0, len(raw) - 1, 2):
                        code = (raw[bo] << 8) | raw[bo + 1]
                        flat.append({"ch": m.get(code, "?"), "i": idx, "elem": ei, "bo": bo, "font": cur})
    return flat, instrs


def _find_run(flat, old_text, occurrence):
    text = "".join(g["ch"] for g in flat)
    start = -1
    for _ in range(occurrence + 1):
        start = text.find(old_text, start + 1)
        if start < 0:
            return None
    return flat[start : start + len(old_text)]


def _set_code(instrs, glyph, code: bytes):
    """Replace the 2 code bytes at ``glyph`` with ``code`` (2 bytes)."""
    ins = instrs[glyph["i"]]
    if glyph["elem"] < 0:  # Tj / '
        old = bytes(ins.operands[-1])
        nb = old[: glyph["bo"]] + code + old[glyph["bo"] + 2 :]
        instrs[glyph["i"]] = ContentStreamInstruction([String(nb)], ins.operator)
    else:  # TJ array element
        arr = list(ins.operands[0])
        old = bytes(arr[glyph["elem"]])
        arr[glyph["elem"]] = String(old[: glyph["bo"]] + code + old[glyph["bo"] + 2 :])
        instrs[glyph["i"]] = ContentStreamInstruction([arr], ins.operator)


def surgical_replace(pdf_bytes: bytes, page_index: int, old_text: str,
                     new_text: str, occurrence: int = 0) -> bytes | None:
    """Replace ``old_text`` with ``new_text`` on a page by editing glyph codes.

    Returns the modified PDF bytes, or None if the edit can't be done surgically
    (text not locatable in the page stream, or a new character has no glyph in
    the font) — the caller should then fall back to the redraw path.
    """
    if not old_text:
        return None

    fdoc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        gid2uni, uni2gid = _font_maps(fdoc.load_page(page_index))
    finally:
        fdoc.close()

    pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    try:
        page = pdf.pages[page_index]
        res2bf = {str(n): str(f.get("/BaseFont", "")).lstrip("/")
                  for n, f in page.Resources.Font.items()}
        flat, instrs = _glyph_index(page, gid2uni, res2bf)
        run = _find_run(flat, old_text, occurrence)
        if run is None:
            return None

        font = run[0]["font"]
        u2g = uni2gid.get(font, {})
        # Every new (non-space) char must have a glyph in this font.
        if any(ch not in u2g for ch in new_text):
            return None

        n = min(len(run), len(new_text))
        # 1:1 replace the overlapping glyphs.
        for k in range(n):
            _set_code(instrs, run[k], u2g[new_text[k]].to_bytes(2, "big"))
        # New text shorter: blank out the leftover original glyphs.
        for k in range(n, len(run)):
            _set_code_blank(instrs, run[k])
        # New text longer: append the remaining glyphs onto the last replaced op.
        if len(new_text) > len(run):
            extra = b"".join(u2g[ch].to_bytes(2, "big") for ch in new_text[len(run):])
            _append_codes(instrs, run[len(run) - 1], extra)

        page.Contents = pdf.make_stream(unparse_content_stream(instrs))
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()
    finally:
        pdf.close()


def _set_code_blank(instrs, glyph):
    """Remove the 2 code bytes at ``glyph`` (used when new text is shorter)."""
    ins = instrs[glyph["i"]]
    if glyph["elem"] < 0:
        old = bytes(ins.operands[-1])
        nb = old[: glyph["bo"]] + old[glyph["bo"] + 2 :]
        instrs[glyph["i"]] = ContentStreamInstruction([String(nb)], ins.operator)
    else:
        arr = list(ins.operands[0])
        old = bytes(arr[glyph["elem"]])
        arr[glyph["elem"]] = String(old[: glyph["bo"]] + old[glyph["bo"] + 2 :])
        instrs[glyph["i"]] = ContentStreamInstruction([arr], ins.operator)


def _append_codes(instrs, glyph, extra: bytes):
    """Append ``extra`` code bytes right after ``glyph`` in its operator."""
    ins = instrs[glyph["i"]]
    if glyph["elem"] < 0:
        old = bytes(ins.operands[-1])
        nb = old[: glyph["bo"] + 2] + extra + old[glyph["bo"] + 2 :]
        instrs[glyph["i"]] = ContentStreamInstruction([String(nb)], ins.operator)
    else:
        arr = list(ins.operands[0])
        old = bytes(arr[glyph["elem"]])
        arr[glyph["elem"]] = String(old[: glyph["bo"] + 2] + extra + old[glyph["bo"] + 2 :])
        instrs[glyph["i"]] = ContentStreamInstruction([arr], ins.operator)
