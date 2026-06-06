"""Settings dialog: edit config.toml from the UI instead of by hand."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..schedule.dayparts import CHANNELS
from . import theme

_WEEKDAYS = ["today", "monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday"]


def _roots_edit(values: list[str]) -> QPlainTextEdit:
    box = QPlainTextEdit("\n".join(values))
    box.setPlaceholderText("One path per line")
    box.setFixedHeight(70)
    return box


def _parse_roots(box: QPlainTextEdit) -> list[str]:
    return [ln.strip() for ln in box.toPlainText().splitlines() if ln.strip()]


class SettingsDialog(QDialog):
    """Edits a Config in place and persists it to config.toml on save."""

    def __init__(self, cfg: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setObjectName("root")
        self.setWindowTitle("RetroGuide Settings")
        self.setMinimumWidth(580)

        tabs = QTabWidget()
        tabs.addTab(self._library_tab(), "Library")
        tabs.addTab(self._ai_tab(), "AI / Metadata")
        tabs.addTab(self._schedule_tab(), "Schedule")
        tabs.addTab(self._channels_tab(), "Channels")
        tabs.addTab(self._ui_tab(), "Interface")

        hint = QLabel("Library changes take effect after \u201cRefresh Library\u201d; "
                      "schedule and channel changes after \u201cRebuild\u201d.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme.MUTED}; font-size:11px;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        root.addWidget(tabs)
        root.addWidget(hint)
        root.addWidget(buttons)

    # -- tabs ---------------------------------------------------------------
    def _library_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        self.tv_roots = _roots_edit(self.cfg.library.tv_roots)
        self.movie_roots = _roots_edit(self.cfg.library.movie_roots)
        self.min_mb = QSpinBox()
        self.min_mb.setRange(0, 100000)
        self.min_mb.setValue(self.cfg.library.min_file_mb)
        self.min_mb.setSuffix(" MB")
        form.addRow("TV roots", self.tv_roots)
        form.addRow("Movie roots", self.movie_roots)
        form.addRow("Min file size", self.min_mb)
        return w

    def _ai_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        self.ollama_host = QLineEdit(self.cfg.ollama.host)
        self.ollama_model = QLineEdit(self.cfg.ollama.model)
        self.ollama_embed = QLineEdit(self.cfg.ollama.embed_model)
        self.ollama_timeout = QSpinBox()
        self.ollama_timeout.setRange(10, 1200)
        self.ollama_timeout.setValue(self.cfg.ollama.timeout)
        self.ollama_timeout.setSuffix(" s")
        self.tmdb_key = QLineEdit(self.cfg.tmdb.api_key)
        self.tmdb_key.setPlaceholderText("Optional \u2014 leave blank to use free TVmaze")
        form.addRow("Ollama host", self.ollama_host)
        form.addRow("Chat model", self.ollama_model)
        form.addRow("Embed model", self.ollama_embed)
        form.addRow("Timeout", self.ollama_timeout)
        form.addRow("TMDB API key", self.tmdb_key)
        return w

    def _schedule_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        self.start = QComboBox()
        self.start.addItems([s.capitalize() for s in _WEEKDAYS])
        cur = (self.cfg.schedule.start or "today").lower()
        self.start.setCurrentIndex(_WEEKDAYS.index(cur) if cur in _WEEKDAYS else 0)
        self.days = QSpinBox()
        self.days.setRange(1, 14)
        self.days.setValue(self.cfg.schedule.days)
        self.days.setSuffix(" days")
        self.day_start = QSpinBox()
        self.day_start.setRange(0, 23)
        self.day_start.setValue(self.cfg.schedule.day_start_hour)
        self.day_start.setSuffix(":00")
        self.events = QCheckBox("Seasonal & spontaneous special events")
        self.events.setChecked(self.cfg.schedule.events)
        form.addRow("Week starts", self.start)
        form.addRow("Length", self.days)
        form.addRow("Broadcast day begins", self.day_start)
        form.addRow("", self.events)
        evnote = QLabel("Holiday marathons and the odd weekend stunt (a May-4th "
                        "Star Wars bonanza, Christmas cinema\u2026) take over The "
                        "Movie Channel for the day.")
        evnote.setWordWrap(True)
        evnote.setStyleSheet(f"color:{theme.MUTED}; font-size:11px;")
        form.addRow(evnote)
        return w

    def _channels_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        intro = QLabel("Rename channels and give each an optional logo (a small "
                       "PNG works best). Leave a name blank to keep the default.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{theme.MUTED}; font-size:11px;")
        form.addRow(intro)
        self.channel_name_edits: dict[str, QLineEdit] = {}
        self.channel_logo_edits: dict[str, QLineEdit] = {}
        for ch in CHANNELS:
            name_edit = QLineEdit(self.cfg.channel_names.get(ch.slug, ""))
            name_edit.setPlaceholderText(ch.name)
            logo_edit = QLineEdit(self.cfg.channel_logos.get(ch.slug, ""))
            logo_edit.setPlaceholderText("No logo")
            browse = QPushButton("\u2026")
            browse.setFixedWidth(34)
            browse.clicked.connect(lambda _=False, e=logo_edit: self._pick_logo(e))
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(name_edit, 2)
            row.addWidget(logo_edit, 3)
            row.addWidget(browse)
            holder = QWidget()
            holder.setLayout(row)
            form.addRow(ch.name, holder)
            self.channel_name_edits[ch.slug] = name_edit
            self.channel_logo_edits[ch.slug] = logo_edit
        return w

    def _pick_logo(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a channel logo", edit.text() or "",
            "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if path:
            edit.setText(path)

    def _ui_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        self.default_view = QComboBox()
        self.default_view.addItems(["magazine", "grid"])
        idx = self.default_view.findText(self.cfg.ui.default_view)
        self.default_view.setCurrentIndex(max(0, idx))
        form.addRow("Default guide view", self.default_view)

        self.era = QComboBox()
        self.era.addItems(["70s", "80s", "90s", "00s"])
        eidx = self.era.findText(self.cfg.ui.era)
        self.era.setCurrentIndex(max(0, eidx))
        self.crt = QCheckBox("CRT scanlines / glass")
        self.crt.setChecked(self.cfg.ui.crt)
        self.bw = QCheckBox("Black & white picture (video only)")
        self.bw.setChecked(self.cfg.ui.bw)
        form.addRow("Broadcast era", self.era)
        form.addRow("", self.crt)
        form.addRow("", self.bw)
        note = QLabel("Era styling applies live in the LAN web view; the desktop "
                      "player honours the CRT and B&W toggles.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{theme.MUTED}; font-size:11px;")
        form.addRow(note)
        return w

    # -- persist ------------------------------------------------------------
    def _on_save(self) -> None:
        self.cfg.library.tv_roots = _parse_roots(self.tv_roots)
        self.cfg.library.movie_roots = _parse_roots(self.movie_roots)
        self.cfg.library.min_file_mb = self.min_mb.value()
        self.cfg.ollama.host = self.ollama_host.text().strip()
        self.cfg.ollama.model = self.ollama_model.text().strip()
        self.cfg.ollama.embed_model = self.ollama_embed.text().strip()
        self.cfg.ollama.timeout = self.ollama_timeout.value()
        self.cfg.tmdb.api_key = self.tmdb_key.text().strip()
        self.cfg.schedule.start = _WEEKDAYS[self.start.currentIndex()]
        self.cfg.schedule.days = self.days.value()
        self.cfg.schedule.day_start_hour = self.day_start.value()
        self.cfg.schedule.events = self.events.isChecked()
        self.cfg.ui.default_view = self.default_view.currentText()
        self.cfg.ui.era = self.era.currentText()
        self.cfg.ui.crt = self.crt.isChecked()
        self.cfg.ui.bw = self.bw.isChecked()
        self.cfg.channel_names = {
            slug: e.text().strip()
            for slug, e in self.channel_name_edits.items() if e.text().strip()
        }
        self.cfg.channel_logos = {
            slug: e.text().strip()
            for slug, e in self.channel_logo_edits.items() if e.text().strip()
        }
        self.cfg.save()
        self.accept()
