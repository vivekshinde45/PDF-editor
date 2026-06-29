"""A left-rail list of page thumbnails for quick navigation.

Read-only: it renders each page at a small scale and emits the page index when
the user picks one. It never touches page content — editing stays on the main
canvas, one page at a time, exactly as before.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem

# Render scale for thumbnails — small, so a many-page document stays cheap.
_THUMB_SCALE = 0.18


class ThumbnailBar(QListWidget):
    page_selected = Signal(int)  # 0-based page index

    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(140)
        self.setIconSize(QSize(110, 150))
        self.setSpacing(4)
        self.setUniformItemSizes(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.currentRowChanged.connect(self._on_row_changed)

    def populate(self, controller) -> None:
        """Render a thumbnail for every page of the open document."""
        self.blockSignals(True)
        self.clear()
        for i in range(controller.page_count):
            rendered = controller.render(i, _THUMB_SCALE)
            icon = QIcon(self._pixmap(rendered))
            item = QListWidgetItem(icon, f"{i + 1}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.addItem(item)
        self.blockSignals(False)

    def set_current(self, index: int) -> None:
        """Highlight ``index`` without re-emitting page_selected (avoid loops)."""
        if 0 <= index < self.count() and index != self.currentRow():
            self.blockSignals(True)
            self.setCurrentRow(index)
            self.blockSignals(False)

    @staticmethod
    def _pixmap(rendered) -> QPixmap:
        img = QImage(
            rendered.samples,
            rendered.width,
            rendered.height,
            rendered.stride,
            QImage.Format.Format_RGB888,
        ).copy()
        return QPixmap.fromImage(img)

    def _on_row_changed(self, row: int) -> None:
        if row >= 0:
            self.page_selected.emit(row)
