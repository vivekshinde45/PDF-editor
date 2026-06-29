# PDF Editor — Improvements & v2 Plan

> Status: proposal / spec. No code changed by this document.
> Companion to [`2026-06-29-pdf-editor-design.md`](2026-06-29-pdf-editor-design.md) (the shipped v1 design).

## 1. Goal

Grow the editor from a precision **text-correction** tool into a more complete
PDF editor — **without regressing the text-editing core that already works**
(surgical content-stream edits, font-faithful redraw, span move, undo).

Two hard rules govern every item below:

1. **Don't touch what works.** The proven paths — `pdfcore/surgical.py`,
   `pdfcore/editor.py::apply_edit` / `apply_span_edit` / `move_span`,
   `pdfcore/fonts.py`, `pdfcore/blocks.py` — are not modified by additive
   features. Where a change *would* alter their behaviour, it ships as an opt-in
   **v2 path** behind a flag, leaving v1 as the default.
2. **Every change ships with tests.** No improvement is "done" until it has unit
   tests (engine) and, where it has UI surface, a headless smoke test. The
   existing suite must stay green.

## 2. The v1 / v2 strategy (recommendation)

The user asked: should we fork the app into v1 and v2, selectable at launch?

**Recommendation: a single mode flag, not a forked app.** A whole-app fork
doubles maintenance and *raises* the risk to the working core (two copies to
keep in sync). Instead:

- **Additive features are always on.** Find, add-text-box, redo, multi-page
  view, thumbnails, zoom, page ops, etc. add *new* code; they don't change the
  edit core, so they need no flag. Tests prove they don't regress v1.
- **Only behavioural changes to existing edits are flag-gated.** Replacing the
  reflow model, shrink-to-fit, a new layout engine — these change how a working
  feature behaves, so they live behind `--mode v2` (default `v1`).

### 2.1 Mode flag — how it plumbs through

```
main.py            parse --mode {v1,v2}  (default v1; env PDFEDITOR_MODE also honored)
   │  EditorMode
   ▼
MainWindow(mode)   passes mode to Controller; may show a "v2 (experimental)" badge
   │
   ▼
Controller(mode)   chooses v1 vs v2 engine function ONLY for flag-gated items
   │
   ▼
pdfcore/…          v1 functions untouched; v2 logic in NEW functions / NEW modules
```

- `EditorMode` is a tiny enum (`pdfcore/mode.py`): `V1 = "v1"`, `V2 = "v2"`.
- Default is **v1**. v2 is opt-in: `python main.py --mode v2`.
- The flag changes behaviour for *only* the items explicitly marked
  **[v2-gated]** below. Everything else ignores it.
- A v2 code path is a **new function** (e.g. `reflow_paragraph_v2`), never an
  edit to a v1 function. v1 and v2 are unit-tested side by side.

### 2.2 Classification used below

- **[additive]** — new code, cannot regress v1, always on. No flag.
- **[v2-gated]** — changes existing behaviour; behind `--mode v2`; v1 stays default.

## 3. Improvement catalog

Each item: what, why, additive/v2, files touched, risk, and **test cases**.
IDs (`IMP-n`) are stable references for the roadmap in §4.

---

### IMP-1 — Find / Find-and-Replace  **[additive]**

**What.** A search box: find text across the page (and document), highlight
matches, step through them, and optionally replace. Replace reuses the *existing*
`surgical_replace` → `apply_span_edit` pipeline per match — no new edit logic.

**Why.** The single most-expected missing feature; users reach for Ctrl+F first.

**Files.** New `pdfcore/search.py` (pure, find offsets in extracted text →
spans). UI: a search bar widget in `app/`. Controller gains `find()` /
`replace_next()` / `replace_all()` that *call existing edit methods*.

**Risk to v1.** None — replace delegates to the proven edit path; search is
read-only.

**Test cases.**
- `test_find_locates_all_occurrences` — a page with "12345" twice → 2 hits with
  correct spans/order.
- `test_find_is_case_insensitive_when_requested` — flag toggles match behaviour.
- `test_find_no_match_returns_empty` — missing term → `[]`, no error.
- `test_replace_next_changes_only_first_match` — two identical numbers; replace
  one → other is untouched (uses occurrence index, like `_occurrence`).
- `test_replace_all_changes_every_match_and_roundtrips` — save/reopen, all
  replaced, fidelity preserved for value edits.
- UI smoke `test_search_bar_steps_through_matches` — next/prev updates selection.

---

### IMP-2 — Multi-page continuous view + navigation  **[additive]**

**What.** Replace the single-page spin-box view with a scrollable list of pages
(or at least prev/next + go-to-page + keyboard PageUp/Down). Thumbnails sidebar
optional (IMP-3).

