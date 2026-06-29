# PDF Editor

A local desktop tool to open a PDF, edit the text of existing text blocks in
place, and save a copy. Runs entirely on your machine — no cloud.

This is **v1**: a precision text-correction / block-editing tool for
text-based (digitally generated) PDFs. See the design spec at
[`docs/superpowers/specs/2026-06-29-pdf-editor-design.md`](docs/superpowers/specs/2026-06-29-pdf-editor-design.md).

## What it does

- Opens and renders PDFs faithfully (embedded fonts, vector art, images shown
  as authored — rendered by MuPDF).
- Detects text blocks; click one to select it.
- Edit text **in place, one span at a time**, so surrounding content and table
  column alignment are preserved (see "Editing model" below).
- Reuses the original embedded font so the font and weight do not change —
  including **subset Identity-H/CID fonts** that ship no Unicode cmap (common in
  real documents): the font is augmented with a character map recovered from the
  document's own glyph usage. Substitutes a standard font (with a warning) only
  when a character isn't present in the embedded subset at all.
- Preserves a block's existing bold/italic formatting when you edit it (it is
  loaded into the editor as styled text, not flattened to one style).
- Bold/italic individual words: select text in the edit box and click **B** / **I**.
  Both are synthesized on the original font (bold = stroke, italic = shear), so
  the font family is preserved even with no embedded bold/italic variant.
- Save a copy (the original file is never overwritten).
- Undo.

## What it does NOT do (v1)

Insert new text boxes, edit/move images, vector editing, OCR / scanned PDFs,
PDF→LaTeX, underline, font-size/color changes, annotations, forms, page
operations (merge/split/reorder). These are deferred. Note: **underline** is not
a font attribute in PDF (it's a drawn line), so it isn't detected or applied.

## Setup

Requires **Python 3.11 or 3.12** (recommended). Newer versions such as 3.13/3.14
may not yet have prebuilt PySide6/PyMuPDF wheels and can fail to install. On
macOS/Linux use `python3`; on Windows the launcher is usually `python`.

```bash
# macOS/Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows
python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

## Run

After activating the venv, `python` points at the venv interpreter:

```bash
python main.py                   # open the app, then File → Open
python main.py path/to/file.pdf  # open a file directly
```

## Test

```bash
pytest                                   # engine tests
QT_QPA_PLATFORM=offscreen pytest         # include headless UI smoke tests
```

## Architecture

```
app/  (PySide6 GUI)        ──depends on──▶  pdfcore/  (Qt-free engine)
  main_window.py                              document.py   open / render / save
  page_view.py    canvas + block boxes        blocks.py     extract TextBlocks
  controller.py   UI↔engine, undo             fonts.py      font resolution
main.py           entry point                 editor.py     redact + reinsert
```

`pdfcore` has no Qt imports, so the engine is unit-tested directly.

## Editing model: surgical first, redraw fallback

When you edit a span, the tool first tries a **surgical content-stream edit** —
it rewrites only the glyph codes inside the original text operators and leaves
every positioning/font operator untouched. The font, weight, size, character
spacing and justification are therefore the *same objects the PDF already used*
and cannot change. This is how online editors stay faithful, and it gives
pixel-identical results for value/number edits (the common case).

Surgical editing is used when the edit changes text without changing style and
the new characters are drawable in the original font. It falls back to the
**redraw** path (below) when: the text lives inside an XObject, a new character
isn't available in the embedded subset, or you change bold/italic. The redraw
path reuses the original embedded font but re-renders, so on justified prose its
spacing is slightly tighter than the original.

Font fallback order on redraw: (1) the original embedded font (augmented with a
cmap if needed); (2) **the same family from installed system fonts** — e.g. real
`Verdana-Bold` when the embedded subset lacks a glyph you typed (a `7` that
wasn't used on that page), so the family/weight still match; (3) only as a last
resort, a generic Base-14 substitute (with a warning).

## Redraw model: in-place, span by span

You click and edit one **span** — a single positioned run of text (a word,
phrase, table cell, or value). The edit is drawn at that span's original
position, and the rest of its **line reflows**:

- Words that follow on the **same line** shift by the width change, so a
  sentence closes up (when shorter) or makes room (when longer) — no gaps, no
  overlap.
- Shifting **stops at a column-sized gap**: content after a wide gap is treated
  as a separate column and stays put, so **table/column alignment is preserved.**
- Other blocks (e.g. other table rows, other paragraphs) are never touched.

This gives sentence-like reflow where you want it and column stability where you
need it, from the same action. Full multi-line *paragraph* reflow (re-wrapping
across lines) is not done — only the edited span's own line reflows.

## Known limitations / notes

- Editing redraws the edited block; minor spacing drift within that block is
  possible and expected. Untouched content is preserved.
- If replacement text is longer than the original block, it flows downward
  below the original box (and may overlap content beneath it) rather than being
  clipped or lost — you'll get an overflow warning. Shrink-to-fit is deferred.
- PyMuPDF is AGPL-3.0 — fine for personal/internal use; revisit before any
  distribution.
- Complex layouts (tables drawn as vector rules, justified text, overlapping
  elements) may detect/edit imperfectly.
