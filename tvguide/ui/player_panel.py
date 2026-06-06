"""Video surface plus a full transport bar that drives the embedded player."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import theme
from ..player.mpvwidget import MpvWidget
from .widgets import fmt_duration


class _CrtOverlay(QWidget):
    """A translucent scanline + vignette layer drawn over the video."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(0, 0, 0, 55), 1))
        y = 0
        while y < h:
            p.drawLine(0, y, w, y)
            y += 3
        g = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        g.setColorAt(0.55, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 140))
        p.fillRect(self.rect(), g)


class PlayerPanel(QFrame):
    prevProgram = Signal()
    nextProgram = Signal()
    goLive = Signal()
    fullscreenToggled = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._duration = 0.0
        self._seeking = False

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Now-playing strip.
        info = QHBoxLayout()
        self.channel_lbl = QLabel("\u25c9 No channel")
        self.channel_lbl.setStyleSheet(
            f"color:{theme.ACCENT}; font-weight:800; letter-spacing:1px; font-size:12px;")
        self.now_lbl = QLabel("Nothing playing")
        self.now_lbl.setStyleSheet(f"color:{theme.TEXT}; font-weight:700; font-size:15px;")
        self.next_lbl = QLabel("")
        self.next_lbl.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
        self.next_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        info.addWidget(self.channel_lbl)
        info.addSpacing(12)
        info.addWidget(self.now_lbl, 1)
        info.addWidget(self.next_lbl)
        root.addLayout(info)

        # Video surface.
        self.video = MpvWidget()
        self.video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video.setMinimumHeight(280)
        root.addWidget(self.video, 1)
        # CRT overlay rides on top of the video and tracks its size.
        self._crt = False
        self.crt_overlay = _CrtOverlay(self.video)
        self.crt_overlay.hide()
        self.video.installEventFilter(self)

        # Seek bar.
        seek = QHBoxLayout()
        self.cur_time = QLabel("0:00")
        self.cur_time.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace; color:{theme.MUTED}; font-size:11px;")
        self.tot_time = QLabel("0:00")
        self.tot_time.setStyleSheet(self.cur_time.styleSheet())
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderPressed.connect(self._on_seek_start)
        self.slider.sliderReleased.connect(self._on_seek_end)
        seek.addWidget(self.cur_time)
        seek.addWidget(self.slider, 1)
        seek.addWidget(self.tot_time)
        root.addLayout(seek)

        # Transport buttons.
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.live_btn = QPushButton("LIVE")
        self.live_btn.setProperty("live", True)
        self.live_btn.clicked.connect(self.goLive)
        bar.addWidget(self.live_btn)
        bar.addStretch()

        self.prev_btn = self._tbtn("\u23ee", self.prevProgram.emit)
        self.back_btn = self._tbtn("\u00ab10", lambda: self.video.seek_relative(-10))
        self.play_btn = self._tbtn("\u25b6", self.video.toggle_pause, main=True)
        self.fwd_btn = self._tbtn("10\u00bb", lambda: self.video.seek_relative(10))
        self.next_btn = self._tbtn("\u23ed", self.nextProgram.emit)
        for b in (self.prev_btn, self.back_btn, self.play_btn, self.fwd_btn, self.next_btn):
            bar.addWidget(b)
        bar.addStretch()

        # Volume + fullscreen.
        self.mute_btn = self._tbtn("\U0001f50a", self._toggle_mute)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setFixedWidth(110)
        self.vol.setRange(0, 130)
        self.vol.setValue(100)
        self.vol.valueChanged.connect(self.video.set_volume)
        self.audio_btn = self._tbtn("AUD", self._show_audio_menu)
        self.audio_btn.setToolTip("Audio track (defaults to English)")
        self.cc_btn = self._tbtn("CC", self._toggle_subs)
        self.cc_btn.setToolTip("Subtitles on/off")
        self.bw_btn = self._tbtn("B&W", self._toggle_bw)
        self.bw_btn.setToolTip("Black & white picture")
        self.crt_btn = self._tbtn("CRT", self._toggle_crt)
        self.crt_btn.setToolTip("CRT scanlines")
        self.aspect_btn = self._tbtn("AUTO", self._cycle_aspect)
        self.aspect_btn.setToolTip("Cycle aspect ratio / zoom")
        self.fs_btn = self._tbtn("\u26f6", self.fullscreenToggled.emit)
        self.fs_btn.setToolTip("Fullscreen (Esc to exit)")
        bar.addWidget(self.mute_btn)
        bar.addWidget(self.vol)
        bar.addWidget(self.audio_btn)
        bar.addWidget(self.cc_btn)
        bar.addWidget(self.bw_btn)
        bar.addWidget(self.crt_btn)
        bar.addWidget(self.aspect_btn)
        bar.addWidget(self.fs_btn)
        root.addLayout(bar)

        self._muted = False
        self.video.positionChanged.connect(self._on_position)
        self.video.durationChanged.connect(self._on_duration)
        self.video.pausedChanged.connect(self._on_paused)

    def _tbtn(self, text: str, slot, main: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("transportMain" if main else "transport")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    # -- info ---------------------------------------------------------------
    def set_now_playing(self, channel: str, title: str, subtitle: str) -> None:
        self.channel_lbl.setText(f"\u25c9 {channel}")
        self.now_lbl.setStyleSheet(
            f"color:{theme.TEXT}; font-weight:700; font-size:15px;")
        line = title
        if subtitle:
            line += f"   {subtitle}"
        self.now_lbl.setText(line)

    def show_error(self, msg: str) -> None:
        self.now_lbl.setStyleSheet(
            f"color:{theme.GOLD}; font-weight:700; font-size:14px;")
        self.now_lbl.setText(f"\u26a0  {msg}")

    def set_next(self, text: str) -> None:
        self.next_lbl.setText(f"NEXT  \u2192  {text}" if text else "")

    # -- transport state ----------------------------------------------------
    def _toggle_mute(self) -> None:
        self._muted = not self._muted
        self.video.set_mute(self._muted)
        self.mute_btn.setText("\U0001f507" if self._muted else "\U0001f50a")

    def _cycle_aspect(self) -> None:
        self.aspect_btn.setText(self.video.cycle_aspect())

    def _show_audio_menu(self) -> None:
        tracks = self.video.audio_tracks()
        menu = QMenu(self)
        if not tracks:
            act = menu.addAction("No audio tracks")
            act.setEnabled(False)
        for t in tracks:
            act = menu.addAction(("\u2713 " if t["selected"] else "    ") + t["label"])
            act.triggered.connect(lambda _=False, tid=t["id"]: self.video.set_audio(tid))
        menu.exec(self.audio_btn.mapToGlobal(self.audio_btn.rect().topLeft()))

    def _toggle_subs(self) -> None:
        on = self.video.toggle_subtitles()
        self.cc_btn.setStyleSheet(
            f"color:{theme.ACCENT};" if on else "")

    def _toggle_bw(self) -> None:
        self.set_bw(not self._bw)

    def _toggle_crt(self) -> None:
        self.set_crt(not self._crt)

    def set_bw(self, on: bool) -> None:
        self.video.set_grayscale(on)
        self.bw_btn.setStyleSheet(f"color:{theme.ACCENT};" if on else "")

    def set_crt(self, on: bool) -> None:
        self._crt = on
        if on:
            self.crt_overlay.setGeometry(self.video.rect())
            self.crt_overlay.show()
            self.crt_overlay.raise_()
        else:
            self.crt_overlay.hide()
        self.crt_btn.setStyleSheet(f"color:{theme.ACCENT};" if on else "")

    def apply_skin(self, crt: bool, bw: bool) -> None:
        """Apply persisted retro toggles (called by the main window on launch)."""
        self.set_bw(bw)
        self.set_crt(crt)

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if obj is self.video and event.type() == QEvent.Type.Resize:
            self.crt_overlay.setGeometry(self.video.rect())
        return super().eventFilter(obj, event)

    def _on_seek_start(self) -> None:
        self._seeking = True

    def _on_seek_end(self) -> None:
        if self._duration > 0:
            self.video.seek_absolute(self.slider.value() / 1000 * self._duration)
        self._seeking = False

    def _on_position(self, pos: float) -> None:
        self.cur_time.setText(fmt_duration(pos))
        if self._duration > 0 and not self._seeking:
            self.slider.setValue(int(pos / self._duration * 1000))

    def _on_duration(self, dur: float) -> None:
        self._duration = dur
        self.tot_time.setText(fmt_duration(dur))

    def _on_paused(self, paused: bool) -> None:
        self.play_btn.setText("\u25b6" if paused else "\u23f8")
