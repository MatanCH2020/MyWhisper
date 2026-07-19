"""Design system for the MyWhisper UI: light/dark palettes, spacing/radius
scales, a font picker, and a global Qt stylesheet (QSS) builder.

A palette is a flat dict of color tokens; build_qss() turns the active palette
into one app-wide stylesheet so every control looks consistent.
"""
from PySide6.QtGui import QFontDatabase

LIGHT = {
    "name": "light",
    "bg": "#EDF0F4",
    "surface": "#FFFFFF",
    "surface_alt": "#F3F5F9",
    "border": "#E2E6EC",
    "accent": "#2F6FE0",
    "accent_hover": "#2660C8",
    "on_accent": "#FFFFFF",
    "text": "#1E2430",
    "text_muted": "#6B7280",
    "danger": "#D14343",
    "danger_soft": "#F6E4E4",
    "unknown_fg": "#C0392B",
    "nav_bg": "#FFFFFF",
    "nav_sel": "#EAF1FE",
    "titlebar_bg": "#FFFFFF",
    "hover": "#EDF1F6",
    "scroll": "#C7CDD6",
    "shadow": "#33000000",
}

DARK = {
    "name": "dark",
    "bg": "#15171C",
    "surface": "#1E2128",
    "surface_alt": "#262A33",
    "border": "#30353F",
    "accent": "#4C82F7",
    "accent_hover": "#3D70E6",
    "on_accent": "#FFFFFF",
    "text": "#E6E8EC",
    "text_muted": "#9AA2AE",
    "danger": "#E5594F",
    "danger_soft": "#3A2526",
    "unknown_fg": "#FF7A70",
    "nav_bg": "#171A1F",
    "nav_sel": "#243049",
    "titlebar_bg": "#1A1D23",
    "hover": "#2A2F39",
    "scroll": "#3A404B",
    "shadow": "#66000000",
}

# spacing / radius scale
SP = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
RADIUS = 12
RADIUS_SM = 8


def palette(name: str) -> dict:
    return DARK if str(name).lower() == "dark" else LIGHT


def pick_font() -> str:
    fams = set(QFontDatabase.families())
    for name in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Inter"):
        if name in fams:
            return name
    return "Segoe UI"


def build_qss(p: dict) -> str:
    """App-wide stylesheet derived from the active palette."""
    return f"""
* {{
    font-family: "{pick_font()}";
    color: {p['text']};
}}
QToolTip {{
    background: {p['surface_alt']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 4px 8px;
}}

/* default = secondary button */
QPushButton {{
    background: {p['surface_alt']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: {RADIUS_SM}px;
    padding: 6px 14px;
    font-size: 13px;
}}
QPushButton:hover {{ background: {p['hover']}; }}
QPushButton:pressed {{ background: {p['border']}; }}

QPushButton[variant="primary"] {{
    background: {p['accent']};
    color: {p['on_accent']};
    border: none;
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {p['accent_hover']}; }}

QPushButton[variant="ghost"] {{
    background: transparent;
    border: none;
    color: {p['text_muted']};
}}
QPushButton[variant="ghost"]:hover {{ background: {p['hover']}; color: {p['text']}; }}

QPushButton[variant="danger"] {{
    background: transparent;
    border: none;
    color: {p['danger']};
}}
QPushButton[variant="danger"]:hover {{ background: {p['danger_soft']}; }}

QPushButton[variant="icon"] {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 4px;
}}
QPushButton[variant="icon"]:hover {{ background: {p['hover']}; }}

QLineEdit {{
    background: {p['surface_alt']};
    border: 1px solid {p['border']};
    border-radius: {RADIUS_SM}px;
    padding: 7px 12px;
    color: {p['text']};
    selection-background-color: {p['accent']};
    selection-color: {p['on_accent']};
}}
QLineEdit:focus {{ border: 1px solid {p['accent']}; }}

QComboBox {{
    background: {p['surface_alt']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: {RADIUS_SM}px;
    padding: 5px 10px;
    font-size: 13px;
}}
QComboBox:hover {{ border: 1px solid {p['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {p['text_muted']};
    margin-right: 8px;
}}
/* the popup list — without this it falls back to a black menu in light theme */
QComboBox QAbstractItemView {{
    background: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: {RADIUS_SM}px;
    padding: 4px;
    outline: none;
    selection-background-color: {p['accent']};
    selection-color: {p['on_accent']};
}}

QSlider::groove:horizontal {{
    height: 5px; border-radius: 3px; background: {p['border']};
}}
QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {p['accent']}; width: 16px; height: 16px;
    margin: -6px 0; border-radius: 8px;
}}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p['scroll']}; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['text_muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}

/* structural surfaces */
#container {{ background: {p['bg']}; border-radius: {RADIUS}px; }}
#titlebar {{ background: {p['titlebar_bg']};
    border-top-left-radius: {RADIUS}px; border-top-right-radius: {RADIUS}px; }}
#navrail {{ background: {p['nav_bg']}; }}
#card {{ background: {p['surface']}; border: 1px solid {p['border']};
    border-radius: {RADIUS}px; }}
#page {{ background: {p['surface']};
    border-top-left-radius: {RADIUS}px; }}

#navitem {{ text-align: left; background: transparent; border: none;
    border-radius: {RADIUS_SM}px; padding: 9px 12px; color: {p['text_muted']};
    font-size: 13px; }}
#navitem:hover {{ background: {p['hover']}; color: {p['text']}; }}
#navitem:checked {{ background: {p['nav_sel']}; color: {p['accent']}; font-weight: 600; }}

#sectiontitle {{ color: {p['text_muted']}; font-size: 12px; font-weight: 600; }}
#muted {{ color: {p['text_muted']}; }}
"""
