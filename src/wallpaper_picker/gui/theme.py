"""Dark theme: palette + stylesheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#141519"
SURFACE = "#1c1d23"
SURFACE_2 = "#24262d"
SURFACE_3 = "#2e3039"
BORDER = "#34363f"
TEXT = "#e9e9ee"
MUTED = "#9b9da8"
FAINT = "#6c6e79"
ACCENT = "#5aa2f0"
ACCENT_HOVER = "#74b3f5"
ACCENT_TEXT = "#0b1522"
DANGER = "#ef6b6b"
OK = "#4fc98a"
WARN = "#f0b35a"

TYPE_COLORS = {
    "video": "#3d8bfd",
    "scene": "#a26bf5",
    "web": "#2fb67c",
    "preset": "#e39b34",
    "application": "#6c6e79",
    "unknown": "#6c6e79",
}


def type_color(t: str) -> QColor:
    return QColor(TYPE_COLORS.get(t, TYPE_COLORS["unknown"]))


STYLE = f"""
* {{ outline: 0; }}
QWidget {{ color: {TEXT}; font-size: 10pt; }}
QMainWindow, #Root {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; border-radius: 6px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 3px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: #454854; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}
QScrollBar:horizontal {{ height: 0; }}

#Panel {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
#Strip {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
#Card {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; }}
#Card[current="true"] {{ border: 1px solid {ACCENT}; }}
#Section {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600; letter-spacing: 0.6px; }}
#Title {{ font-size: 14pt; font-weight: 600; }}
#AppTitle {{ font-size: 12.5pt; font-weight: 700; }}
#Muted {{ color: {MUTED}; }}
#Faint {{ color: {FAINT}; font-size: 9pt; }}
#Error {{ background: #3a1f24; color: #ffc9c9; border: 1px solid #6b2b33; border-radius: 8px; padding: 8px; }}
#Success {{ background: #173126; color: #bff0d5; border: 1px solid #25573f; border-radius: 8px; padding: 8px; }}
#Info {{ background: {SURFACE_2}; color: {MUTED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px; }}
#GroupLabel {{ color: {TEXT}; font-weight: 600; padding-top: 8px; }}
#PropLabel[modified="true"] {{ color: {ACCENT}; }}

QLineEdit {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px;
             selection-background-color: {ACCENT}; selection-color: {ACCENT_TEXT}; }}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
#Search {{ min-width: 260px; }}

QPushButton {{ background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 14px; }}
QPushButton:hover {{ background: #383a45; }}
QPushButton:pressed {{ background: #2a2c34; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE_2}; }}
QPushButton#Primary {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; font-weight: 600; padding: 8px 18px; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: #34506f; color: #8aa3bf; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {MUTED}; padding: 4px 8px; }}
QPushButton#Ghost:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton#Danger {{ background: transparent; border: 1px solid {BORDER}; color: {MUTED}; padding: 3px 10px; }}
QPushButton#Danger:hover {{ border-color: {DANGER}; color: {DANGER}; }}

QPushButton#Chip {{ background: transparent; border: 1px solid {BORDER}; border-radius: 14px; padding: 4px 14px; color: {MUTED}; }}
QPushButton#Chip:hover {{ color: {TEXT}; border-color: #4a4d58; }}
QPushButton#Chip:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: {ACCENT_TEXT}; font-weight: 600; }}

QPushButton#Seg {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 0; padding: 6px 10px; color: {MUTED}; }}
QPushButton#Seg[first="true"] {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
QPushButton#Seg[last="true"] {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
QPushButton#Seg:hover {{ color: {TEXT}; }}
QPushButton#Seg:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: {ACCENT_TEXT}; font-weight: 600; }}
QPushButton#Seg:disabled {{ color: {FAINT}; }}

QComboBox {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 10px; min-height: 18px; }}
QComboBox::drop-down {{ border: none; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url(__ARROW__); width: 10px; height: 10px; }}
QComboBox:hover {{ border-color: #4a4d58; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
                               selection-color: {ACCENT_TEXT}; padding: 4px; outline: 0; }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}

QDoubleSpinBox, QSpinBox {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 6px; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; }}

QSplitter::handle {{ background: {BORDER}; }}
"""


def _arrow_file() -> str:
    """A small chevron SVG for combo boxes (QSS needs a file)."""
    from .. import paths

    p = paths.cache_dir() / "ui" / "chevron-down.svg"
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">
<path d="M2 3.5 L5 6.5 L8 3.5" fill="none" stroke="{MUTED}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>''')
    return str(p)


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.Window, c(BG))
    pal.setColor(QPalette.WindowText, c(TEXT))
    pal.setColor(QPalette.Base, c(SURFACE_2))
    pal.setColor(QPalette.AlternateBase, c(SURFACE))
    pal.setColor(QPalette.ToolTipBase, c(SURFACE_3))
    pal.setColor(QPalette.ToolTipText, c(TEXT))
    pal.setColor(QPalette.Text, c(TEXT))
    pal.setColor(QPalette.Button, c(SURFACE_3))
    pal.setColor(QPalette.ButtonText, c(TEXT))
    pal.setColor(QPalette.Highlight, c(ACCENT))
    pal.setColor(QPalette.HighlightedText, c(ACCENT_TEXT))
    pal.setColor(QPalette.PlaceholderText, c(FAINT))
    pal.setColor(QPalette.Link, c(ACCENT))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c(FAINT))
    app.setPalette(pal)
    app.setStyleSheet(STYLE.replace("__ARROW__", _arrow_file()))
