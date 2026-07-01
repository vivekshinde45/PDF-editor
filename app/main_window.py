"""Main window: toolbar, scrollable page canvas, and a properties/edit panel."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QColorDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pdfcore.blocks import Span
from pdfcore.document import PdfError
from pdfcore.editor import Fidelity
from pdfcore.images import SignatureImage

from .controller import Controller
from .page_view import PageView
from .thumbnail_bar import ThumbnailBar

_SCALE = 2.0  # render zoom: crisp on screen, mapped back to points for edits


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Editor")
        self.resize(1100, 800)
        self.ctrl = Controller()
        self._page_index = 0
        self._last_query = ""   # for Find cycling
        self._find_pos = -1

        self._build_ui()
        self._refresh_actions()

    # -- construction ---------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)

        # Left: thumbnail navigation rail.
        self.thumbs = ThumbnailBar()
        self.thumbs.page_selected.connect(self._on_thumbnail_selected)
        root.addWidget(self.thumbs)

        # Center: toolbar + scrollable page.
        center = QVBoxLayout()
        bar = QHBoxLayout()
        self.btn_open = QPushButton("Open…")
        self.btn_open.clicked.connect(self._on_open)
        self.btn_save = QPushButton("Save a copy…")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_undo = QPushButton("Undo")
        self.btn_undo.clicked.connect(self._on_undo)
        self.btn_redo = QPushButton("Redo")
        self.btn_redo.clicked.connect(self._on_redo)
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedWidth(32)
        self.btn_prev.setToolTip("Previous page")
        self.btn_prev.clicked.connect(self._on_prev_page)
        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedWidth(32)
        self.btn_next.setToolTip("Next page")
        self.btn_next.clicked.connect(self._on_next_page)
        self.btn_delete_page = QPushButton("Delete page")
        self.btn_delete_page.setToolTip("Delete the current page")
        self.btn_delete_page.clicked.connect(self._on_delete_page)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.valueChanged.connect(self._on_page_changed)
        self.lbl_page_count = QLabel("/ 0")
        self.btn_add_text = QPushButton("Add text")
        self.btn_add_text.setCheckable(True)
        self.btn_add_text.setToolTip("Click on the page to place a new text box")
        self.btn_add_text.toggled.connect(self._on_toggle_add_text)
        self.btn_add_signature = QPushButton("Add signature")
        self.btn_add_signature.setCheckable(True)
        self.btn_add_signature.setToolTip("Click on the page to place a signature image")
        self.btn_add_signature.toggled.connect(self._on_toggle_add_signature)
        bar.addWidget(self.btn_open)
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_undo)
        bar.addWidget(self.btn_redo)
        bar.addWidget(self.btn_add_text)
        bar.addWidget(self.btn_add_signature)
        bar.addStretch(1)
        bar.addWidget(QLabel("Page"))
        bar.addWidget(self.btn_prev)
        bar.addWidget(self.page_spin)
        bar.addWidget(self.lbl_page_count)
        bar.addWidget(self.btn_next)
        bar.addWidget(self.btn_delete_page)
        center.addLayout(bar)

        # Find / replace row.
        find_row = QHBoxLayout()
        self.find_field = QLineEdit()
        self.find_field.setPlaceholderText("Find on this page…")
        self.find_field.returnPressed.connect(self._on_find)
        self.replace_field = QLineEdit()
        self.replace_field.setPlaceholderText("Replace with…")
        self.btn_find = QPushButton("Find")
        self.btn_find.clicked.connect(self._on_find)
        self.btn_replace_all = QPushButton("Replace all")
        self.btn_replace_all.clicked.connect(self._on_replace_all)
        find_row.addWidget(self.find_field, 1)
        find_row.addWidget(self.replace_field, 1)
        find_row.addWidget(self.btn_find)
        find_row.addWidget(self.btn_replace_all)
        center.addLayout(find_row)

        self.view = PageView()
        self.view.span_clicked.connect(self._on_span_clicked)
        self.view.signature_clicked.connect(self._on_signature_clicked)
        self.view.span_moved.connect(self._on_span_moved)
        self.view.signature_moved.connect(self._on_signature_moved)
        self.view.delete_requested.connect(self._on_delete)
        self.view.insert_requested.connect(self._on_insert_text)
        self.view.signature_insert_requested.connect(self._on_insert_signature)
        scroll = QScrollArea()
        scroll.setWidget(self.view)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(scroll, 1)
        root.addLayout(center, 1)

        # Right: properties / edit panel.
        self.panel = self._build_panel()
        root.addWidget(self.panel)

        self.setCentralWidget(central)

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(280)
        v = QVBoxLayout(panel)
        v.addWidget(QLabel("<b>Selected item</b>"))

        self.lbl_font = QLabel("Font: —")
        self.lbl_size = QLabel("Size: —")
        self.lbl_color = QLabel("Color: —")
        for w in (self.lbl_font, self.lbl_size, self.lbl_color):
            w.setWordWrap(True)
            v.addWidget(w)

        # Editable size + colour for the selected span.
        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Size:"))
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(1.0, 1000.0)
        self.size_spin.setSingleStep(0.5)
        self.size_spin.setDecimals(1)
        self.size_spin.setToolTip("Font size for text, or width for a selected signature")
        style_row.addWidget(self.size_spin)
        self.btn_color = QPushButton("Color…")
        self.btn_color.setToolTip("Change the colour of the selected text")
        self.btn_color.clicked.connect(self._pick_color)
        style_row.addWidget(self.btn_color)
        v.addLayout(style_row)
        # The colour currently chosen for the selected span (rgb 0..1).
        self._sel_color: tuple[float, float, float] = (0.0, 0.0, 0.0)

        # Text label + Bold toggle on one row.
        text_row = QHBoxLayout()
        text_row.addWidget(QLabel("Text:"))
        text_row.addStretch(1)
        self.btn_bold = QPushButton("B")
        self.btn_bold.setCheckable(True)
        self.btn_bold.setFixedWidth(32)
        fb = self.btn_bold.font()
        fb.setBold(True)
        self.btn_bold.setFont(fb)
        self.btn_bold.setToolTip("Bold the selected text")
        self.btn_bold.clicked.connect(self._toggle_bold)
        text_row.addWidget(self.btn_bold)

        self.btn_italic = QPushButton("I")
        self.btn_italic.setCheckable(True)
        self.btn_italic.setFixedWidth(32)
        fi = self.btn_italic.font()
        fi.setItalic(True)
        self.btn_italic.setFont(fi)
        self.btn_italic.setToolTip("Italicize the selected text")
        self.btn_italic.clicked.connect(self._toggle_italic)
        text_row.addWidget(self.btn_italic)
        v.addLayout(text_row)

        self.edit = QTextEdit()
        self.edit.setAcceptRichText(False)  # paste as plain text; we manage bold
        self.edit.setPlaceholderText("Select an editable block…")
        self.edit.cursorPositionChanged.connect(self._sync_bold_button)
        v.addWidget(self.edit, 1)

        self.btn_apply = QPushButton("Apply edit")
        self.btn_apply.clicked.connect(self._on_apply)
        v.addWidget(self.btn_apply)

        # Span actions: delete, duplicate, copy text.
        actions = QHBoxLayout()
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setToolTip("Delete the selected text (or press Delete)")
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_duplicate = QPushButton("Duplicate")
        self.btn_duplicate.setToolTip("Place a copy of the selected text nearby")
        self.btn_duplicate.clicked.connect(self._on_duplicate)
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setToolTip("Copy the selected text to the clipboard")
        self.btn_copy.clicked.connect(self._on_copy)
        for b in (self.btn_delete, self.btn_duplicate, self.btn_copy):
            actions.addWidget(b)
        v.addLayout(actions)

        self.btn_replace_signature = QPushButton("Replace signature…")
        self.btn_replace_signature.setToolTip("Replace the selected signature image")
        self.btn_replace_signature.clicked.connect(self._on_replace_signature)
        v.addWidget(self.btn_replace_signature)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)
        v.addStretch(1)
        return panel

    # -- actions --------------------------------------------------------

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf)")
        if not path:
            return
        try:
            self.ctrl.open(path)
        except PdfError as exc:
            if "encrypted" in str(exc).lower():
                pw, ok = QInputDialog.getText(
                    self, "Password", "This PDF is encrypted:", QLineEdit.EchoMode.Password
                )
                if not ok:
                    return
                try:
                    self.ctrl.open(path, pw)
                except PdfError as exc2:
                    return self._error(str(exc2))
            else:
                return self._error(str(exc))

        self._page_index = 0
        self.page_spin.setMaximum(self.ctrl.page_count)
        self.page_spin.setValue(1)
        self.lbl_page_count.setText(f"/ {self.ctrl.page_count}")
        self.thumbs.populate(self.ctrl)
        self._load_page()
        self._refresh_actions()

    def _on_save(self) -> None:
        if not self.ctrl.is_open:
            return
        src = self.ctrl.source_path or "edited.pdf"
        suggested = os.path.splitext(src)[0] + "-edited.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save a copy", suggested, "PDF files (*.pdf)")
        if not path:
            return
        try:
            self.ctrl.save_as(path)
            self._status(f"Saved: {path}", ok=True)
        except PdfError as exc:
            self._error(str(exc))

    def _on_undo(self) -> None:
        if self.ctrl.can_undo():
            self.ctrl.undo()
            self._load_page()
            self._refresh_actions()
            self._status("Reverted last edit.")

    def _on_redo(self) -> None:
        if self.ctrl.can_redo():
            self.ctrl.redo()
            self._load_page()
            self._refresh_actions()
            self._status("Reapplied edit.")

    def _on_delete(self) -> None:
        span = self.view.selected
        sig = self.view.selected_signature
        if not self.ctrl.is_open:
            return
        if span is not None:
            result = self.ctrl.delete_span(self._page_index, span)
        elif sig is not None:
            result = self.ctrl.delete_signature(self._page_index, sig)
        else:
            return
        if not result.ok:
            self._error(result.message or "Delete failed.")
            return
        self._load_page()
        self._refresh_actions()
        self._status("Deleted.", ok=True)

    def _on_duplicate(self) -> None:
        span = self.view.selected
        sig = self.view.selected_signature
        if not self.ctrl.is_open:
            return
        if span is not None:
            result = self.ctrl.duplicate_span(self._page_index, span)
        elif sig is not None:
            result = self.ctrl.duplicate_signature(self._page_index, sig)
        else:
            return
        if not result.ok:
            self._error(result.message or "Duplicate failed.")
            return
        self._load_page()
        self._refresh_actions()
        if result.fidelity == Fidelity.EXACT:
            self._status("Duplicated.", ok=True)
        else:
            self._status(f"⚠ {result.message}")

    def _on_copy(self) -> None:
        span = self.view.selected
        if span is None:
            return
        QGuiApplication.clipboard().setText(span.text)
        self._status("Copied text to clipboard.", ok=True)

    # -- size / colour --------------------------------------------------

    def _pick_color(self) -> None:
        r, g, b = (int(c * 255) for c in self._sel_color)
        chosen = QColorDialog.getColor(QColor(r, g, b), self, "Text colour")
        if chosen.isValid():
            self._sel_color = (chosen.red() / 255, chosen.green() / 255, chosen.blue() / 255)
            self._update_color_swatch()

    def _update_color_swatch(self) -> None:
        r, g, b = (int(c * 255) for c in self._sel_color)
        self.btn_color.setStyleSheet(
            f"background-color: #{r:02X}{g:02X}{b:02X}; "
            f"color: {'#000' if (r + g + b) > 384 else '#fff'};"
        )

    # -- find / replace -------------------------------------------------

    def _on_find(self) -> None:
        if not self.ctrl.is_open:
            return
        query = self.find_field.text()
        if not query:
            return
        matches = self.ctrl.find(self._page_index, query)
        if not matches:
            self._status(f"No matches for “{query}” on this page.")
            return
        # Cycle through matches on repeated Find.
        if query != self._last_query:
            self._find_pos = 0
            self._last_query = query
        else:
            self._find_pos = (self._find_pos + 1) % len(matches)
        span = matches[self._find_pos].span
        self.view.select(span)
        self._on_span_clicked(span)
        self._status(f"Match {self._find_pos + 1} of {len(matches)}.")

    def _on_replace_all(self) -> None:
        if not self.ctrl.is_open:
            return
        query = self.find_field.text()
        if not query:
            return
        replacement = self.replace_field.text()
        count = self.ctrl.replace_all_in_page(self._page_index, query, replacement)
        self._load_page()
        self._refresh_actions()
        if count:
            self._status(f"Replaced {count} occurrence(s).", ok=True)
        else:
            self._status(f"No matches for “{query}” on this page.")

    # -- add text -------------------------------------------------------

    def _on_toggle_add_text(self, on: bool) -> None:
        self.view.set_add_text_mode(on and self.ctrl.is_open)
        if on:
            self.btn_add_signature.setChecked(False)
            self._status("Click on the page to place a new text box.")

    def _on_insert_text(self, x: float, y: float) -> None:
        if not self.ctrl.is_open:
            return
        text, ok = QInputDialog.getText(self, "Add text", "Text to insert:")
        # Leave add-text mode after one placement.
        self.btn_add_text.setChecked(False)
        if not ok or not text:
            return
        # A default box growing right/down from the click point.
        rect = (x, y, x + 260, y + 40)
        result = self.ctrl.insert_text(self._page_index, rect, text)
        if not result.ok:
            self._error(result.message or "Insert failed.")
            return
        self._load_page()
        self._refresh_actions()
        if result.fidelity == Fidelity.EXACT:
            self._status("Text inserted.", ok=True)
        else:
            self._status(f"⚠ {result.message}")

    # -- add / replace signature ---------------------------------------

    def _on_toggle_add_signature(self, on: bool) -> None:
        self.view.set_add_signature_mode(on and self.ctrl.is_open)
        if on:
            self.btn_add_text.setChecked(False)
            self._status("Click on the page to place a signature image.")

    def _signature_file(self, title: str) -> str | None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All files (*)",
        )
        return path or None

    def _on_insert_signature(self, x: float, y: float) -> None:
        if not self.ctrl.is_open:
            return
        self.btn_add_signature.setChecked(False)
        path = self._signature_file("Choose signature image")
        if not path:
            return
        result = self.ctrl.insert_signature(self._page_index, path, x, y)
        if not result.ok:
            self._error(result.message or "Insert signature failed.")
            return
        self._load_page()
        self._refresh_actions()
        if result.fidelity == Fidelity.EXACT:
            self._status("Signature inserted.", ok=True)
        else:
            self._status(f"⚠ {result.message}")

    def _on_replace_signature(self) -> None:
        sig = self.view.selected_signature
        if sig is None or not self.ctrl.is_open:
            return
        path = self._signature_file("Choose replacement signature image")
        if not path:
            return
        result = self.ctrl.replace_signature(self._page_index, sig, path)
        if not result.ok:
            self._error(result.message or "Replace signature failed.")
            return
        old_center = self._center(sig.bbox)
        self._load_page()
        replacement = self._find_signature_near(old_center)
        if replacement is not None:
            self.view.select_signature(replacement)
            self._on_signature_clicked(replacement)
        self._refresh_actions()
        self._status("Signature replaced.", ok=True)

    def _on_page_changed(self, value: int) -> None:
        if not self.ctrl.is_open:
            return
        self._page_index = value - 1
        self._load_page()

    def _on_prev_page(self) -> None:
        self.page_spin.setValue(self.page_spin.value() - 1)  # clamped by min/max

    def _on_next_page(self) -> None:
        self.page_spin.setValue(self.page_spin.value() + 1)

    def _on_delete_page(self) -> None:
        if not self.ctrl.is_open or self.ctrl.page_count <= 1:
            return
        page_no = self._page_index + 1
        confirm = QMessageBox.question(
            self,
            "Delete page",
            f"Delete page {page_no}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self.ctrl.delete_page(self._page_index)
        except (IndexError, ValueError) as exc:
            self._error(str(exc))
            return
        self._page_index = min(self._page_index, self.ctrl.page_count - 1)
        self.page_spin.setMaximum(self.ctrl.page_count)
        self.lbl_page_count.setText(f"/ {self.ctrl.page_count}")
        self.thumbs.populate(self.ctrl)
        self.page_spin.setValue(self._page_index + 1)
        self._load_page()
        self._refresh_actions()
        self._status(f"Deleted page {page_no}.", ok=True)

    def _on_thumbnail_selected(self, index: int) -> None:
        if self.ctrl.is_open and 0 <= index < self.ctrl.page_count:
            self.page_spin.setValue(index + 1)  # routes through _on_page_changed

    def _on_span_clicked(self, span: Span | None) -> None:
        if span is None:
            if self.view.selected_signature is None:
                self._clear_panel()
            return

        self.view.select(span)
        self._show_text_controls(True)
        self.btn_replace_signature.setEnabled(False)
        self.lbl_font.setText(f"Font: {span.font_name}")
        self.lbl_size.setText(f"Size: {span.size:.1f} pt")
        r, g, b = (int(c * 255) for c in span.color)
        self.lbl_color.setText(f"Color: #{r:02X}{g:02X}{b:02X}")
        self.edit.setEnabled(True)
        self._load_span_text(span)
        # Seed the size/colour controls from the span (no change until applied).
        self._sel_color = span.color
        self.size_spin.blockSignals(True)
        self.size_spin.setValue(span.size)
        self.size_spin.blockSignals(False)
        self._update_color_swatch()
        self.btn_delete.setEnabled(True)
        self.btn_duplicate.setEnabled(True)
        self.btn_copy.setEnabled(True)
        self._status(
            "Edit in place keeps the layout. Drag the box (or use arrow keys; "
            "Shift = 10pt) to move this text."
        )

    def _on_signature_clicked(self, sig: SignatureImage | None) -> None:
        if sig is None:
            if self.view.selected is None:
                self._clear_panel()
            return
        self._show_text_controls(False)
        w = sig.bbox[2] - sig.bbox[0]
        h = sig.bbox[3] - sig.bbox[1]
        self.lbl_font.setText(f"Image: {sig.ext.upper() or 'raster'}")
        self.lbl_size.setText(f"Size: {w:.1f} x {h:.1f} pt")
        self.lbl_color.setText(f"Pixels: {sig.width} x {sig.height}")
        self.size_spin.blockSignals(True)
        self.size_spin.setValue(w)
        self.size_spin.blockSignals(False)
        self.size_spin.setEnabled(True)
        self.edit.clear()
        self.edit.setPlaceholderText("Selected signature image")
        self.btn_apply.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_duplicate.setEnabled(True)
        self.btn_copy.setEnabled(False)
        self.btn_replace_signature.setEnabled(True)
        self._status(
            "Drag the signature box to move it. Change Size, then Apply edit to resize it."
        )

    def _show_text_controls(self, enabled: bool) -> None:
        self.edit.setEnabled(enabled)
        self.size_spin.setEnabled(enabled)
        self.btn_color.setEnabled(enabled)
        self.btn_bold.setEnabled(enabled)
        self.btn_italic.setEnabled(enabled)
        self.btn_apply.setEnabled(enabled)

    def _load_span_text(self, span: Span) -> None:
        """Populate the editor with the span's text, preserving bold/italic."""
        self.edit.clear()
        cursor = self.edit.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if span.bold else QFont.Weight.Normal)
        fmt.setFontItalic(span.italic)
        cursor.insertText(span.text, fmt)
        self.edit.moveCursor(QTextCursor.MoveOperation.Start)

    def _toggle_bold(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if self.btn_bold.isChecked() else QFont.Weight.Normal)
        self._apply_format(fmt)

    def _toggle_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(self.btn_italic.isChecked())
        self._apply_format(fmt)

    def _apply_format(self, fmt: QTextCharFormat) -> None:
        cur = self.edit.textCursor()
        cur.mergeCharFormat(fmt)               # applies to selection if any
        self.edit.mergeCurrentCharFormat(fmt)  # and to subsequent typing
        self.edit.setFocus()

    def _sync_bold_button(self) -> None:
        fmt = self.edit.currentCharFormat()
        self.btn_bold.setChecked(fmt.fontWeight() >= QFont.Weight.Bold)
        self.btn_italic.setChecked(fmt.fontItalic())

    def _extract_runs(self) -> list[tuple[str, bool, bool]]:
        """Collapse the rich-text editor into (text, bold, italic) runs."""
        doc = self.edit.document()
        text = self.edit.toPlainText()
        cur = QTextCursor(doc)
        runs: list[tuple[str, bool, bool]] = []
        for i, ch in enumerate(text):
            cur.setPosition(i + 1)
            fmt = cur.charFormat()
            style = (fmt.fontWeight() >= QFont.Weight.Bold, fmt.fontItalic())
            if runs and (runs[-1][1], runs[-1][2]) == style:
                runs[-1] = (runs[-1][0] + ch, style[0], style[1])
            else:
                runs.append((ch, style[0], style[1]))
        return runs

    def _on_apply(self) -> None:
        span = self.view.selected
        sig = self.view.selected_signature
        if not self.ctrl.is_open:
            return
        if sig is not None:
            return self._on_resize_signature(sig)
        if span is None:
            return
        runs = self._extract_runs()
        block = self.view.selected_block
        block_spans = block.spans if block else None
        # Pass the panel's size/colour; the controller treats them as overrides
        # only when they differ from the span (so a pure text edit stays surgical).
        result = self.ctrl.edit_span(
            self._page_index, span, runs, block_spans,
            size=self.size_spin.value(), color=self._sel_color,
        )
        if not result.ok:
            self._error(result.message or "Edit failed.")
            return
        self._load_page()
        self._refresh_actions()
        if result.fidelity == Fidelity.EXACT:
            self._status("Edit applied.", ok=True)
        else:
            self._status(f"⚠ {result.message}")

    def _on_resize_signature(self, sig: SignatureImage) -> None:
        result = self.ctrl.resize_signature(self._page_index, sig, self.size_spin.value())
        if not result.ok:
            self._error(result.message or "Resize signature failed.")
            return
        old_center = self._center(sig.bbox)
        self._load_page()
        resized = self._find_signature_near(old_center)
        if resized is not None:
            self.view.select_signature(resized)
            self._on_signature_clicked(resized)
        self._refresh_actions()
        if result.fidelity == Fidelity.EXACT:
            self._status("Signature resized.", ok=True)
        else:
            self._status(f"⚠ {result.message}")

    def _on_span_moved(self, span: Span, dx: float, dy: float) -> None:
        if span is None or not self.ctrl.is_open:
            return
        result = self.ctrl.move_span(self._page_index, span, dx, dy)
        if not result.ok:
            self._error(result.message or "Move failed.")
            return
        # The page is re-extracted on reload, so the old Span object is stale.
        # Re-select the moved text at its new location to keep nudging fluid.
        target = (span.origin[0] + dx, span.origin[1] + dy)
        self._load_page()
        moved = self._find_span_near(span.text, target)
        if moved is not None:
            self.view.select(moved)
            self._on_span_clicked(moved)
        self._refresh_actions()
        if result.fidelity == Fidelity.EXACT:
            self._status("Moved.", ok=True)
        else:
            self._status(f"⚠ {result.message}")

    def _on_signature_moved(self, sig: SignatureImage, dx: float, dy: float) -> None:
        if sig is None or not self.ctrl.is_open:
            return
        result = self.ctrl.move_signature(self._page_index, sig, dx, dy)
        if not result.ok:
            self._error(result.message or "Move signature failed.")
            return
        target = self._center(
            (sig.bbox[0] + dx, sig.bbox[1] + dy, sig.bbox[2] + dx, sig.bbox[3] + dy)
        )
        self._load_page()
        moved = self._find_signature_near(target)
        if moved is not None:
            self.view.select_signature(moved)
            self._on_signature_clicked(moved)
        self._refresh_actions()
        if result.fidelity == Fidelity.EXACT:
            self._status("Signature moved.", ok=True)
        else:
            self._status(f"⚠ {result.message}")

    def _find_span_near(self, text: str, origin: tuple[float, float]) -> Span | None:
        """The span with matching text whose origin is closest to ``origin``."""
        best: Span | None = None
        best_d: float | None = None
        for s in self.view.spans:
            if s.text != text:
                continue
            d = (s.origin[0] - origin[0]) ** 2 + (s.origin[1] - origin[1]) ** 2
            if best_d is None or d < best_d:
                best_d, best = d, s
        return best

    def _find_signature_near(self, center: tuple[float, float]) -> SignatureImage | None:
        best: SignatureImage | None = None
        best_d: float | None = None
        for sig in self.view.signatures:
            sx, sy = self._center(sig.bbox)
            d = (sx - center[0]) ** 2 + (sy - center[1]) ** 2
            if best_d is None or d < best_d:
                best_d, best = d, sig
        return best

    def _center(self, bbox) -> tuple[float, float]:
        return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """PageUp/PageDown and Home/End navigate pages (when a doc is open)."""
        if not self.ctrl.is_open:
            return super().keyPressEvent(event)
        key = event.key()
        if key == Qt.Key.Key_PageDown:
            self._on_next_page()
        elif key == Qt.Key.Key_PageUp:
            self._on_prev_page()
        elif key == Qt.Key.Key_Home:
            self.page_spin.setValue(1)
        elif key == Qt.Key.Key_End:
            self.page_spin.setValue(self.ctrl.page_count)
        else:
            return super().keyPressEvent(event)

    # -- helpers --------------------------------------------------------

    def _load_page(self) -> None:
        if not self.ctrl.is_open:
            return
        rendered = self.ctrl.render(self._page_index, _SCALE)
        blocks = self.ctrl.blocks(self._page_index)
        signatures = self.ctrl.signatures(self._page_index)
        self.view.set_page(rendered, blocks, signatures)
        self.thumbs.set_current(self._page_index)
        self.btn_prev.setEnabled(self._page_index > 0)
        self.btn_next.setEnabled(self._page_index < self.ctrl.page_count - 1)
        self._last_query = ""  # restart Find cycling for the new page content
        self._clear_panel()

    def _clear_panel(self) -> None:
        self.lbl_font.setText("Font: —")
        self.lbl_size.setText("Size: —")
        self.lbl_color.setText("Color: —")
        self.edit.clear()
        self.edit.setPlaceholderText("Select an editable block…")
        self.edit.setEnabled(False)
        self.btn_bold.setChecked(False)
        self.btn_bold.setEnabled(False)
        self.btn_italic.setChecked(False)
        self.btn_italic.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_duplicate.setEnabled(False)
        self.btn_copy.setEnabled(False)
        self.size_spin.setEnabled(False)
        self.btn_color.setEnabled(False)
        self.btn_replace_signature.setEnabled(False)

    def _refresh_actions(self) -> None:
        is_open = self.ctrl.is_open
        self.btn_save.setEnabled(is_open)
        self.page_spin.setEnabled(is_open)
        multipage = is_open and self.ctrl.page_count > 1
        self.btn_prev.setEnabled(multipage and self._page_index > 0)
        self.btn_next.setEnabled(multipage and self._page_index < self.ctrl.page_count - 1)
        self.btn_delete_page.setEnabled(multipage)
        self.btn_undo.setEnabled(is_open and self.ctrl.can_undo())
        self.btn_redo.setEnabled(is_open and self.ctrl.can_redo())
        self.btn_find.setEnabled(is_open)
        self.btn_replace_all.setEnabled(is_open)
        self.btn_add_text.setEnabled(is_open)
        self.btn_add_signature.setEnabled(is_open)

    def _status(self, text: str, ok: bool = False) -> None:
        color = "#1d9e75" if ok else "#8a6d00"
        self.lbl_status.setText(f"<span style='color:{color}'>{text}</span>")

    def _error(self, text: str) -> None:
        QMessageBox.critical(self, "Error", text)
