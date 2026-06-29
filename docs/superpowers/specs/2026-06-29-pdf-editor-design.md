# PDF Editor — v1 Design Spec

**Date:** 2026-06-29
**Status:** Approved (design), pending spec review

## 1. Goal

A local desktop application that lets a user open an existing PDF, edit the
text of existing text blocks in place (with reflow inside each block), and save
the result. Runs entirely on the user's machine — no cloud services.

This is a **precision text-correction / block-editing** tool, not a
"convert any PDF into a freely-editable Word document" tool. It targets
text-based (digitally generated) PDFs.

## 2. Scope

### In scope (v1)
- Open and render a PDF faithfully (embedded fonts, vector graphics, images
  all display as-authored — rendered by MuPDF, not reconstructed).
- Detect text blocks per page and let the user select one.
- Edit the text of a selected block; new text reflows within the block's
  bounding box, matching the original font, size, and color where possible.
- Save the edited PDF (full rewrite, fonts embedded/subset), protecting the
  original source file.
- Undo/redo of edits.

### Out of scope (v1 — YAGNI, deferred to later)
- Inserting brand-new text boxes.
- Inserting / moving / resizing images.
- Vector graphics editing.
- OCR / scanned (image-only) PDF editing.
- PDF → LaTeX export.
- Multi-column reflow across blocks, or document-wide reflow.

## 3. Constraints & decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python | Fastest path; strong PDF + GUI ecosystem |
| UI framework | PySide6 (Qt) | Mature desktop canvas, hit-testing, packaging |
| PDF engine | PyMuPDF (fitz) | Renders, extracts text with styling+coords, and redacts/reinserts text in one library |
| Font tooling | PyMuPDF built-ins (v1); fontTools later if subset augmentation is needed | Keep v1 dependencies minimal |
| Editing strategy | Block-level redact + reflow ("redact-and-redraw") | Enables free text editing; accepts some layout drift |
| Licensing | Personal / internal use | PyMuPDF is AGPL-3.0; acceptable for non-distributed use. **Risk: revisit before any distribution.** |

### Accepted fidelity trade-offs
- Editing a block **truly removes** the original glyphs and redraws new text.
  Untouched content is preserved, but the edited block is regenerated, so minor
  spacing/layout drift within that block is possible and accepted.
- If new characters require glyphs not present in a subsetted embedded font,
  the tool substitutes the nearest available font and **surfaces a visible
  warning** rather than failing silently.

## 4. Architecture

Three layers with clean boundaries. The engine layer has **no Qt imports** so
it is independently unit-testable.

```
UI (PySide6)  →  Controller/Model  →  PDF Engine (PyMuPDF)
```

### 4.1 Engine — `pdfcore/` (Qt-free, pure library)

- **`document.py`** — Open and save a PDF; expose page count; render a page to
  a pixmap (bytes + dimensions) at a requested DPI. Save uses write-to-temp +
  atomic replace; supports "save a copy".
- **`blocks.py`** — Extract text blocks for a page via
  `page.get_text("rawdict")`. Produce a list of `TextBlock`, each with:
  bounding box (PDF coordinates), and ordered `Span`s carrying text, font name,
  size, color, and per-span bbox. Flag blocks that are not safely editable
  (e.g. no extractable spans / vector-drawn text).
- **`editor.py`** — Apply an edit to a block: redact the original block region,
  reinsert the new text reflowed within the block bbox, matching font/size/color.
  Return an `EditResult` with success status and a `fidelity` flag
  (e.g. `font_substituted`, `overflow`).
- **`fonts.py`** — Given a block's font and a target string, determine whether
  the characters can be rendered with the original (embedded) font; otherwise
  select a substitute and report the substitution.

### 4.2 UI + controller — `app/`

- **`main_window.py`** — Top toolbar (open, save, edit-text tool, undo/redo),
  left thumbnail rail, center page canvas, right properties panel.
- **`page_view.py`** — Render the page pixmap; draw detected block boxes;
  hit-test clicks → block selection; host the inline text editor for the
  selected block.
- **`controller.py`** — Hold the open document, current page, selection, the
  edit transaction list, and the undo/redo stack. Mediate all UI ↔ engine
  calls. After each edit, request a fresh render from the mutated document so
  the canvas always reflects ground truth.

### 4.3 Data model (key types)

- `Span` — `{ text, font_name, size, color, bbox }`
- `TextBlock` — `{ bbox, spans: [Span], editable: bool, reason_if_not: str|None }`
- `EditResult` — `{ ok: bool, fidelity: enum, message: str|None }`
  where `fidelity ∈ { exact, font_substituted, overflow }`

## 5. Data flow — single edit

1. User clicks the canvas → `page_view` hit-tests against block bboxes →
   selects a `TextBlock`.
2. Properties panel displays font / size / color from the block's first span.
3. User edits the text inline; on commit, controller calls
   `editor.apply_edit(page, block, new_text)`.
4. Engine redacts the original block region, reinserts reflowed text, returns
   an `EditResult`.
5. Controller re-renders the page from the **mutated** document → canvas
   updates. The edit is pushed onto the undo stack. Any non-`exact` fidelity
   flag is shown as a warning in the properties panel.
6. On Save → `document.save()` (full rewrite, fonts embedded/subset, temp +
   atomic replace; default is "save a copy" to protect the source).

## 6. Error handling

- **Open:** encrypted PDF → prompt for password; corrupt/unreadable → error
  dialog; non-PDF → reject with message.
- **Edit:** font substitution needed → complete edit, set `font_substituted`,
  show warning. Block not editable (no spans / vector text) → disable editing
  for that block with an explanatory tooltip. Replacement text overflows the
  block bbox → set `overflow`, warn, and (v1) clip; offer "shrink to fit" as a
  later option.
- **Save:** write to temp file then atomic-replace the target; on any failure
  the original is left intact. Never overwrite the source file by default.

## 7. Testing strategy

- **Engine (primary):** Qt-free, unit-tested with pytest against fixture PDFs:
  digitally-generated, subsetted-font, multi-column, and image-bearing samples.
  - Assertions: block extraction count and approximate positions; an edit
    yields the expected text on re-extraction; `font_substituted` fires when a
    glyph is missing; save round-trips and the file reopens cleanly with the
    edit present.
- **UI (light):** a few `pytest-qt` smoke tests (window opens, document loads,
  block selection emits the right signal) plus manual testing on real files.

## 8. Build order (milestones)

1. **M1 — Engine: open + render.** `document.py` opens a PDF and renders a page
   to a pixmap. Unit test against a fixture.
2. **M2 — Engine: block extraction.** `blocks.py` returns `TextBlock`s with
   spans and editability flags. Tested.
3. **M3 — UI shell.** `main_window` + `page_view` render pages, thumbnails, and
   draw block boxes; click selects a block and shows its properties.
4. **M4 — Engine: edit + reflow.** `editor.py` + `fonts.py` redact and reinsert
   reflowed text with fidelity reporting. Tested.
5. **M5 — Wire edit into UI + undo + save.** Inline editing, fidelity warnings,
   undo/redo, save-a-copy.

M1–M5 constitute the shippable v1.

## 9. Open risks

- **Reconstruction quality varies by document.** The earliest possible
  validation is M1–M3 on the user's *real* PDFs — block detection quality is
  the make-or-break factor and should be checked before investing in M4–M5.
- **PyMuPDF AGPL licensing** must be revisited if the tool is ever distributed.
- **Complex layouts** (tables drawn as vector rules, justified text, overlapping
  elements) may detect/edit imperfectly; acceptable for v1.
