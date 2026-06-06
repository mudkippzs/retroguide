"""GUI entry point for RetroGuide."""
from __future__ import annotations

import os
import sys


def main() -> int:
    # mpv embeds most reliably into an X11 (XWayland) surface.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    # Strip Cursor/AppImage vars that confuse interpreter resolution.
    for var in ("APPIMAGE", "APPDIR", "ARGV0"):
        os.environ.pop(var, None)

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from .config import Config
    from .logsetup import setup_logging
    from .ui.mainwindow import MainWindow
    from .ui.theme import QSS

    log = setup_logging("gui")
    cfg = Config.load()
    log.info("RetroGuide starting (Qt platform=%s)", os.environ.get("QT_QPA_PLATFORM"))
    app = QApplication(sys.argv)
    app.setApplicationName("RetroGuide")
    app.setApplicationDisplayName("RetroGuide")
    app.setFont(QFont("Inter", 10))
    app.setStyleSheet(QSS)

    win = MainWindow(cfg)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