**Why.** Editing a real document one page at a time via a spin box is painful.

**Files.** `app/page_view.py` / `app/main_window.py` only. Engine untouched
(`document.render_page` already renders any page).

**Risk to v1.** UI-only. Edit/move/selection logic per page is unchanged; we
render more pages, same code per page.

**Test cases.**
- `test_render_each_page_independently` — multi-page fixture renders page 0..n
  without error (engine-level, already partly covered; extend).
- UI smoke `test_navigation_changes_current_page` — go-to-page updates the
  rendered page and clears selection.
- UI smoke `test_edit_on_page_2_targets_page_2` — selecting+editing a span on a
  non-first page edits the right page (guards against page-index bugs).

---

### IMP-3 — Thumbnail sidebar  **[additive]**

**What.** A left rail of page thumbnails; click to jump.

**Why.** Standard navigation aid for multi-page docs.

**Files.** `app/` only; uses `render_page` at a small scale.

**Risk to v1.** None (UI-only, read-only renders).

**Test cases.**
- UI smoke `test_thumbnail_click_navigates` — clicking thumbnail N sets current
  page to N.
- `test_thumbnail_render_scale_is_small` — thumbnails render at a reduced scale
  (perf guard; assert pixmap dimensions).

---

### IMP-4 — Redo  **[additive]**

**What.** Pair the existing undo stack with a redo stack. On a new edit, clear
redo (standard semantics).

**Why.** Undo without redo is half a feature; trivial to add given snapshots.

**Files.** `app/controller.py` only — add `_redo: list[bytes]`, `can_redo()`,
`redo()`; push to `_redo` on undo; clear on new edit. **No engine change.**

**Risk to v1.** Low and contained to the controller's snapshot bookkeeping.
Covered by tests.

**Test cases.**
- `test_redo_reapplies_undone_edit` — edit → undo → redo restores the edit.
- `test_new_edit_clears_redo_stack` — edit A → undo → edit B → redo is a no-op.
- `test_redo_unavailable_initially` — `can_redo()` is False on open.
- `test_undo_redo_roundtrip_preserves_bytes` — undo→redo yields identical doc
  bytes (snapshot integrity).

---

### IMP-5 — Span property edits: font size & colour  **[additive]**

**What.** Let the panel change a span's **size** and **colour** (today only
text + bold/italic). Implemented by extending the *redraw* path's parameters —
size/colour are already arguments to `_draw_line` / `insert_text`; we just stop
forcing the original values.

**Why.** Common correction need (fix a wrong colour, bump a heading size).

**Files.** `pdfcore/editor.py` gains an *optional* `override_size` /
`override_color` (defaulting to the span's own values, so existing callers are
unchanged). UI: a size spinner + colour picker in the panel.

**Risk to v1.** Low — new **optional** params with defaults equal to current
behaviour. Existing calls pass nothing → identical output. Guarded by a test
that asserts byte/render equivalence when no override is given.

**Test cases.**
- `test_size_override_changes_rendered_height` — same text at 12pt vs 24pt →
  taller glyph bbox / more ink at larger size.
- `test_color_override_changes_pixel_color` — edit to red → red pixels appear
  where the text is.
- `test_no_override_matches_existing_behaviour` — calling with no override
  produces the same extracted text/size as before (regression guard).
- UI smoke `test_color_picker_applies_to_selected_span`.

---

### IMP-6 — Add a new text box (insert text)  **[additive]**

**What.** Click an empty area → type → a *new* text run is inserted there
(choose font from system fonts / a default). Distinct from editing an existing
span.

**Why.** "Add text" is core to what users call a PDF editor.

**Files.** New `pdfcore/insert.py` (`insert_text_box(page, rect, runs, font,
size, color)`) using `insert_textbox`/`TextWriter`. UI: an "Add text" tool mode
in `page_view`. **Existing edit code is not involved.**

**Risk to v1.** None — entirely new module + new UI mode; the edit path is
untouched.

**Test cases.**
- `test_insert_adds_new_text_at_position` — insert "Hello" at a rect → it
  extracts at ~that position; pre-existing text is unchanged.
- `test_insert_does_not_disturb_existing_spans` — count/positions of original
  spans identical after an insert elsewhere.
- `test_insert_wraps_in_box` — long text wraps within the given rect.
- `test_insert_roundtrips` — save/reopen retains the inserted text.
- UI smoke `test_add_text_mode_inserts_on_click`.

---

### IMP-7 — Delete / duplicate a span  **[additive]**

**What.** Delete the selected span (redact only, no reinsert) or duplicate it.

**Why.** Quick corrections; deletion is just the redaction half we already do.

