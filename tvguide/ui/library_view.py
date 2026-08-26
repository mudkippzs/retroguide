"""Binge-mode library browser and queue panel.

Left: the whole playable catalog as a tree (Films flat; Series -> seasons ->
episodes, loaded lazily). Right: the live queue with reorder, shuffle,
theme-random, save/load and play. All styling comes from ui.theme tokens.
"""
from __future__ import annotations

import json
import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from ..binge import playlist
from ..schedule import rules as sched_rules
from ..util.media_ok import PLAYABLE_SQL
from .widgets import fmt_duration

_ROLE = Qt.ItemDataRole.UserRole


def _fmt_hours(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


class _ThemeRandomDialog(QDialog):
    """Pick a tag/bucket/kind/count and append random matches to the queue."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Theme Random")
        form = QFormLayout(self)

        self.tag = QComboBox()
        self.tag.addItem("Any tag", None)
        tags: set[str] = set()
        for r in conn.execute("SELECT tags FROM titles WHERE tags IS NOT NULL"):
            tags.update(json.loads(r["tags"] or "[]"))
        for t in sorted(tags):
            self.tag.addItem(t, t)
        self.tag.setToolTip("Only pick titles carrying this enrichment tag "
                            "(horror, christmas, family…)")

        self.bucket = QComboBox()
        self.bucket.addItem("Any bucket", None)
        for r in conn.execute(
                "SELECT DISTINCT bucket FROM titles WHERE bucket IS NOT NULL "
                "ORDER BY bucket"):
            self.bucket.addItem(r["bucket"], r["bucket"])
        self.bucket.setToolTip("Only pick titles from this scheduling bucket "
                               "(sitcom, drama, anime…)")

        self.kind = QComboBox()
        self.kind.addItem("Films", "film")
        self.kind.addItem("Episodes", "episode")
        self.kind.setToolTip("Films adds whole movies; Episodes adds one "
                             "random episode per picked series")

        self.count = QSpinBox()
        self.count.setRange(1, 50)
        self.count.setValue(5)
        self.count.setToolTip("How many picks to add to the queue")

        form.addRow("Tag", self.tag)
        form.addRow("Bucket", self.bucket)
        form.addRow("Pick", self.kind)
        form.addRow("Count", self.count)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def run(self) -> int:
        return playlist.theme_random(
            self.conn, tag=self.tag.currentData(),
            bucket=self.bucket.currentData(), kind=self.kind.currentData(),
            n=self.count.value())


class LibraryPanel(QFrame):
    """Catalog browser + binge queue. Emits playRequested when the user
    wants the queue on the screen."""
    playRequested = Signal()
    queueChanged = Signal()
    ruleChanged = Signal(str)   # human-readable note for the status line

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self.setObjectName("panel")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._build_browser())
        split.addWidget(self._build_queue())
        split.setSizes([520, 420])
        root.addWidget(split, 1)

        self.reload()

    # ------------------------------------------------------------------
    # Browser (left)
    # ------------------------------------------------------------------
    def _build_browser(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("LIBRARY")
        title.setStyleSheet(
            f"color:{theme.TEXT}; font-size:15px; font-weight:800; "
            "letter-spacing:1px;")
        head.addWidget(title)
        head.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter titles…")
        self.search.setToolTip("Filter the tree by name — matches films, "
                               "series and episodes")
        self.search.textChanged.connect(self._apply_filter)
        head.addWidget(self.search, 1)
        v.addLayout(head)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setToolTip("Double-click or press Enter to add to the queue")
        self.tree.itemExpanded.connect(self._lazy_load)
        # itemActivated covers both double-click and Enter, once per gesture.
        self.tree.itemActivated.connect(lambda it, _c: self._add_node(it))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._browser_menu)
        v.addWidget(self.tree, 1)
        return box

    def _build_queue(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        head = QHBoxLayout()
        self.queue_head = QLabel("QUEUE")
        self.queue_head.setStyleSheet(
            f"color:{theme.TEXT}; font-size:15px; font-weight:800; "
            "letter-spacing:1px;")
        head.addWidget(self.queue_head)
        head.addStretch()
        self.repeat_btn = QPushButton("↻")
        self.repeat_btn.setCheckable(True)
        self.repeat_btn.setToolTip("Repeat — when the queue finishes, "
                                   "start it again")
        self.random_btn = QPushButton("🎲")
        self.random_btn.setCheckable(True)
        self.random_btn.setToolTip("Random order — jump to a random "
                                   "unplayed item instead of the next one")
        head.addWidget(self.repeat_btn)
        head.addWidget(self.random_btn)
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setObjectName("accent")
        self.play_btn.setToolTip("Play the queue from the top in the player")
        self.play_btn.clicked.connect(self.playRequested)
        head.addWidget(self.play_btn)
        v.addLayout(head)

        self.queue_list = QListWidget()
        self.queue_list.setToolTip("What will play, in order. Series and "
                                   "seasons stay as blocks and expand at "
                                   "play time.")
        self.queue_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self._queue_menu)
        v.addWidget(self.queue_list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        for text, tip, fn in (
            ("▲", "Move up", lambda: self._move(-1)),
            ("▼", "Move down", lambda: self._move(1)),
            ("✕", "Remove from queue", self._remove),
            ("\U0001f500", "Shuffle the queue (series/season blocks stay in "
             "episode order)", self._shuffle),
            ("✨", "Theme random — add random picks by tag/bucket",
             self._theme_random),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        save_b = QPushButton("Save…")
        save_b.setToolTip("Save the queue as a named playlist")
        save_b.clicked.connect(self._save)
        load_b = QPushButton("Load…")
        load_b.setToolTip("Replace the queue with a saved playlist")
        load_b.clicked.connect(self._load)
        clear_b = QPushButton("Clear")
        clear_b.setToolTip("Empty the queue")
        clear_b.clicked.connect(self._clear)
        bar.addWidget(save_b)
        bar.addWidget(load_b)
        bar.addWidget(clear_b)
        v.addLayout(bar)
        return box

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._populate_tree()
        self.refresh_queue()

    def _populate_tree(self) -> None:
        self.tree.clear()
        films = QTreeWidgetItem(["Films"])
        films.setData(0, _ROLE, ("root", None, None, None))
        series = QTreeWidgetItem(["Series"])
        series.setData(0, _ROLE, ("root", None, None, None))
        self.tree.addTopLevelItem(films)
        self.tree.addTopLevelItem(series)

        for r in self.conn.execute(
                "SELECT t.id, t.name, t.year, m.duration_sec "
                "FROM titles t JOIN media_files m ON m.id = t.movie_file_id "
                f"WHERE t.kind='movie' AND {PLAYABLE_SQL} "
                "ORDER BY t.sort_name"):
            label = f"{r['name']}" + (f" ({r['year']})" if r["year"] else "")
            if r["duration_sec"]:
                label += f"  ·  {fmt_duration(r['duration_sec'])}"
            node = QTreeWidgetItem([label])
            node.setData(0, _ROLE, ("film", r["id"], None, None))
            node.setToolTip(0, "Double-click to add this film to the queue")
            films.addChild(node)

        for r in self.conn.execute(
                "SELECT t.id, t.name, COUNT(e.id) AS n "
                "FROM titles t "
                "JOIN episodes e ON e.title_id = t.id "
                "JOIN media_files m ON m.id = e.file_id "
                f"WHERE t.kind='series' AND {PLAYABLE_SQL} "
                "GROUP BY t.id HAVING n > 0 ORDER BY t.sort_name"):
            node = QTreeWidgetItem([f"{r['name']}  ·  {r['n']} eps"])
            node.setData(0, _ROLE, ("series", r["id"], None, None))
            node.setToolTip(0, "Double-click to add the whole series; "
                               "expand for seasons and episodes")
            node.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            node.addChild(QTreeWidgetItem(["…"]))  # lazy placeholder
            series.addChild(node)

        films.setExpanded(True)
        series.setExpanded(True)
        self._apply_filter(self.search.text())

    def _lazy_load(self, node: QTreeWidgetItem) -> None:
        kind, title_id, _season, _ep = node.data(0, _ROLE)
        if kind != "series":
            return
        if node.childCount() == 1 and node.child(0).text(0) == "…":
            node.takeChild(0)
        else:
            return
        seasons: dict[int, QTreeWidgetItem] = {}
        for r in self.conn.execute(
                "SELECT e.id, e.season, e.episode, e.name, m.duration_sec "
                "FROM episodes e JOIN media_files m ON m.id = e.file_id "
                f"WHERE e.title_id=? AND {PLAYABLE_SQL} "
                "ORDER BY e.season, e.episode", (title_id,)):
            s = r["season"]
            if s not in seasons:
                label = ("Specials" if s == 0 else f"Season {s}")
                snode = QTreeWidgetItem([label])
                snode.setData(0, _ROLE, ("season", title_id, s, None))
                snode.setToolTip(0, "Double-click to add this season")
                seasons[s] = snode
                node.addChild(snode)
            ep_label = f"E{r['episode']:02d}"
            if r["name"]:
                ep_label += f"  {r['name']}"
            if r["duration_sec"]:
                ep_label += f"  ·  {fmt_duration(r['duration_sec'])}"
            enode = QTreeWidgetItem([ep_label])
            enode.setData(0, _ROLE, ("episode", title_id, s, r["id"]))
            enode.setToolTip(0, "Double-click to add this episode")
            seasons[s].addChild(enode)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            any_visible = False
            for j in range(root.childCount()):
                child = root.child(j)
                hit = not needle or needle in child.text(0).lower()
                child.setHidden(not hit)
                any_visible = any_visible or hit
            root.setHidden(bool(needle) and not any_visible)

    # ------------------------------------------------------------------
    # Queue (right)
    # ------------------------------------------------------------------
    def refresh_queue(self) -> None:
        self.queue_list.clear()
        its = playlist.items(self.conn)
        total = 0.0
        for idx, it in enumerate(its, start=1):
            n, secs = playlist.item_stats(self.conn, it)
            total += secs
            if n == 0:
                label = f"{idx}. {it.label}  ·  unavailable"
            elif it.kind in ("series", "season"):
                label = f"{idx}. {it.label}  ·  {n} eps · {_fmt_hours(secs)}"
            else:
                label = f"{idx}. {it.label}  ·  {_fmt_hours(secs)}"
            row = QListWidgetItem(label)
            row.setData(_ROLE, it.id)
            row.setToolTip(f"{n} playable file(s), {_fmt_hours(secs)} total")
            self.queue_list.addItem(row)
        if its:
            self.queue_head.setText(
                f"QUEUE  ·  {len(its)} items · {_fmt_hours(total)}")
        else:
            self.queue_head.setText("QUEUE  ·  empty")
            row = QListWidgetItem("Queue is empty — add something from "
                                  "the library")
            row.setFlags(Qt.ItemFlag.NoItemFlags)
            self.queue_list.addItem(row)
        self.play_btn.setEnabled(bool(its))
        self.queueChanged.emit()

    def _selected_item_id(self) -> int | None:
        row = self.queue_list.currentItem()
        if row is None:
            return None
        return row.data(_ROLE)

    def _add_node(self, node: QTreeWidgetItem) -> None:
        kind, title_id, season, episode_id = node.data(0, _ROLE)
        if kind in (None, "root") or title_id is None:
            return
        playlist.add(self.conn, kind, title_id, season=season,
                     episode_id=episode_id)
        self.refresh_queue()

    def _move(self, delta: int) -> None:
        iid = self._selected_item_id()
        if iid is not None:
            playlist.move(self.conn, iid, delta)
            self.refresh_queue()

    def _remove(self) -> None:
        iid = self._selected_item_id()
        if iid is not None:
            playlist.remove(self.conn, iid)
            self.refresh_queue()

    def _shuffle(self) -> None:
        playlist.shuffle(self.conn)
        self.refresh_queue()

    def _clear(self) -> None:
        playlist.clear(self.conn)
        self.refresh_queue()

    def _theme_random(self) -> None:
        dlg = _ThemeRandomDialog(self.conn, self)
        if dlg.exec():
            dlg.run()
            self.refresh_queue()

    def _save(self) -> None:
        name, ok = QInputDialog.getText(self, "Save playlist",
                                        "Name for this playlist:")
        if ok and name.strip():
            playlist.save_as(self.conn, name)

    def _load(self) -> None:
        saved = playlist.list_saved(self.conn)
        if not saved:
            return
        names = [f"{n}  ({c} items)" for n, c, _ in saved]
        pick, ok = QInputDialog.getItem(self, "Load playlist", "Playlist:",
                                        names, 0, False)
        if ok:
            playlist.load(self.conn, saved[names.index(pick)][0])
            self.refresh_queue()

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------
    def _browser_menu(self, pos) -> None:  # noqa: ANN001
        node = self.tree.itemAt(pos)
        if node is None:
            return
        kind, title_id, season, _e = node.data(0, _ROLE)
        if kind in (None, "root") or title_id is None:
            return
        menu = QMenu(self)
        add_act = menu.addAction("Add to queue")
        rule_acts: dict[object, tuple] = {}
        if kind == "season" and season and season > 0:
            menu.addSeparator()
            a = menu.addAction(f"Start rotation at Season {season}")
            a.setToolTip("The TV schedule will never rotate earlier than "
                         "this season (applies on next Rebuild)")
            rule_acts[a] = ("start", season)
        if kind in ("series", "season"):
            if sched_rules.get_start_season(self.conn, title_id) is not None:
                a = menu.addAction("Clear rotation start")
                rule_acts[a] = ("start", None)
            menu.addSeparator()
            rule_acts.update(self._channel_rule_menus(menu, title_id))
        picked = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if picked == add_act:
            self._add_node(node)
        elif picked in rule_acts:
            self._apply_rule(title_id, *rule_acts[picked])

    def _channel_rule_menus(self, menu: QMenu, title_id: int) -> dict:
        channels = self.conn.execute(
            "SELECT slug, name FROM channels ORDER BY position").fetchall()
        if not channels:
            return {}
        current = sched_rules.rules_for_title(self.conn, title_id)
        acts: dict[object, tuple] = {}
        pin = menu.addMenu("Pin to channel…")
        block = menu.addMenu("Block from channel…")
        for ch in channels:
            mark = " ✓" if current.get(ch["slug"]) == "allow" else ""
            a = pin.addAction(ch["name"] + mark)
            a.setToolTip("This show airs ONLY on this channel, and the "
                         "channel's series slots air only pinned shows")
            acts[a] = ("channel", ch["slug"], "allow")
            mark = " ✓" if current.get(ch["slug"]) == "block" else ""
            a = block.addAction(ch["name"] + mark)
            a.setToolTip("Never air this show on this channel")
            acts[a] = ("channel", ch["slug"], "block")
        if current:
            clear = menu.addMenu("Clear channel rule…")
            for ch in channels:
                if ch["slug"] in current:
                    a = clear.addAction(ch["name"])
                    acts[a] = ("channel", ch["slug"], None)
        return acts

    def _apply_rule(self, title_id: int, scope: str, *args) -> None:
        name = self.conn.execute(
            "SELECT name FROM titles WHERE id=?", (title_id,)).fetchone()["name"]
        if scope == "start":
            season = args[0]
            sched_rules.set_start_season(self.conn, title_id, season)
            note = (f"{name}: rotation starts at Season {season}"
                    if season is not None else f"{name}: rotation start cleared")
        else:
            slug, mode = args
            sched_rules.set_channel_rule(self.conn, title_id, slug, mode)
            ch = self.conn.execute(
                "SELECT name FROM channels WHERE slug=?", (slug,)).fetchone()
            ch_name = ch["name"] if ch else slug
            note = {"allow": f"{name}: pinned to {ch_name}",
                    "block": f"{name}: blocked from {ch_name}",
                    None: f"{name}: rule on {ch_name} cleared"}[mode]
        self.ruleChanged.emit(note + " — applies on next Rebuild")

    def _queue_menu(self, pos) -> None:  # noqa: ANN001
        row = self.queue_list.itemAt(pos)
        if row is None or row.data(_ROLE) is None:
            return
        menu = QMenu(self)
        up = menu.addAction("Move up")
        down = menu.addAction("Move down")
        rm = menu.addAction("Remove")
        picked = menu.exec(self.queue_list.viewport().mapToGlobal(pos))
        if picked == up:
            self._move(-1)
        elif picked == down:
            self._move(1)
        elif picked == rm:
            self._remove()
