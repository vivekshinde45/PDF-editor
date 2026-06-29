"""The page canvas: renders a page, overlays editable text spans, handles clicks.

Editing happens at the SPAN level (a single positioned run of text) so that
editing one piece does not move its neighbours — this preserves column
alignment in tables. Block boxes are still drawn faintly for context.

Coordinate spaces:
- PDF points (engine) → screen pixels: multiply by ``scale``.
- screen pixels → PDF points (hit-testing): divide by ``scale``.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from pdfcore.blocks import Span, TextBlock


class PageView(QWidget):
    span_clicked = Signal(object)  # emits a Span or None
    # emits (Span, dx_points, dy_points) when the user moves the selected span
    span_moved = Signal(object, float, float)
    # emits the selected Span when the user presses Delete/Backspace
    delete_requested = Signal(object)

    # Drag must exceed this many screen pixels to count as a move (vs. a click).
    _DRAG_THRESHOLD = 3.0

    def __init__(self) -> None:
        super().__init__()
        self._pixmap: QPixmap | None = None
        self._blocks: list[TextBlock] = []
        self._spans: list[Span] = []
        self._span_block: dict[int, TextBlock] = {}
        self._scale: float = 1.0
        self._selected: Span | None = None
        self._selected_block: TextBlock | None = None
        self._press_pos = None        # QPointF where a press began (screen px)
        self._drag_offset = None      # QPointF live drag delta (screen px)
        self.setMinimumSize(400, 500)
        self.setMouseTracking(True)
        # Accept keyboard focus so arrow keys can nudge the selected span.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @property
    def spans(self) -> list[Span]:
        return self._spans

    def select(self, span: Span | None) -> None:
        """Programmatically select a span (e.g. to re-select after a move)."""
        self._selected = span
        self._selected_block = self._span_block.get(id(span)) if span else None
        self.update()

    def set_page(self, rendered, blocks: list[TextBlock]) -> None:
        img = QImage(
            rendered.samples,
            rendered.width,
            rendered.height,
            rendered.stride,
            QImage.Format.Format_RGB888,
        ).copy()  # copy: detach from the transient bytes buffer
        self._pixmap = QPixmap.fromImage(img)
        self._blocks = blocks
        self._spans = [s for b in blocks if b.editable for s in b.spans]
        self._span_block = {id(s): b for b in blocks if b.editable for s in b.spans}
        self._scale = rendered.scale
        self._selected = None
        self._selected_block = None
        self._press_pos = None
        self._drag_offset = None
        self.setFixedSize(rendered.width, rendered.height)
        self.update()

    @property
    def selected(self) -> Span | None:
        return self._selected

    @property
    def selected_block(self) -> TextBlock | None:
        return self._selected_block

    def clear_selection(self) -> None:
        self._selected = None
        self._selected_block = None
        self.update()

    # -- painting -------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._pixmap is None:
            return
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap)

        # Faint block outlines for context.
        for block in self._blocks:
            p.setPen(QPen(QColor(150, 150, 150, 50), 1, Qt.PenStyle.DotLine))
            p.drawRect(self._rect(block.bbox))

        # Editable spans (the click targets).
        for span in self._spans:
            rect = self._rect(span.bbox)
            if span is self._selected:
                p.setPen(QPen(QColor(40, 120, 220), 2, Qt.PenStyle.SolidLine))
                p.fillRect(rect, QColor(40, 120, 220, 32))
                p.drawRect(rect)
            else:
                p.setPen(QPen(QColor(40, 120, 220, 90), 1, Qt.PenStyle.DashLine))
                p.drawRect(rect)

        # Drag preview: a "ghost" of the selected span at its prospective drop
        # position, so the move is visible before it's committed.
        if self._selected is not None and self._drag_offset is not None:
            ghost = self._rect(self._selected.bbox).translated(
                self._drag_offset.x(), self._drag_offset.y()
            )
            p.setPen(QPen(QColor(220, 90, 40), 2, Qt.PenStyle.SolidLine))
            p.fillRect(ghost, QColor(220, 90, 40, 40))
            p.drawRect(ghost)
        p.end()

    def _rect(self, bbox) -> QRectF:
        x0, y0, x1, y1 = bbox
        s = self._scale
        return QRectF(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s)

    # -- interaction ----------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._pixmap is None:
            return
        pos = event.position()
        hit = None
        # Smallest matching span wins, so dense rows are easy to target.
        best_area = None
        for span in self._spans:
            r = self._rect(span.bbox)
            if r.contains(pos):
                area = r.width() * r.height()
                if best_area is None or area < best_area:
                    best_area, hit = area, span
        self._selected = hit
        self._selected_block = self._span_block.get(id(hit)) if hit else None
        self._press_pos = pos if hit is not None else None
        self._drag_offset = None
        self.setFocus()  # so arrow keys nudge this selection
        self.update()
        self.span_clicked.emit(hit)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # Track a drag only while the left button is held on a selected span.
        if self._selected is None or self._press_pos is None:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        offset = event.position() - self._press_pos
        # Ignore sub-threshold jitter so a plain click doesn't register as a move.
        if self._drag_offset is None and offset.manhattanLength() < self._DRAG_THRESHOLD:
            return
        self._drag_offset = offset
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        span = self._selected
        offset = self._drag_offset
        self._press_pos = None
        self._drag_offset = None
        if span is not None and offset is not None:
            dx = offset.x() / self._scale
            dy = offset.y() / self._scale
            self.update()
            self.span_moved.emit(span, dx, dy)
        else:
            self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Arrow keys nudge the selected span (Shift = a larger 10pt step)."""
        if self._selected is None:
            return super().keyPressEvent(event)
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit(self._selected)
            return
        step = 10.0 if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) else 1.0
        delta = {
            Qt.Key.Key_Left: (-step, 0.0),
            Qt.Key.Key_Right: (step, 0.0),
            Qt.Key.Key_Up: (0.0, -step),
            Qt.Key.Key_Down: (0.0, step),
        }.get(event.key())
        if delta is None:
            return super().keyPressEvent(event)
        self.span_moved.emit(self._selected, delta[0], delta[1])