**Files.** `pdfcore/editor.py` — `delete_span(page, span)` (redact, no draw);
duplicate = `move_span` of a copy. Controller wiring + Delete key.

**Risk to v1.** Low — `delete_span` reuses the exact redaction call already used
by `apply_span_edit`; no reinsertion to get wrong.

**Test cases.**
- `test_delete_span_removes_text` — deleted text gone on re-extraction; other
  text intact.
- `test_delete_preserves_background_graphics` — like the existing graphics test,
  no white box stamped.
- `test_duplicate_span_creates_second_copy` — two copies of the text afterward.
- UI smoke `test_delete_key_removes_selected_span`.

---

### IMP-8 — Annotations: highlight (and optionally note/strike)  **[additive]**

**What.** Add a highlight annotation over selected text; optionally strikeout /
underline-as-annotation and sticky notes. These are PDF *annotations*, separate
from content editing.

**Why.** Reviewing/marking is a top-3 PDF use case.

**Files.** New `pdfcore/annotate.py` using PyMuPDF `add_highlight_annot` etc.
UI: a highlight tool. Edit core untouched.

**Risk to v1.** None — annotations are an independent layer.

**Test cases.**
- `test_highlight_adds_annotation` — page annot count increases by 1; bbox
  matches selection.
- `test_highlight_does_not_change_text` — extracted text identical after
  highlighting.
- `test_remove_annotation` — added annot can be deleted.
- UI smoke `test_highlight_tool_marks_selection`.

---

### IMP-9 — Page operations: rotate / delete / reorder / insert blank  **[additive]**

**What.** Rotate a page, delete a page, reorder pages, insert a blank page,
merge another PDF, split.

**Why.** Standard document management; users expect it.

**Files.** New `pdfcore/pages.py` wrapping PyMuPDF page APIs (`delete_page`,
`move_page`, `insert_page`, `Document.insert_pdf`). UI: page-context actions /
thumbnail right-click. Edit core untouched.

**Risk to v1.** None — operates on page structure, not text content. (Care:
invalidate per-page caches / selection after a reorder — covered by a test.)

**Test cases.**
- `test_rotate_page_sets_rotation` — rotation attribute updates; round-trips.
- `test_delete_page_reduces_count`.
- `test_reorder_pages_changes_order` — page text order matches new sequence.
- `test_insert_blank_page_adds_empty_page`.
- `test_merge_appends_pages_from_other_pdf`.
- UI smoke `test_page_ops_refresh_view_and_clear_selection`.

---

### IMP-10 — Copy text to clipboard / extract text  **[additive]**

**What.** Copy selected span text; "export page text" to .txt.

**Why.** Small, expected convenience.

**Files.** UI + a thin `controller.page_text(index)`.

**Risk to v1.** None (read-only).

**Test cases.**
- `test_page_text_returns_reading_order_text`.
- UI smoke `test_copy_selected_span_to_clipboard`.

---

### IMP-11 — Document security: set / remove password  **[additive]**

**What.** Save a copy with a user/owner password and permissions; remove an
existing password (when opened with the right one).

**Why.** Common need; v1 can *open* encrypted PDFs but not *set* protection.

**Files.** `pdfcore/document.py::save_as` gains optional encryption params
(default none → current behaviour). UI: a "Protect…" dialog.

**Risk to v1.** Low — optional params default to today's plain save. Regression
test asserts default save is byte-comparable in structure.

**Test cases.**
- `test_save_with_password_requires_password_to_reopen`.
- `test_remove_password_saves_unprotected_copy`.
- `test_default_save_unchanged` — no encryption args → opens without password
  (guards the default path).

---

### IMP-12 — Shrink-to-fit on overflow  **[v2-gated]**

**What.** When replacement text is longer than the block, *optionally* reduce
font size (or condense) so it fits, instead of flowing past the box. Today v1
flows downward and warns (documented, intentional). v2 offers auto-fit.

**Why.** Nicer for fixed-size cells/labels — but it **changes** an existing,
deliberate v1 behaviour, so it must not be forced on v1 users.

**Files.** New `editor.py::_layout_runs_autofit_v2` (NEW function). Controller
picks it only when `mode is V2`. v1 `_layout_runs` untouched.

**Risk to v1.** None when default — v1 path byte-identical. v2 is opt-in.

**Test cases.**
- `test_autofit_v2_reduces_size_to_fit` — long text in a small box → final size
  < original, text within bounds, no overflow flag.
