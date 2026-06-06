"""Stark-HUD dark theme (QSS) and shared palette constants."""
from __future__ import annotations

ACCENT = "#36e0c8"
ACCENT_DIM = "#1c8f80"
BG = "#070b11"
PANEL = "#0e1620"
PANEL_HI = "#15202e"
BORDER = "#1d2c3e"
TEXT = "#e8f1f5"
MUTED = "#7d93a6"
GOLD = "#ffd166"

QSS = f"""
* {{
    font-family: "Inter", "Segoe UI", "Helvetica Neue", sans-serif;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QWidget#root {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {BG}, stop:1 #0b1119);
}}
QFrame#panel, QScrollArea#guide {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
/* The scroll area's viewport + inner container default to the light palette
   base colour; force them dark so the magazine listing matches the panel. */
QScrollArea#guide > QWidget {{ background: transparent; }}
QWidget#guideBody {{ background: {PANEL}; }}
QLabel#brand {{
    font-size: 22px; font-weight: 800; letter-spacing: 3px; color: {TEXT};
}}
QLabel#brandAccent {{ color: {ACCENT}; }}
QLabel#clock {{
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
    font-size: 18px; color: {ACCENT}; letter-spacing: 1px;
}}
QLabel#tagline {{ color: {MUTED}; font-size: 11px; letter-spacing: 2px; }}

/* Channel buttons */
QPushButton#channel {{
    text-align: left; padding: 12px 14px; border-radius: 12px;
    background: {PANEL_HI}; border: 1px solid {BORDER};
    font-size: 14px; font-weight: 600;
}}
QPushButton#channel:hover {{ border: 1px solid {ACCENT_DIM}; }}
QPushButton#channel:checked {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 rgba(54,224,200,0.18), stop:1 rgba(54,224,200,0.04));
    border: 1px solid {ACCENT};
}}

QPushButton {{
    background: {PANEL_HI}; border: 1px solid {BORDER};
    border-radius: 10px; padding: 8px 14px; font-weight: 600;
}}
QPushButton:hover {{ border: 1px solid {ACCENT_DIM}; color: {ACCENT}; }}
QPushButton:pressed {{ background: #0a121b; }}
QPushButton:disabled {{ color: #44566a; }}
QPushButton#accent {{
    background: {ACCENT}; color: #04201c; border: none;
}}
QPushButton#accent:hover {{ background: #4cf0d8; }}
QPushButton[live="true"] {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #ff3b6b, stop:1 #ff5e8a); color: white; border: none;
}}

QPushButton#transport {{
    background: transparent; border: none; border-radius: 22px;
    padding: 0; font-size: 18px; min-width: 44px; min-height: 44px;
}}
QPushButton#transport:hover {{ background: {PANEL_HI}; color: {ACCENT}; }}
QPushButton#transportMain {{
    background: {ACCENT}; color: #04201c; border-radius: 28px;
    min-width: 56px; min-height: 56px; font-size: 22px;
}}
QPushButton#transportMain:hover {{ background: #4cf0d8; }}

/* Tabs (days) */
QPushButton#day {{
    background: transparent; border: none; border-radius: 9px;
    padding: 7px 14px; color: {MUTED}; font-weight: 600;
}}
QPushButton#day:hover {{ color: {TEXT}; }}
QPushButton#day:checked {{ background: {PANEL_HI}; color: {ACCENT}; }}

QSlider::groove:horizontal {{
    height: 5px; background: {BORDER}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
    background: {ACCENT};
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}

QProgressBar {{
    background: {PANEL_HI}; border: 1px solid {BORDER}; border-radius: 8px;
    height: 18px; text-align: center; color: {MUTED};
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {ACCENT_DIM}, stop:1 {ACCENT});
    border-radius: 7px;
}}
QComboBox {{
    background: {PANEL_HI}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 10px; color: {TEXT};
}}
QComboBox:hover {{ border: 1px solid {ACCENT_DIM}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DIM}; selection-color: {TEXT};
}}
QToolTip {{ background: {PANEL_HI}; color: {TEXT}; border: 1px solid {ACCENT_DIM}; }}

/* Dialogs & text inputs */
QDialog {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {BG}, stop:1 #0b1119);
}}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {PANEL_HI}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 10px;
    selection-background-color: {ACCENT_DIM}; selection-color: {TEXT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit::placeholder, QPlainTextEdit::placeholder {{ color: {MUTED}; }}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px; background: {PANEL}; border: none;
}}
QSpinBox::up-arrow {{ image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-bottom: 5px solid {MUTED}; }}
QSpinBox::down-arrow {{ image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid {MUTED}; }}

/* Tabs */
QTabWidget::pane {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px;
    top: -1px;
}}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {MUTED};
    padding: 8px 16px; margin-right: 4px; font-weight: 600;
    border-top-left-radius: 9px; border-top-right-radius: 9px;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ background: {PANEL_HI}; color: {ACCENT}; }}

QLabel {{ background: transparent; }}
"""
