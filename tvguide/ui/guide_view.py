"""The two guide presentations: a magazine listing and a grid EPG."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import theme
from ..repo import Program
from .widgets import ProgramRow, fmt_time


class MagazineView(QScrollArea):
    """Vertical "TV Guide magazine" listing for one channel + day."""

    programActivated = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("guide")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._container.setObjectName("guideBody")
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(2)
        self._layout.addStretch()
        self.setWidget(self._container)
        self._rows: list[ProgramRow] = []
        self._accent = theme.ACCENT

    def set_accent(self, accent: str) -> None:
        self._accent = accent

    def populate(self, programs: list[Program], now_ts: float) -> None:
        # Clear existing.
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        while self._layout.count() > 1:  # keep trailing stretch
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        last_daypart = None
        insert_at = 0
        for prog in programs:
            if prog.daypart and prog.daypart != last_daypart:
                header = QLabel(prog.daypart.upper())
                header.setStyleSheet(
                    f"color:{self._accent}; font-size:11px; font-weight:800; "
                    "letter-spacing:2px; padding:12px 10px 4px 10px;")
                self._layout.insertWidget(insert_at, header)
                insert_at += 1
                last_daypart = prog.daypart
            row = ProgramRow(prog, self._accent)
            row.activated.connect(self.programActivated)
            row.set_now(prog.start_ts <= now_ts < prog.end_ts)
            self._layout.insertWidget(insert_at, row)
            insert_at += 1
            self._rows.append(row)

    def refresh_now(self, now_ts: float) -> ProgramRow | None:
        current = None
        for row in self._rows:
            is_now = row.program.start_ts <= now_ts < row.program.end_ts
            row.set_now(is_now)
            if is_now:
                current = row
        return current

    def scroll_to(self, row: ProgramRow) -> None:
        self.ensureWidgetVisible(row, 0, 80)


# --------------------------------------------------------------------------

_NOW_COLOR = "#ff5e8a"


class _ProgItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, program: Program, accent: str, view: "GridView"):
        super().__init__(rect)
        self.program = program
        self._view = view
        self._accent = accent
        self._is_now = False
        self.setAcceptHoverEvents(True)
        # Clip child text to this box so long descriptions never spill out.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        blurb = (program.blurb or "").strip()
        self.setToolTip(
            "\n".join(p for p in (program.display_title, program.subtitle, blurb) if p))

        pad = 7
        inner_w = rect.width() - 2 * pad
        fm_title = QFontMetrics(QFont("Inter", 9, QFont.Weight.DemiBold))
        title = QGraphicsSimpleTextItem(
            fm_title.elidedText(program.display_title, Qt.TextElideMode.ElideRight,
                                int(max(10, inner_w))), self)
        title.setFont(QFont("Inter", 9, QFont.Weight.DemiBold))
        title.setBrush(QBrush(QColor(theme.TEXT)))
        title.setPos(rect.x() + pad, rect.y() + pad)

        if program.subtitle and rect.width() > 48:
            fm_sub = QFontMetrics(QFont("Inter", 8))
            sub = QGraphicsSimpleTextItem(
                fm_sub.elidedText(program.subtitle, Qt.TextElideMode.ElideRight,
                                  int(max(10, inner_w))), self)
            sub.setFont(QFont("Inter", 8))
            sub.setBrush(QBrush(QColor(theme.MUTED)))
            sub.setPos(rect.x() + pad, rect.y() + pad + 16)

        self._apply_style()

    def set_now(self, is_now: bool) -> None:
        if is_now != self._is_now:
            self._is_now = is_now
            self._apply_style()

    def _apply_style(self) -> None:
        if self._is_now:
            self.setBrush(QBrush(QColor("#2c1622")))
            self.setPen(QPen(QColor(_NOW_COLOR), 2))
        else:
            self.setBrush(QBrush(QColor(theme.PANEL_HI)))
            self.setPen(QPen(QColor(self._accent), 1))

    def hoverEnterEvent(self, event):  # noqa: ANN001
        if not self._is_now:
            self.setBrush(QBrush(QColor("#1b2a3c")))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: ANN001
        self._apply_style()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):  # noqa: ANN001
        self._view.programActivated.emit(self.program.id)
        super().mousePressEvent(event)


class GridView(QGraphicsView):
    """Classic horizontal EPG grid (channels as lanes, time across)."""

    programActivated = Signal(int)

    PX_PER_MIN = 5
    LANE_H = 70
    HEADER_H = 30
    LABEL_W = 150

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("guide")
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QBrush(QColor(theme.PANEL)))
        self._scene.setBackgroundBrush(QBrush(QColor(theme.PANEL)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Repaint the whole viewport on scroll so the pinned channel-label
        # column (drawn in drawForeground) is always redrawn instead of being
        # scrolled over until the next mouse-move.
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self._origin_ts = 0.0
        self._now_ts = 0.0
        self._channels: list = []
        self._items: list[_ProgItem] = []
        self._now_line = None
        self._scene_height = 0.0

    def populate(self, channels: list, programs_by_channel: dict[int, list[Program]],
                 origin_ts: float, end_ts: float, now_ts: float) -> None:
        self._scene.clear()
        self._items = []
        self._origin_ts = origin_ts
        self._now_ts = now_ts
        self._channels = channels
        total_min = (end_ts - origin_ts) / 60
        width = self.LABEL_W + total_min * self.PX_PER_MIN
        height = self.HEADER_H + len(channels) * self.LANE_H
        self._scene_height = height
        self._scene.setSceneRect(0, 0, width, height)

        # Hour ruler.
        t = origin_ts
        while t < end_ts:
            x = self.LABEL_W + (t - origin_ts) / 60 * self.PX_PER_MIN
            line = self._scene.addLine(x, 0, x, height, QPen(QColor(theme.BORDER)))
            line.setZValue(-5)
            dt = datetime.fromtimestamp(t)
            lbl = self._scene.addText(dt.strftime("%a %H:%M"))
            lbl.setDefaultTextColor(QColor(theme.MUTED))
            lbl.setFont(QFont("JetBrains Mono", 8))
            lbl.setPos(x + 4, 6)
            t += 3600

        for li, ch in enumerate(channels):
            y = self.HEADER_H + li * self.LANE_H
            accent = ch.accent
            for prog in programs_by_channel.get(ch.id, []):
                x = self.LABEL_W + (prog.start_ts - origin_ts) / 60 * self.PX_PER_MIN
                w = max(8, prog.duration_sec / 60 * self.PX_PER_MIN)
                rect = QRectF(x + 1, y + 3, w - 2, self.LANE_H - 6)
                item = _ProgItem(rect, prog, accent, self)
                item.set_now(prog.start_ts <= now_ts < prog.end_ts)
                self._scene.addItem(item)
                self._items.append(item)

        # Now line.
        nx = self.LABEL_W + (now_ts - origin_ts) / 60 * self.PX_PER_MIN
        self._now_line = self._scene.addLine(nx, 0, nx, height, QPen(QColor(_NOW_COLOR), 2))
        self._now_line.setZValue(10)

    def update_now(self, now_ts: float) -> None:
        """Advance the now-line and ON-AIR highlight without a full rebuild."""
        self._now_ts = now_ts
        if self._now_line is not None and self._origin_ts:
            nx = self.LABEL_W + (now_ts - self._origin_ts) / 60 * self.PX_PER_MIN
            self._now_line.setLine(nx, 0, nx, self._scene_height)
        for item in self._items:
            item.set_now(item.program.start_ts <= now_ts < item.program.end_ts)

    def drawForeground(self, painter, rect) -> None:  # noqa: ANN001
        """Pin the channel-name column to the left edge while scrolling."""
        super().drawForeground(painter, rect)
        left = self.mapToScene(0, 0).x()
        painter.fillRect(QRectF(left, rect.top(), self.LABEL_W, rect.height()),
                         QColor(theme.PANEL))
        painter.setPen(QColor(theme.BORDER))
        painter.drawLine(int(left + self.LABEL_W), int(rect.top()),
                         int(left + self.LABEL_W), int(rect.bottom()))
        for li, ch in enumerate(self._channels):
            y = self.HEADER_H + li * self.LANE_H
            painter.setPen(QColor(ch.accent))
            f = painter.font()
            f.setBold(True)
            f.setPointSize(11)
            painter.setFont(f)
            painter.drawText(QRectF(left + 12, y + 8, self.LABEL_W - 16, 24),
                             Qt.AlignmentFlag.AlignVCenter, ch.name)

    def scroll_to_now(self) -> None:
        nx = self.LABEL_W + (self._now_ts - self._origin_ts) / 60 * self.PX_PER_MIN
        self.centerOn(nx, self.HEADER_H + self.LANE_H)