- `test_v1_overflow_unchanged` — same input in v1 still returns OVERFLOW and
  flows down (regression guard that v2 didn't leak into v1).
- `test_autofit_respects_min_size_floor` — won't shrink below a readable floor;
  falls back to overflow + warning.

---

### IMP-13 — Full paragraph reflow (multi-line re-wrap)  **[v2-gated]**

**What.** v1 reflows only the edited span's own line. v2 re-wraps an entire
multi-line paragraph when its text changes, so a longer/shorter edit re-lays the
whole block cleanly.

**Why.** Better for prose editing — but it's a fundamental change to the layout
model and the riskiest item, so strictly v2/opt-in.

**Files.** New `editor.py::reflow_paragraph_v2` (NEW). v1 paths untouched.

**Risk to v1.** None when default. This is exactly the kind of change the v1/v2
flag exists for.

**Test cases.**
- `test_paragraph_reflow_v2_rewraps_longer_text` — added sentence re-wraps
  across lines, no overlap, text preserved.
- `test_paragraph_reflow_v2_preserves_styles` — bold/italic runs survive
  re-wrap.
- `test_v1_single_line_reflow_unchanged` — v1 behaviour identical (regression).
- `test_reflow_v2_tables_still_protected` — column gaps still stop reflow.

---

### IMP-14 — OCR / scanned-PDF text layer  **[additive, large]**

**What.** Detect image-only (scanned) pages, run OCR (e.g. Tesseract via
`pytesseract`, or PyMuPDF's OCR if available), add a hidden/visible text layer,
then allow editing via the existing pipeline.

**Why.** The biggest expectation gap — most "edit my PDF" users have a scan.

**Files.** New `pdfcore/ocr.py`. New optional dependency (Tesseract). Edit core
unchanged — OCR just *produces editable spans* the existing code then handles.

**Risk to v1.** None to existing code, but adds a heavy external dependency;
keep it optional and feature-detected (skip tests if Tesseract absent, mirroring
the Verdana-font skip pattern already in `conftest.py`).

**Test cases.**
- `test_detect_scanned_page` — image-only page flagged as scanned.
- `test_ocr_produces_text_spans` (skip if Tesseract unavailable) — a rendered
  text image OCRs to the expected string.
- `test_ocr_layer_is_editable` — OCR'd span edits through the normal path.
- `test_digital_pdf_skips_ocr` — a text PDF is not OCR'd (no double layer).

---

## 4. Roadmap (suggested order)

Ordered by value ÷ effort, additive-first, riskiest last:

| Phase | Items | Rationale |
|-------|-------|-----------|
| **1 — Quick wins** | IMP-4 (redo), IMP-10 (copy text), IMP-7 (delete span) | Tiny, high-use, near-zero risk. |
| **2 — Navigation** | IMP-2 (multi-page), IMP-3 (thumbnails) | Makes real documents usable. |
| **3 — Core editing reach** | IMP-1 (find/replace), IMP-5 (size/colour), IMP-6 (add text box) | The most-requested editing features; all additive. |
| **4 — Document tools** | IMP-9 (page ops), IMP-8 (annotations), IMP-11 (security) | Rounds out "PDF editor" expectations. |
| **5 — v2 behavioural** | IMP-12 (shrink-to-fit), IMP-13 (paragraph reflow) | Opt-in via `--mode v2`; only after v1 stays rock-solid. |
| **6 — Big bet** | IMP-14 (OCR) | Largest effort + external dep; do last, optional. |

## 5. Testing strategy (unchanged philosophy)

- Keep the **engine pure and unit-tested** (`pdfcore` has no Qt). Every new
  engine function gets direct tests with on-the-fly fixture PDFs (see
  `tests/conftest.py`).
- UI gets **headless smoke tests** (`QT_QPA_PLATFORM=offscreen`), asserting
  wiring/behaviour, not pixels.
- **Regression guards are mandatory** for any item that adds optional params or
  a v2 path: a test proving the default/v1 behaviour is byte- or
  render-identical to today.
- The full suite (`pytest`, and the headless UI run) must stay green before any
  item is considered done.
- Skip-if-unavailable for optional deps (Tesseract, platform fonts), mirroring
  the existing Verdana skips.

## 6. Risks & mitigations

- **Scope creep into the working core.** Mitigation: the additive/[v2-gated]
  discipline above; new modules over edits to `surgical.py`/`editor.py`.
- **v2 logic leaking into v1.** Mitigation: v2 is always a *new* function chosen
  by the mode flag in the controller; paired regression tests assert v1
  unchanged.
- **Heavy deps (OCR, Qt addons).** Mitigation: keep optional, feature-detect,
  skip tests gracefully.
- **Multi-page caches/selection going stale after page ops.** Mitigation:
  explicit cache-invalidation + a smoke test (IMP-9).

## 7. Out of scope (still deferred)

PDF→LaTeX, advanced vector/path editing, form *creation* (vs. fill), digital
certificate signing, real-time collaboration. Revisit only after Phases 1–4 land
and prove stable.
