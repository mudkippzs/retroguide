"""Reusable guide widgets: the magazine-style program row."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import theme
from ..repo import Program


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class ProgramRow(QFrame):
    """One magazine listing entry: time - Title: subtitle / blurb."""

    activated = Signal(int)  # program id

    def __init__(self, program: Program, accent: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.program = program
        self.setObjectName("progRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._accent = accent
        self._is_now = False
        self._missing = program.path is None

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(14)

        # Time column.
        timebox = QVBoxLayout()
        timebox.setSpacing(2)
        self.time_lbl = QLabel(fmt_time(program.start_ts))
        self.time_lbl.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace; font-size:17px; "
            f"font-weight:700; color:{accent};"
        )
        self.dur_lbl = QLabel(fmt_duration(program.duration_sec))
        self.dur_lbl.setStyleSheet(f"color:{theme.MUTED}; font-size:11px;")
        timebox.addWidget(self.time_lbl)
        timebox.addWidget(self.dur_lbl)
        timebox.addStretch()
        tw = QWidget()
        tw.setLayout(timebox)
        tw.setFixedWidth(62)
        root.addWidget(tw)

        # Accent divider.
        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background:{accent}; border-radius:1px;")
        root.addWidget(bar)

        # Main column.
        main = QVBoxLayout()
        main.setSpacing(3)
        title_text = program.display_title
        if self._missing:
            title_text += "  \u26a0"
        self.title_lbl = QLabel(title_text)
        self.title_lbl.setStyleSheet(
            f"font-size:15px; font-weight:700; color:{theme.TEXT};"
        )
        self.title_lbl.setWordWrap(True)
        main.addWidget(self.title_lbl)

        if program.subtitle:
            self.sub_lbl = QLabel(program.subtitle)
            self.sub_lbl.setStyleSheet(
                f"color:{theme.ACCENT if program.kind=='episode' else theme.GOLD}; "
                "font-size:12px; font-weight:600;"
            )
            self.sub_lbl.setWordWrap(True)
            main.addWidget(self.sub_lbl)

        if program.blurb:
            self.blurb_lbl = QLabel(program.blurb)
            self.blurb_lbl.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
            self.blurb_lbl.setWordWrap(True)
            main.addWidget(self.blurb_lbl)
        root.addLayout(main, 1)

        # Right badge.
        self.badge = QLabel("")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self.badge.setFixedWidth(64)
        root.addWidget(self.badge)

        self._apply_style()

    def set_now(self, is_now: bool) -> None:
        if is_now != self._is_now:
            self._is_now = is_now
            self._apply_style()

    def _apply_style(self) -> None:
        if self._is_now:
            self.badge.setText("● ON AIR")
            self.badge.setStyleSheet(
                "color:#ff5e8a; font-size:10px; font-weight:800; letter-spacing:1px;")
            self.setStyleSheet(
                "#progRow {"
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #2c1622, stop:1 #16101a);"
                "border:1px solid #ff5e8a; border-radius:12px; margin:2px 4px; }"
                "#progRow:hover { background: #361a29; }"
            )
        else:
            self.badge.setText("")
            self.setStyleSheet(
                "#progRow {"
                f"background:{theme.PANEL_HI}; border:1px solid transparent;"
                "border-radius:12px; margin:2px 4px; }"
                f"#progRow:hover {{ border:1px solid {theme.ACCENT_DIM}; }}"
            )

    def mousePressEvent(self, event):  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.program.id)
        super().mousePressEvent(event)
