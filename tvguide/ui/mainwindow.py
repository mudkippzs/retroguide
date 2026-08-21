"""RetroGuide main window: channels, guide, embedded player and setup flow."""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .. import repo
from ..config import Config
from ..db import connect, get_meta, init_db, set_meta
from .guide_view import GridView, MagazineView
from .health import HealthDots
from .player_panel import PlayerPanel
from .settings_dialog import SettingsDialog
from .workers import (
    Worker,
    task_add_new,
    task_blurbs,
    task_enrich,
    task_full_setup,
    task_probe,
    task_scan,
    task_schedule,
)

# Sleep-timer options in minutes (0 = off), cycled by the Sleep key/button.
_SLEEP_STEPS = (0, 30, 60, 90, 120)

_log = logging.getLogger("tvguide.ui")


class MainWindow(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.conn = connect()
        init_db(self.conn)
        self.setObjectName("root")
        self.setWindowTitle("RetroGuide")
        self.resize(1480, 920)

        self.channels: list[repo.ChannelInfo] = []
        self._channel_buttons: dict[int, QPushButton] = {}
        self.current_channel: repo.ChannelInfo | None = None
        self.current_program: repo.Program | None = None
        self.day_index = 0
        self.origin_ts = 0.0
        self.worker: Worker | None = None
        self._stream_server = None
        # "wide" | "compact" — resolved from cfg.ui.layout (+ auto aspect).
        self._layout_mode: str | None = None
        self._body_layout: QHBoxLayout | QVBoxLayout | None = None

        # Remote-style multi-digit tuning: digits buffer briefly before tuning.
        self._num_buffer = ""
        self._num_timer = QTimer(self)
        self._num_timer.setSingleShot(True)
        self._num_timer.timeout.connect(self._commit_number)
        # Sleep timer.
        self._sleep_deadline: float | None = None
        self._sleep_idx = 0

        self._stack = QStackedWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

        self._build_main_page()
        self._build_setup_page()

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._tick)
        self.clock_timer.start(1000)
        self._tick_count = 0

        self.reload()

    # ====================================================================
    # Layout
    # ====================================================================
    def _build_main_page(self) -> None:
        page = QWidget()
        page.setObjectName("root")
        v = QVBoxLayout(page)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(12)

        self.topbar = QWidget()
        self.topbar.setLayout(self._build_topbar())
        v.addWidget(self.topbar)

        self.sidebar = self._build_sidebar()
        self.player = PlayerPanel()
        self.player.prevProgram.connect(self._play_prev)
        self.player.nextProgram.connect(self._play_next)
        self.player.goLive.connect(self.go_live)
        self.player.fullscreenToggled.connect(self._toggle_fullscreen)
        self.player.video.endReached.connect(self._on_end_reached)
        self.player.video.loadFailed.connect(self._on_play_error)
        self.player.apply_skin(self.cfg.ui.crt, self.cfg.ui.bw)
        self.guide_panel = self._build_guide_panel()

        # Body host is re-laid-out between wide (side-by-side) and compact
        # (stacked) without destroying the child widgets.
        self.body_host = QWidget()
        self.body_host.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Expanding)
        v.addWidget(self.body_host, 1)
        self._stack.addWidget(page)
        self._apply_layout(force=True)

    def _build_topbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        brand = QLabel()
        brand.setObjectName("brand")
        brand.setText("RETRO<span style='color:%s'>GUIDE</span>" % theme.ACCENT)
        brand.setTextFormat(Qt.TextFormat.RichText)
        self._tagline = QLabel("YOUR LIBRARY, ON A SCHEDULE")
        self._tagline.setObjectName("tagline")
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(brand)
        col.addWidget(self._tagline)
        bar.addLayout(col)
        bar.addStretch()

        self.view_mag_btn = QPushButton("Magazine")
        self.view_grid_btn = QPushButton("Grid")
        self.view_mag_btn.setCheckable(True)
        self.view_grid_btn.setCheckable(True)
        self.view_mag_btn.setChecked(True)
        vg = QButtonGroup(self)
        vg.setExclusive(True)
        vg.addButton(self.view_mag_btn)
        vg.addButton(self.view_grid_btn)
        self.view_mag_btn.clicked.connect(lambda: self._set_view(0))
        self.view_grid_btn.clicked.connect(lambda: self._set_view(1))
        bar.addWidget(self.view_mag_btn)
        bar.addWidget(self.view_grid_btn)
        bar.addSpacing(6)

        self.layout_btn = QPushButton("Compact")
        self.layout_btn.setCheckable(True)
        self.layout_btn.setToolTip(
            "Compact / vertical layout for portrait displays (V)")
        self.layout_btn.clicked.connect(self._toggle_compact)
        bar.addWidget(self.layout_btn)
        bar.addSpacing(10)

        self.surprise_btn = QPushButton("\U0001f3b2")
        self.surprise_btn.setToolTip("Surprise me \u2014 tune to a random channel (R)")
        self.surprise_btn.clicked.connect(self._surprise)
        bar.addWidget(self.surprise_btn)

        self.sleep_btn = QPushButton("\U0001f319 Sleep")
        self.sleep_btn.setToolTip("Sleep timer: cycle 30/60/90/120 min / off (S)")
        self.sleep_btn.clicked.connect(self._cycle_sleep)
        bar.addWidget(self.sleep_btn)
        bar.addSpacing(16)

        self.rebuild_btn = QPushButton("\u21bb Rebuild")
        self.rebuild_btn.clicked.connect(self._rebuild_schedule)
        bar.addWidget(self.rebuild_btn)
        self.addnew_btn = QPushButton("\u2795 Add New")
        self.addnew_btn.setToolTip("Scan for newly added files and fold them into the schedule")
        self.addnew_btn.clicked.connect(lambda: self._run_task(task_add_new, "Add new"))
        bar.addWidget(self.addnew_btn)
        self.blurb_btn = QPushButton("\u2728 Blurbs")
        self.blurb_btn.setToolTip("Write retro-voice teasers for the week with your local model")
        self.blurb_btn.clicked.connect(lambda: self._run_task(task_blurbs, "Writing blurbs"))
        bar.addWidget(self.blurb_btn)
        self.refresh_btn = QPushButton("Refresh Library")
        self.refresh_btn.clicked.connect(lambda: self._start_setup(full=True))
        bar.addWidget(self.refresh_btn)
        self.stream_btn = QPushButton("\U0001f4e1 Stream")
        self.stream_btn.setCheckable(True)
        self.stream_btn.setToolTip("Broadcast the live channels to your LAN")
        self.stream_btn.clicked.connect(self._toggle_stream)
        bar.addWidget(self.stream_btn)
        self.settings_btn = QPushButton("\u2699")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        bar.addWidget(self.settings_btn)
        bar.addSpacing(16)

        self.health = HealthDots(self.cfg)
        bar.addWidget(self.health)
        bar.addSpacing(16)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color:{theme.ACCENT}; font-size:11px;")
        self.status_lbl.setMaximumWidth(260)
        bar.addWidget(self.status_lbl)
        bar.addSpacing(8)

        self.clock = QLabel("--:--:--")
        self.clock.setObjectName("clock")
        bar.addWidget(self.clock)
        return bar

    def _build_sidebar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        self.sidebar_head = QLabel("CHANNELS")
        self.sidebar_head.setStyleSheet(
            f"color:{theme.MUTED}; font-size:11px; font-weight:800; letter-spacing:2px;")
        v.addWidget(self.sidebar_head)
        self.channel_scroll = QScrollArea()
        self.channel_scroll.setWidgetResizable(True)
        self.channel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.channel_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self.channel_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.channel_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.channel_inner = QWidget()
        self.channel_inner.setStyleSheet("background: transparent;")
        self.channel_box = QVBoxLayout(self.channel_inner)
        self.channel_box.setContentsMargins(2, 2, 2, 2)
        self.channel_box.setSpacing(8)
        self.channel_scroll.setWidget(self.channel_inner)
        v.addWidget(self.channel_scroll, 1)
        self.channel_group = QButtonGroup(self)
        self.channel_group.setExclusive(True)
        return frame

    def _build_guide_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        v = QVBoxLayout(frame)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        self.guide_title = QLabel("GUIDE")
        self.guide_title.setStyleSheet(
            f"color:{theme.TEXT}; font-size:15px; font-weight:800; letter-spacing:1px;")
        v.addWidget(self.guide_title)

        self.day_bar = QHBoxLayout()
        self.day_bar.setSpacing(4)
        self.day_group = QButtonGroup(self)
        self.day_group.setExclusive(True)
        v.addLayout(self.day_bar)

        self.guide_stack = QStackedWidget()
        self.magazine = MagazineView()
        self.magazine.programActivated.connect(self._on_program_clicked)
        self.grid = GridView()
        self.grid.programActivated.connect(self._on_program_clicked)
        self.guide_stack.addWidget(self.magazine)
        self.guide_stack.addWidget(self.grid)
        v.addWidget(self.guide_stack, 1)
        return frame

    def _build_setup_page(self) -> None:
        page = QWidget()
        page.setObjectName("root")
        v = QVBoxLayout(page)
        v.addStretch()
        box = QFrame()
        box.setObjectName("panel")
        box.setMaximumWidth(640)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(40, 36, 40, 36)
        bl.setSpacing(16)
        title = QLabel("RETRO<span style='color:%s'>GUIDE</span>" % theme.ACCENT)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setObjectName("brand")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(title)
        sub = QLabel("Let's build your channels. This scans your drives, reads "
                     "runtimes, fetches metadata and programs a week of TV.")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color:{theme.MUTED};")
        bl.addWidget(sub)

        self.setup_status = QLabel("Ready when you are.")
        self.setup_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setup_status.setStyleSheet(f"color:{theme.ACCENT}; font-weight:600;")
        bl.addWidget(self.setup_status)
        self.setup_bar = QProgressBar()
        self.setup_bar.setRange(0, 0)
        self.setup_bar.hide()
        bl.addWidget(self.setup_bar)

        self.setup_btn = QPushButton("Build my TV  \u25b6")
        self.setup_btn.setObjectName("accent")
        self.setup_btn.clicked.connect(lambda: self._start_setup(full=True))
        bl.addWidget(self.setup_btn)

        row = QHBoxLayout()
        for label, fn in (("Scan", task_scan), ("Probe", task_probe),
                          ("Enrich", task_enrich), ("Schedule", task_schedule)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, f=fn, n=label: self._run_task(f, n))
            row.addWidget(b)
        bl.addLayout(row)

        wrap = QHBoxLayout()
        wrap.addStretch()
        wrap.addWidget(box)
        wrap.addStretch()
        v.addLayout(wrap)
        v.addStretch()
        self._stack.addWidget(page)

    # ====================================================================
    # Data loading
    # ====================================================================
    def reload(self) -> None:
        self.conn.commit()
        self.health.check()
        if not repo.has_schedule(self.conn):
            self._stack.setCurrentIndex(1)
            return
        self._stack.setCurrentIndex(0)
        self.channels = repo.list_channels(self.conn)
        bounds = repo.schedule_bounds(self.conn)
        if bounds:
            self.origin_ts = bounds[0]
        # Open on today's page rather than the first day of the grid.
        self.day_index = self._today_index()
        # Restore the last-watched channel across restarts (falls back to #1).
        if self.channels and self.current_channel is None:
            last = get_meta(self.conn, "last_channel")
            self.current_channel = next(
                (c for c in self.channels if c.slug == last), None) or self.channels[0]
        self._populate_channels()
        self._populate_days()
        # Restore the last guide view (magazine vs grid).
        last_view = get_meta(self.conn, "last_view")
        if last_view in ("0", "1"):
            idx = int(last_view)
            (self.view_mag_btn if idx == 0 else self.view_grid_btn).setChecked(True)
            self.guide_stack.setCurrentIndex(idx)
        self._refresh_guide()
        self._maybe_extend_schedule()

    # ====================================================================
    # Layout modes (wide vs compact / portrait)
    # ====================================================================
    def _resolve_layout(self) -> str:
        pref = (self.cfg.ui.layout or "auto").strip().lower()
        if pref in ("compact", "vertical", "portrait"):
            return "compact"
        if pref in ("wide", "landscape", "horizontal"):
            return "wide"
        # auto: portrait-ish windows get the stacked chrome.
        w, h = max(1, self.width()), max(1, self.height())
        if h >= w * 1.12:
            return "compact"
        if w >= h * 1.12:
            return "wide"
        return self._layout_mode or "wide"

    def _apply_layout(self, force: bool = False) -> None:
        mode = self._resolve_layout()
        if not force and mode == self._layout_mode and self._body_layout is not None:
            return
        self._layout_mode = mode
        compact = mode == "compact"

        # Detach children from the previous body layout without deleting them.
        if self._body_layout is not None:
            while self._body_layout.count():
                self._body_layout.takeAt(0)
            # Orphan the old layout so Qt can dispose it.
            QWidget().setLayout(self._body_layout)
            self._body_layout = None

        if compact:
            lay: QHBoxLayout | QVBoxLayout = QVBoxLayout(self.body_host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(10)
            lay.addWidget(self.player, 5)
            lay.addWidget(self.sidebar, 0)
            lay.addWidget(self.guide_panel, 3)
        else:
            lay = QHBoxLayout(self.body_host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            lay.addWidget(self.sidebar, 0)
            lay.addWidget(self.player, 3)
            lay.addWidget(self.guide_panel, 2)
        self._body_layout = lay

        self._style_sidebar(compact)
        self._rebuild_channel_box(compact)
        self.player.setMinimumHeight(200 if compact else 280)
        if hasattr(self, "_tagline"):
            self._tagline.setVisible(not compact)
        if hasattr(self, "status_lbl"):
            self.status_lbl.setMaximumWidth(140 if compact else 260)
        if hasattr(self, "layout_btn"):
            self.layout_btn.blockSignals(True)
            self.layout_btn.setChecked(compact)
            self.layout_btn.setText("Wide" if compact else "Compact")
            self.layout_btn.blockSignals(False)
        if self.channels:
            self._populate_channels()

    def _style_sidebar(self, compact: bool) -> None:
        self.sidebar_head.setVisible(not compact)
        if compact:
            self.sidebar.setMinimumWidth(0)
            self.sidebar.setMaximumWidth(16777215)
            self.sidebar.setFixedHeight(78)
            self.channel_scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.channel_scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self.sidebar.setMinimumHeight(0)
            self.sidebar.setMaximumHeight(16777215)
            self.sidebar.setFixedWidth(220)
            self.channel_scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.channel_scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _rebuild_channel_box(self, compact: bool) -> None:
        """Swap the channel strip between vertical and horizontal layouts."""
        want_h = compact
        have_h = isinstance(self.channel_box, QHBoxLayout)
        if want_h == have_h and self.channel_box is not None:
            # Orientation already correct — just clear children for repopulate.
            while self.channel_box.count():
                item = self.channel_box.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            self.channel_box.addStretch()
            return
        while self.channel_box.count():
            item = self.channel_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # Replace the layout on the inner widget.
        QWidget().setLayout(self.channel_box)
        if compact:
            self.channel_box = QHBoxLayout(self.channel_inner)
            self.channel_box.setContentsMargins(4, 4, 4, 4)
            self.channel_box.setSpacing(6)
        else:
            self.channel_box = QVBoxLayout(self.channel_inner)
            self.channel_box.setContentsMargins(2, 2, 2, 2)
            self.channel_box.setSpacing(8)
        self.channel_box.addStretch()

    def _toggle_compact(self) -> None:
        # Explicit preference (leaves "auto" behind so the choice sticks).
        self.cfg.ui.layout = "wide" if self._layout_mode == "compact" else "compact"
        try:
            self.cfg.save()
        except OSError:
            pass
        self._apply_layout(force=True)

    def resizeEvent(self, event):  # noqa: ANN001
        super().resizeEvent(event)
        if (self.cfg.ui.layout or "auto").strip().lower() == "auto":
            self._apply_layout()

    def _populate_channels(self) -> None:
        compact = self._layout_mode == "compact"
        # Drop existing buttons but keep the trailing stretch.
        while self.channel_box.count():
            item = self.channel_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._channel_buttons = {}
        insert_at = 0
        for num, ch in enumerate(self.channels, start=1):
            if compact:
                btn = QPushButton(f"{num}  {ch.name}")
                btn.setObjectName("channelCompact")
                btn.setMinimumHeight(48)
                btn.setMaximumHeight(56)
                btn.setMinimumWidth(108)
                btn.setMaximumWidth(168)
                btn.setToolTip(ch.tagline or ch.name)
                if ch.logo:
                    btn.setIcon(QIcon(ch.logo))
                    btn.setIconSize(QSize(22, 22))
                btn.setStyleSheet(
                    f"QPushButton#channelCompact:checked {{ border:1px solid {ch.accent}; }}")
            else:
                btn = QPushButton(f"{num}   {ch.name}\n")
                btn.setObjectName("channel")
                if ch.logo:
                    btn.setIcon(QIcon(ch.logo))
                    btn.setIconSize(QSize(26, 26))
                label = QLabel(ch.tagline, btn)
                label.move(40, 30)
                label.setStyleSheet(
                    f"color:{theme.MUTED}; font-size:10px; font-weight:400;")
                btn.setMinimumHeight(54)
                btn.setStyleSheet(
                    f"QPushButton#channel:checked {{ border:1px solid {ch.accent}; }}")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, c=ch: self._select_channel(c))
            self.channel_group.addButton(btn)
            self.channel_box.insertWidget(insert_at, btn)
            insert_at += 1
            self._channel_buttons[ch.id] = btn
            if self.current_channel and ch.id == self.current_channel.id:
                btn.setChecked(True)
        self.channel_box.addStretch()

    def _populate_days(self) -> None:
        while self.day_bar.count():
            item = self.day_bar.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for b in list(self.day_group.buttons()):
            self.day_group.removeButton(b)
        for i in range(self.cfg.schedule.days):
            day = datetime.fromtimestamp(self.origin_ts) + timedelta(days=i)
            btn = QPushButton(day.strftime("%a %d"))
            btn.setObjectName("day")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if i == self.day_index:
                btn.setChecked(True)
            btn.clicked.connect(lambda _=False, idx=i: self._select_day(idx))
            self.day_group.addButton(btn)
            self.day_bar.addWidget(btn)
        self.day_bar.addStretch()

    def _day_window(self) -> tuple[float, float]:
        start = self.origin_ts + self.day_index * 86400
        return start, start + 86400

    def _refresh_guide(self) -> None:
        if not self.current_channel:
            return
        now = datetime.now().timestamp()
        if self.guide_stack.currentIndex() == 0:
            start, end = self._day_window()
            programs = repo.programs_for(self.conn, self.current_channel.id, start, end)
            self.magazine.set_accent(self.current_channel.accent)
            self.magazine.populate(programs, now)
            self.guide_title.setText(f"{self.current_channel.name.upper()}  \u2022  GUIDE")
            cur = self.magazine.refresh_now(now)
            if cur and self.day_index == self._today_index():
                QTimer.singleShot(60, lambda: self.magazine.scroll_to(cur))
        else:
            bounds = repo.schedule_bounds(self.conn)
            if not bounds:
                return
            by_ch = {ch.id: repo.programs_for(self.conn, ch.id, bounds[0], bounds[1])
                     for ch in self.channels}
            self.grid.populate(self.channels, by_ch, bounds[0], bounds[1], now)
            self.guide_title.setText("ALL CHANNELS  \u2022  GRID")
            QTimer.singleShot(60, self.grid.scroll_to_now)

    def _today_index(self) -> int:
        now = datetime.now().timestamp()
        idx = int((now - self.origin_ts) // 86400)
        return max(0, min(self.cfg.schedule.days - 1, idx))

    # ====================================================================
    # Selection
    # ====================================================================
    def _select_channel(self, ch: repo.ChannelInfo) -> None:
        self.current_channel = ch
        self._check_channel_button(ch.id)
        set_meta(self.conn, "last_channel", ch.slug)
        self._refresh_guide()
        # A quick burst of static on the switch, like an old set retuning.
        self.player.static_wipe()
        # Pressing a channel button always tunes you in live to whatever is
        # airing right now, mid-show -- even re-pressing the current channel
        # (just like hitting the number on an old remote).
        self.go_live()

    def _check_channel_button(self, channel_id: int) -> None:
        btn = getattr(self, "_channel_buttons", {}).get(channel_id)
        if btn is not None:
            btn.setChecked(True)

    def _surf(self, delta: int) -> None:
        """Channel up/down, wrapping around the lineup."""
        if not self.channels:
            return
        idx = 0
        if self.current_channel is not None:
            idx = next((i for i, c in enumerate(self.channels)
                        if c.id == self.current_channel.id), 0)
        self._select_channel(self.channels[(idx + delta) % len(self.channels)])

    def _tune_number(self, num: int) -> None:
        if 1 <= num <= len(self.channels):
            self._select_channel(self.channels[num - 1])

    def _push_digit(self, digit: int) -> None:
        """Buffer digits so channels 10+ are reachable by number (e.g. 1 then 2
        tunes 12). A leading 0 is ignored; the number commits when no longer
        number could extend it, on Enter, or after a short pause."""
        if not self._num_buffer and digit == 0:
            return
        self._num_buffer += str(digit)
        self.player.flash_osd(f"CH {self._num_buffer}")
        total = len(self.channels)
        n = int(self._num_buffer)
        can_extend = any(
            str(k).startswith(self._num_buffer) and len(str(k)) > len(self._num_buffer)
            for k in range(1, total + 1))
        if n > total or not can_extend:
            self._commit_number()
        else:
            self._num_timer.start(1100)

    def _commit_number(self) -> None:
        self._num_timer.stop()
        if not self._num_buffer:
            return
        num = int(self._num_buffer)
        self._num_buffer = ""
        self._tune_number(num)

    def _surprise(self) -> None:
        """Tune to a random channel (other than the current one, if possible)."""
        if not self.channels:
            return
        pool = [c for c in self.channels
                if not self.current_channel or c.id != self.current_channel.id]
        pick = random.choice(pool or self.channels)
        self.player.flash_osd("\U0001f3b2 Surprise")
        self._select_channel(pick)

    def _cycle_sleep(self) -> None:
        """Cycle the sleep timer through the presets (last step wraps to off)."""
        self._sleep_idx = (self._sleep_idx + 1) % len(_SLEEP_STEPS)
        minutes = _SLEEP_STEPS[self._sleep_idx]
        if minutes:
            self._sleep_deadline = datetime.now().timestamp() + minutes * 60
            self.sleep_btn.setText(f"\U0001f319 {minutes}m")
            self.player.flash_osd(f"\U0001f319 Sleep {minutes} min")
        else:
            self._sleep_deadline = None
            self.sleep_btn.setText("\U0001f319 Sleep")
            self.player.flash_osd("\U0001f319 Sleep off")

    def _maybe_extend_schedule(self) -> None:
        """Roll a fresh week when the grid is about to run dry, so the guide
        never dead-ends. Only fires when idle and on the main page."""
        if self.worker and self.worker.isRunning():
            return
        if self._stack.currentIndex() != 0:
            return
        bounds = repo.schedule_bounds(self.conn)
        if not bounds:
            return
        now = datetime.now().timestamp()
        if bounds[1] - now < 86400:   # less than a day of programming left
            _log.info("schedule within a day of running out - auto-extending")
            self._run_task(task_schedule, "Extending schedule")

    def _select_day(self, idx: int) -> None:
        self.day_index = idx
        self._refresh_guide()

    def _set_view(self, idx: int) -> None:
        self.guide_stack.setCurrentIndex(idx)
        set_meta(self.conn, "last_view", str(idx))
        self._refresh_guide()

    # ====================================================================
    # Playback
    # ====================================================================
    def _on_program_clicked(self, program_id: int) -> None:
        prog = repo.get_program(self.conn, program_id)
        if not prog:
            return
        ch = next((c for c in self.channels if c.id == prog.channel_id), None)
        if ch:
            self.current_channel = ch
            self._check_channel_button(ch.id)
        # If you click the slot that's on the air right now, you join it live
        # (already in progress); past/future picks preview from the start.
        now = datetime.now().timestamp()
        live = prog.start_ts <= now < prog.end_ts
        self._play(prog, live=live)

    def go_live(self) -> None:
        if not self.current_channel:
            return
        now = datetime.now().timestamp()
        prog = repo.program_at(self.conn, self.current_channel.id, now)
        if prog:
            self._play(prog, live=True)

    def _play(self, prog: repo.Program, live: bool) -> None:
        # Skip programs whose file is missing, partial, or failed probe.
        if prog and (not prog.playable or not prog.path):
            prog = repo.next_playable(self.conn, prog.channel_id, prog.end_ts)
        if not prog or not prog.path:
            self.player.show_error("Nothing playable on this channel right now")
            return
        start_sec = 0.0
        if live:
            now = datetime.now().timestamp()
            start_sec = max(0.0, now - prog.start_ts)
        self.current_program = prog
        self.player.video.play(prog.path, start_sec)
        ch = next((c for c in self.channels if c.id == prog.channel_id), None)
        self.player.set_now_playing(ch.name if ch else "", prog.display_title, prog.subtitle)
        nxt = repo.next_playable(self.conn, prog.channel_id, prog.end_ts)
        self.player.set_next(f"{nxt.display_title}" if nxt else "")
        self._show_details(prog)

    def _show_details(self, prog: repo.Program) -> None:
        """Push synopsis + cast into the status line / OSD when available."""
        if not prog.title_id:
            return
        details = repo.get_title_details(self.conn, prog.title_id, prog.episode_id)
        if not details:
            return
        bits: list[str] = []
        if details.cast:
            bits.append("Starring " + ", ".join(details.cast[:4]))
        ov = details.episode_overview or details.overview or details.blurb
        if ov:
            bits.append(ov.strip().split(". ")[0][:160])
        if bits:
            self.status_lbl.setText("  ·  ".join(bits))

    def _on_play_error(self, reason: str) -> None:
        self.player.show_error(f"{reason} \u2014 skipping")
        self.status_lbl.setText(reason)
        # Auto-advance past a dead file so the channel never sticks on black.
        if self.current_program:
            nxt = repo.next_playable(
                self.conn, self.current_program.channel_id,
                self.current_program.end_ts)
            if nxt:
                self._play(nxt, live=False)

    def _on_end_reached(self) -> None:
        if not self.current_program:
            return
        nxt = repo.next_playable(self.conn, self.current_program.channel_id,
                                 self.current_program.end_ts)
        if nxt:
            self._play(nxt, live=False)

    def _play_next(self) -> None:
        if not self.current_program:
            return
        nxt = repo.next_playable(self.conn, self.current_program.channel_id,
                                 self.current_program.end_ts)
        if nxt:
            self._play(nxt, live=False)

    def _play_prev(self) -> None:
        if not self.current_program:
            return
        prev = self.conn.execute(
            "SELECT id FROM programs WHERE channel_id=? AND start_ts<? "
            "ORDER BY start_ts DESC LIMIT 1",
            (self.current_program.channel_id, self.current_program.start_ts),
        ).fetchone()
        if prev:
            p = repo.get_program(self.conn, prev["id"])
            if p:
                self._play(p, live=False)

    def _toggle_fullscreen(self) -> None:
        entering = not self.isFullScreen()
        # Cinema mode: hide the chrome so the video fills the screen.
        for w in (self.topbar, self.sidebar, self.guide_panel):
            w.setVisible(not entering)
        if entering:
            self.showFullScreen()
        else:
            self.showNormal()

    def keyPressEvent(self, event):  # noqa: ANN001
        key = event.key()
        if key == Qt.Key.Key_Escape and self.isFullScreen():
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_F:
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_Space:
            self.player.video.toggle_pause()
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            self._surf(-1)
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown):
            self._surf(1)
        elif key == Qt.Key.Key_Left:
            self.player.nudge_seek(-30)
        elif key == Qt.Key.Key_Right:
            self.player.nudge_seek(30)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.player.bump_volume(5)
        elif key == Qt.Key.Key_Minus:
            self.player.bump_volume(-5)
        elif key == Qt.Key.Key_M:
            self.player.toggle_mute()
        elif key == Qt.Key.Key_R:
            self._surprise()
        elif key == Qt.Key.Key_S:
            self._cycle_sleep()
        elif key == Qt.Key.Key_V:
            self._toggle_compact()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit_number()
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            self._push_digit(key - Qt.Key.Key_0)
        else:
            super().keyPressEvent(event)

    # ====================================================================
    # Tick
    # ====================================================================
    def _tick(self) -> None:
        now = datetime.now()
        self.clock.setText(now.strftime("%a %d %b   %H:%M:%S"))
        self._tick_count += 1
        now_ts = now.timestamp()
        # Keep the live ON-AIR markers moving without a click every few seconds.
        if self._tick_count % 5 == 0:
            if self.guide_stack.currentIndex() == 0:
                self.magazine.refresh_now(now_ts)
            else:
                self.grid.update_now(now_ts)
        # Sleep timer: pause playback once the deadline passes.
        if self._sleep_deadline is not None and now_ts >= self._sleep_deadline:
            self._sleep_deadline = None
            self._sleep_idx = 0
            self.sleep_btn.setText("\U0001f319 Sleep")
            self.player.video.set_paused(True)
            self.player.flash_osd("\U0001f319 Sleeping", ms=4000)
        # Every few minutes: refresh health dots and top up the schedule.
        if self._tick_count % 300 == 0:
            self.health.check()
            self._maybe_extend_schedule()

    # ====================================================================
    # Setup / pipeline
    # ====================================================================
    def _start_setup(self, full: bool) -> None:
        self._run_task(task_full_setup, "Setup")

    def _rebuild_schedule(self) -> None:
        self._run_task(task_schedule, "Rebuild schedule")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            self.status_lbl.setText("Settings saved")
            self.player.apply_skin(self.cfg.ui.crt, self.cfg.ui.bw)
            self._apply_layout(force=True)

    def _toggle_stream(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from ..stream import StreamServer
        if self._stream_server is not None and self._stream_server.running:
            self._stream_server.stop()
            self._stream_server = None
            self.stream_btn.setChecked(False)
            self.status_lbl.setText("LAN stream stopped")
            _log.info("LAN stream stopped by user")
            return
        try:
            self._stream_server = StreamServer(self.cfg)
            url = self._stream_server.start()
        except OSError as exc:
            self._stream_server = None
            self.stream_btn.setChecked(False)
            _log.error("could not start stream server: %s", exc)
            QMessageBox.warning(
                self, "Stream", f"Couldn't start the stream server:\n{exc}\n\n"
                "Is the port already in use? Change it in Settings.")
            return
        self.stream_btn.setChecked(True)
        self.status_lbl.setText("Streaming \u2192 LAN")
        QMessageBox.information(
            self, "RetroGuide is on the air",
            f"Open this address from any device on your network:\n\n{url}\n\n"
            "Pick a channel and you'll join whatever is airing right now.")

    def _run_task(self, task, label: str) -> None:
        if self.worker and self.worker.isRunning():
            return
        for b in (getattr(self, "setup_btn", None), self.rebuild_btn,
                  self.refresh_btn, self.blurb_btn, self.addnew_btn):
            if b is not None:
                b.setEnabled(False)
        self.setup_bar.show()
        self.setup_status.setText(f"{label}...")
        self.status_lbl.setText(f"{label}...")
        self.worker = Worker(self.cfg, task, label)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_task_done)
        self.worker.failed.connect(self._on_task_failed)
        self.worker.start()

    def _enable_task_buttons(self) -> None:
        for b in (getattr(self, "setup_btn", None), self.rebuild_btn,
                  self.refresh_btn, self.blurb_btn, self.addnew_btn):
            if b is not None:
                b.setEnabled(True)

    def _on_progress(self, msg: str, cur: int, total: int) -> None:
        self.setup_status.setText(msg)
        pct = f" {int(cur/total*100)}%" if total else ""
        self.status_lbl.setText(f"{msg[:40]}{pct}")
        if total:
            self.setup_bar.setRange(0, total)
            self.setup_bar.setValue(cur)
        else:
            self.setup_bar.setRange(0, 0)

    def _on_task_done(self, msg: str) -> None:
        self.setup_status.setText(msg)
        self.status_lbl.setText(msg)
        self.setup_bar.hide()
        self._enable_task_buttons()
        self.reload()

    def _on_task_failed(self, msg: str) -> None:
        _log.error("task failed:\n%s", msg)
        self.setup_status.setText(msg.splitlines()[0])
        self.status_lbl.setText("Error \u2014 see console")
        self.setup_bar.hide()
        self._enable_task_buttons()
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Task failed", msg.splitlines()[0])

    def closeEvent(self, event):  # noqa: ANN001
        if self._stream_server is not None:
            try:
                self._stream_server.stop()
            except Exception:
                _log.exception("error stopping stream server on close")
            self._stream_server = None
        try:
            self.health.shutdown()
        except Exception:
            _log.exception("error stopping health check")
        try:
            self.player.video.shutdown()
        except Exception:
            _log.exception("error shutting down player")
        super().closeEvent(event)
