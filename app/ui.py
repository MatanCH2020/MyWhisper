"""MyWhisper UI (Qt / PySide6) — professional themed shell.

A frameless branded window with a side nav rail and three pages (history,
dictionary, settings), light/dark themes, search, per-item actions and native
RTL rendering. Everything runs on the Qt main thread; worker threads talk to the
UI only through AppUI's thread-safe signals.
"""
import html
import re

from version import __version__ as APP_VERSION

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizeGrip, QStackedWidget, QVBoxLayout, QWidget

import icons
import theme
from widgets import FramelessWindow, NavRail, TitleBar

# Cards rendered per page. Qt re-lays out the whole scroll area on every insert,
# so the cost of a refresh grows with the number of cards on screen, not with the
# size of history.json — 100 cards cost ~490ms per rebuild, 25 cost ~100ms.
# "הצג עוד" adds another page.
HISTORY_PAGE = 25
MAX_HISTORY_CARDS = HISTORY_PAGE  # first page; grows via _page_limit

from overlay import Overlay  # re-export for existing UI callers


class CorrectionDialog(QDialog):
    def __init__(self, parent, palette, word, on_save, on_approve,
                 suggestions=None):
        super().__init__(parent)
        self.setWindowTitle("תיקון מילה")
        theme.bind_style(self, lambda _theme_palette: f"QDialog{{background:{_theme_palette['bg']};}}", palette)
        self.resize(380, 260)
        self._word, self._on_save, self._on_approve = word, on_save, on_approve
        p = palette
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        t = QLabel("תיקון מילה")
        t.setFont(QFont(theme.pick_font(), 15, QFont.Bold))
        lay.addWidget(t)
        sub = QLabel(f"המילה שזוהתה: {word}")
        sub.setObjectName("muted")
        lay.addWidget(sub)
        hint = QLabel("מילה לועזית? כתוב אותה באנגלית (thumbnail, render).")
        hint.setObjectName("hint")
        lay.addWidget(hint)
        # --- Suggestion chips from Hebrew dictionary ---
        if suggestions:
            sug_label = QLabel("הצעות מהמילון:")
            theme.bind_style(sug_label, lambda _theme_palette: f"color:{_theme_palette['text_muted']}; font-size:12px; margin-top:4px;", p)
            lay.addWidget(sug_label)
            chips_row = QHBoxLayout()
            chips_row.setContentsMargins(0, 0, 0, 0)
            chips_row.setSpacing(6)
            for sug in suggestions:
                chip = QPushButton(sug)
                chip.setCursor(Qt.PointingHandCursor)
                theme.bind_style(chip, lambda _theme_palette: f"QPushButton{{"
                    f"  background:{_theme_palette['surface']};"
                    f"  color:{_theme_palette['accent']};"
                    f"  border:1px solid {_theme_palette['accent']};"
                    f"  border-radius:12px;"
                    f"  padding:3px 12px;"
                    f"  font-size:13px;"
                    f"}}"
                    f"QPushButton:hover{{"
                    f"  background:{_theme_palette['accent']};"
                    f"  color:{_theme_palette['on_accent']};"
                    f"}}", p)
                chip.clicked.connect(lambda _, s=sug: self._use_suggestion(s))
                chips_row.addWidget(chip)
            chips_row.addStretch(1)
            lay.addLayout(chips_row)
        self.edit = QLineEdit(word)
        self.edit.selectAll()
        self.edit.returnPressed.connect(self._save)
        lay.addWidget(self.edit)
        lay.addStretch(1)
        row = QHBoxLayout()
        save = QPushButton("שמור תיקון")
        save.setProperty("variant", "primary")
        save.clicked.connect(self._save)
        approve = QPushButton("המילה תקינה")
        approve.clicked.connect(self._approve)
        cancel = QPushButton("ביטול")
        cancel.setProperty("variant", "ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(save)
        row.addWidget(approve)
        row.addStretch(1)
        row.addWidget(cancel)
        lay.addLayout(row)
        self.edit.setFocus()

    def _save(self):
        new = self.edit.text().strip()
        if new and new != self._word:
            if self._on_save(self._word, new) is False:
                return
        self.accept()

    def _approve(self):
        if self._on_approve(self._word) is False:
            return
        self.accept()

    def _use_suggestion(self, text):
        """Fill the correction field with a dictionary suggestion."""
        self.edit.setText(text)
        self.edit.selectAll()
        self.edit.setFocus()


class ChangelogDialog(QDialog):
    """A styled, card-per-version 'What's new' screen. Parses CHANGELOG.md into
    version entries and renders each as a themed card, highlighting the version
    the user currently has installed."""

    def __init__(self, parent, palette):
        super().__init__(parent)
        self.p = palette
        self.setWindowTitle("מה חדש ב-MyWhisper")
        theme.bind_style(self, lambda _theme_palette: f"QDialog{{background:{_theme_palette['bg']};}}", palette)
        self.resize(620, 560)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # --- header ---
        header = QWidget()
        header.setStyleSheet("background:transparent;")
        hl = QVBoxLayout(header)
        hl.setContentsMargins(24, 22, 24, 14)
        hl.setSpacing(5)
        title = QLabel("מה חדש ב-MyWhisper")
        title.setFont(QFont(theme.pick_font(), 18, QFont.Bold))
        theme.bind_style(title, lambda _theme_palette: f"color:{_theme_palette['text']};background:transparent;", palette)
        hl.addWidget(title)
        intro = self._read_intro()
        if intro:
            sub = QLabel(intro)
            sub.setWordWrap(True)
            theme.bind_style(sub, lambda _theme_palette: f"color:{_theme_palette['text_muted']};font-size:13px;background:transparent;", palette)
            hl.addWidget(sub)
        lay.addWidget(header)

        # --- divider ---
        divider = QFrame()
        divider.setFixedHeight(1)
        theme.bind_style(divider, lambda _theme_palette: f"background:{_theme_palette['border']};border:none;", palette)
        lay.addWidget(divider)

        # --- scrollable list of version cards ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        self.vbox = QVBoxLayout(inner)
        self.vbox.setContentsMargins(24, 18, 18, 6)
        self.vbox.setSpacing(14)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        # --- footer ---
        footer = QWidget()
        footer.setStyleSheet("background:transparent;")
        row = QHBoxLayout(footer)
        row.setContentsMargins(24, 12, 24, 16)
        row.addStretch(1)
        close_btn = QPushButton("סגור")
        close_btn.setProperty("variant", "primary")
        close_btn.setStyleSheet(_primary_btn_qss(palette))
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        lay.addWidget(footer)

        self._build_cards()

    # ---- CHANGELOG.md parsing ----
    def _read_lines(self):
        try:
            from pathlib import Path
            p = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
            if p.exists():
                return p.read_text(encoding="utf-8").splitlines()
        except Exception:
            pass
        return []

    def _read_intro(self):
        intro = []
        for ln in self._read_lines():
            s = ln.strip()
            if s.startswith("## "):
                break
            if s.startswith("#") or not s:
                continue
            intro.append(s)
        return " ".join(intro)

    def _parse_versions(self):
        versions, cur = [], None
        for ln in self._read_lines():
            s = ln.strip()
            if s.startswith("## "):
                head = s[3:].strip()
                m = re.search(r"(\d+\.\d+\.\d+)", head)
                cur = {"head": head, "ver": m.group(1) if m else None, "items": []}
                versions.append(cur)
            elif s.startswith("- ") and cur is not None:
                body = s[2:].strip()
                bm = re.match(r"\*\*(.+?)\*\*\s*:?\s*(.*)", body)
                if bm:
                    cur["items"].append((bm.group(1).strip(), bm.group(2).strip()))
                else:
                    cur["items"].append(("", body))
        return versions

    # ---- rendering ----
    def _build_cards(self):
        p = self.p
        green = "#35C46A" if p.get("name") == "dark" else "#2E9E54"
        current = str(APP_VERSION)
        for v in self._parse_versions():
            is_current = v["ver"] == current

            card = QFrame()
            card.setObjectName("clcard")
            if is_current:
                bg = _blend(p["accent"], p["surface"], 0.10)
                theme.bind_style(card, lambda _theme_palette: f"#clcard{{background:{bg};border:2px solid {_theme_palette['accent']};"
                    f"border-radius:14px;}}", p)
            else:
                theme.bind_style(card, lambda _theme_palette: f"#clcard{{background:{_theme_palette['surface']};"
                    f"border:1px solid {_theme_palette['border']};border-radius:14px;}}", p)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(18, 15, 18, 16)
            cl.setSpacing(11)

            hr = QHBoxLayout()
            hr.setSpacing(8)
            badge = QLabel(("v" + v["ver"]) if v["ver"] else v["head"])
            theme.bind_style(badge, lambda _theme_palette: f"background:{_theme_palette['accent'] if is_current else _theme_palette['surface_alt']};"
                f"color:{_theme_palette['on_accent'] if is_current else _theme_palette['text']};"
                f"border-radius:8px;padding:3px 11px;font-weight:800;font-size:13px;", p)
            hr.addWidget(badge)
            if is_current:
                pill = QLabel("✓ הגרסה שלך")
                pill.setStyleSheet(
                    f"background:{green};color:#06210F;border-radius:9px;"
                    f"padding:3px 11px;font-weight:700;font-size:12px;")
                hr.addWidget(pill)
            hr.addStretch(1)
            cl.addLayout(hr)

            for title, body in v["items"]:
                cl.addWidget(self._item_label(title, body))

            self.vbox.addWidget(card)
        self.vbox.addStretch(1)

    def _item_label(self, title, body):
        """One changelog bullet as a single wrapped rich-text label with an
        inline accent dot — no separate dot widget, so nothing drifts or picks
        up stray borders from the global stylesheet."""
        p = self.p
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.RichText)
        lbl.setStyleSheet("background:transparent;border:none;font-size:13px;")
        bullet = f"<span style='color:{p['accent']};font-weight:700;'>●</span>&nbsp;&nbsp;"
        if title:
            lbl.setText(
                f"{bullet}<span style='font-weight:700;color:{p['text']};'>"
                f"{html.escape(title)}</span>"
                f"<span style='color:{p['text_muted']};'> — {html.escape(body)}</span>")
        else:
            lbl.setText(
                f"{bullet}<span style='color:{p['text_muted']};'>"
                f"{html.escape(body)}</span>")
        return lbl


def _set_role(w, role):
    """Switch a widget between QSS roles (see build_qss: #hint, #statusok, …).

    Clears any inline stylesheet first — an inline rule outranks the global
    sheet, so without this a widget that was ever styled directly would ignore
    every later role change.
    """
    w.setStyleSheet("")
    w.setObjectName(role)
    w.style().unpolish(w)
    w.style().polish(w)


def _blend(fg, bg, t):
    """Blend hex color *fg* over *bg* by factor t in [0,1]; returns '#rrggbb'.
    Used for a subtle accent tint behind the current-version card."""
    def rgb(c):
        c = c.lstrip("#")
        return [int(c[i:i + 2], 16) for i in (0, 2, 4)]
    f, b = rgb(fg), rgb(bg)
    return "#%02x%02x%02x" % tuple(round(b[i] + (f[i] - b[i]) * t) for i in range(3))


def _version_gt(a, b):
    """True if version string a is newer than b (numeric dotted compare)."""
    def parts(v):
        return [int(x) for x in str(v).split(".") if x.isdigit()]
    try:
        return parts(a) > parts(b)
    except Exception:
        return False


def _combo_qss(p):
    """Inline style for a QComboBox incl. its popup list — set on the widget so
    the drop-down never falls back to a black menu in light theme (the global
    QComboBox QAbstractItemView selector isn't reliably applied on this build)."""
    return (
        f"QComboBox{{background:{p['surface_alt']};color:{p['text']};"
        f"border:1px solid {p['border']};border-radius:8px;padding:5px 10px;font-size:13px;}}"
        f"QComboBox:hover{{border:1px solid {p['accent']};}}"
        f"QComboBox:focus{{border:1px solid {p['accent']};}}"
        f"QComboBox::drop-down{{border:none;width:22px;}}"
        f"QComboBox QAbstractItemView{{background:{p['surface']};color:{p['text']};"
        f"border:1px solid {p['border']};outline:none;padding:4px;"
        f"selection-background-color:{p['accent']};selection-color:{p['on_accent']};}}")


def _primary_btn_qss(p):
    """Inline primary-button style. Set directly on the widget so it never
    depends on the global [variant="primary"] property selector being matched."""
    return (
        f"QPushButton{{background:{p['accent']};color:{p['on_accent']};"
        f"border:none;border-radius:9px;padding:7px 18px;font-size:13px;font-weight:600;}}"
        f"QPushButton:hover{{background:{p['accent_hover']};}}"
        f"QPushButton:focus{{border:2px solid {p['on_accent']};padding:5px 16px;}}")


def _qt_key_name(key):
    """Map a Qt key code to the name the `keyboard` library expects, or None
    for keys we don't accept as a hotkey trigger (bare modifiers etc.)."""
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key).lower()
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)
    if Qt.Key_F1 <= key <= Qt.Key_F12:
        return "f" + str(key - Qt.Key_F1 + 1)
    special = {
        Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter",
        Qt.Key_Tab: "tab", Qt.Key_Backspace: "backspace", Qt.Key_Insert: "insert",
        Qt.Key_Delete: "delete", Qt.Key_Home: "home", Qt.Key_End: "end",
        Qt.Key_PageUp: "page up", Qt.Key_PageDown: "page down",
        Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right",
        # Punctuation keys — must match the names hotkey._KEYS accepts.
        Qt.Key_QuoteLeft: "`", Qt.Key_AsciiTilde: "`",
        Qt.Key_Minus: "-", Qt.Key_Equal: "=",
        Qt.Key_BracketLeft: "[", Qt.Key_BracketRight: "]",
        Qt.Key_Backslash: "\\", Qt.Key_Semicolon: ";", Qt.Key_Apostrophe: "'",
        Qt.Key_Comma: ",", Qt.Key_Period: ".", Qt.Key_Slash: "/",
    }
    return special.get(key)


class HotkeyEdit(QPushButton):
    """A button that captures a key combo when clicked and emits it as a
    keyboard-library string (e.g. 'ctrl+alt+space'). Esc cancels capture."""

    captured = Signal(str)
    _MODS = {Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta,
             Qt.Key_AltGr, Qt.Key_Super_L, Qt.Key_Super_R}

    def __init__(self, palette, current):
        super().__init__(current or "לא הוגדר")
        self._p = palette
        self._current = current
        self._capturing = False
        self.setStyleSheet(_primary_btn_qss(palette))
        self.setMinimumWidth(150)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._begin)

    def _begin(self):
        self._capturing = True
        self.setText("הקש צירוף מקשים…")
        self.grabKeyboard()
        self.setFocus()

    def _finish(self, combo):
        self._capturing = False
        self.releaseKeyboard()
        if combo:
            self._current = combo
            self.setText(combo)
            self.captured.emit(combo)
        else:
            self.setText(self._current or "לא הוגדר")

    def reset(self):
        """Revert the label to the last accepted hotkey (after a rejection)."""
        self.setText(self._current or "לא הוגדר")

    def keyPressEvent(self, e):
        if not self._capturing:
            return super().keyPressEvent(e)
        key = e.key()
        if key == Qt.Key_Escape:
            self._finish(None)
            return
        if key in self._MODS:
            return  # wait for a real key while modifiers are held
        name = _qt_key_name(key)
        if not name:
            return
        parts = []
        m = e.modifiers()
        if m & Qt.ControlModifier:
            parts.append("ctrl")
        if m & Qt.AltModifier:
            parts.append("alt")
        if m & Qt.ShiftModifier:
            parts.append("shift")
        if m & Qt.MetaModifier:
            parts.append("windows")
        parts.append(name)
        self._finish("+".join(parts))

    def focusOutEvent(self, e):
        if self._capturing:
            self._finish(None)  # clicking away cancels
        super().focusOutEvent(e)


class HistoryCard(QFrame):
    """A transcription card: timestamp, RTL clickable text, and copy/delete.

    The actions used to be hidden until hover, which made them undiscoverable —
    nothing on screen suggested a card could be copied or deleted. They are now
    always present but dimmed, and come to full strength under the cursor.
    """

    def __init__(self, win, entry_id, text, time_str, original_text=None):
        super().__init__()
        self.setObjectName("card")
        p = win.p
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 12)
        v.setSpacing(6)

        top = QHBoxLayout()
        ts = QLabel(time_str)
        ts.setObjectName("hint")
        top.addWidget(ts)
        top.addStretch(1)
        if isinstance(original_text, str):
            source = QPushButton("הצג מקור")
            source.setCursor(Qt.PointingHandCursor)
            source.clicked.connect(lambda: win.show_original(entry_id))
            top.addWidget(source)
        self._actions = QWidget()
        ah = QHBoxLayout(self._actions)
        ah.setContentsMargins(0, 0, 0, 0)
        ah.setSpacing(5)
        # Two icon variants per button (dim / full) — swapping a prebuilt QIcon
        # is far cheaper than a QGraphicsOpacityEffect on every card.
        dim = _blend(p["text_muted"], p["surface"], 0.66)
        dim_danger = _blend(p["danger"], p["surface"], 0.62)
        self._copy = self._icon_btn("copy", dim, p["text_muted"],
                                    lambda: win.copy_text(text), "העתק")
        self._trash = self._icon_btn("trash", dim_danger, p["danger"],
                                     lambda: win.delete_entry(entry_id), "מחק")
        ah.addWidget(self._copy)
        ah.addWidget(self._trash)
        top.addWidget(self._actions)
        v.addLayout(top)

        body = QLabel(win.card_html(entry_id, text))
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setOpenExternalLinks(False)
        body.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.TextSelectableByMouse)
        body.setObjectName("cardtext")
        body.linkActivated.connect(win.on_word_clicked)
        v.addWidget(body)

    def _icon_btn(self, name, dim_color, full_color, cb, tip):
        b = QPushButton()
        b.setProperty("variant", "icon")
        b.setFixedSize(34, 32)
        b.setToolTip(tip)
        b._dim = icons.icon(name, dim_color, 17)
        b._full = icons.icon(name, full_color, 17)
        b.setIcon(b._dim)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: cb())
        return b

    def _set_hot(self, hot):
        for b in (self._copy, self._trash):
            b.setIcon(b._full if hot else b._dim)

    def enterEvent(self, _e):
        self._set_hot(True)

    def leaveEvent(self, _e):
        self._set_hot(False)


class Toast(QFrame):
    """Transient message pinned to the bottom of the window, with one optional
    action. Used instead of a modal box for things the user should be able to
    ignore — a deletion they can undo, a copy that succeeded."""

    def __init__(self, parent, palette):
        super().__init__(parent)
        self.p = palette
        self.setObjectName("toast")  # styled by build_qss
        h = QHBoxLayout(self)
        h.setContentsMargins(theme.SP["md"], theme.SP["sm"], theme.SP["sm"], theme.SP["sm"])
        h.setSpacing(theme.SP["md"])
        self._label = QLabel()
        self._label.setObjectName("toastlabel")
        h.addWidget(self._label)
        h.addStretch(1)
        self._action = QPushButton()
        self._action.setObjectName("toastaction")
        self._action.setCursor(Qt.PointingHandCursor)
        h.addWidget(self._action)
        self._cb = None
        self._action.clicked.connect(self._fire)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def _fire(self):
        cb, self._cb = self._cb, None
        self.hide()
        if cb:
            cb()

    def show_message(self, text, action=None, on_action=None, msec=7000):
        self._label.setText(text)
        self._cb = on_action
        self._action.setText(action or "")
        self._action.setVisible(bool(action))
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(msec)

    def reposition(self):
        par = self.parentWidget()
        if par is None:
            return
        self.adjustSize()
        w = min(max(self.sizeHint().width(), 260), par.width() - 60)
        self.resize(w, self.sizeHint().height())
        self.move((par.width() - w) // 2, par.height() - self.height() - 22)


from ui_pages.history_page import HistoryPageMixin
from ui_pages.dictionary_page import DictionaryPageMixin
from ui_pages.settings_page import SettingsPageMixin


class MainWindow(HistoryPageMixin, DictionaryPageMixin, SettingsPageMixin, FramelessWindow):
    """The branded shell: title bar + nav rail + stacked pages."""

    _update_result = Signal(object)  # latest version string (or None), off-thread
    _cloud_result = Signal(object)
    _scan_progress = Signal(object)
    _scan_result = Signal(object)

    def __init__(self, ui, palette):
        super().__init__()
        self.ui = ui
        self.p = palette
        self._force_close = False  # tests can close instead of hiding to tray
        self._update_result.connect(self._on_update_result)
        self._cloud_result.connect(self._on_cloud_result)
        self._scan_progress.connect(self._on_scan_progress)
        self._scan_result.connect(self._on_scan_result)
        # Rendering a history card costs a flag_tokens() pass (wordfreq lookups
        # per Hebrew word), so the built HTML is cached per entry. Anything that
        # changes the dictionary must clear it — see _invalidate_cards().
        self._html_cache = {}
        self._entries = []  # last loaded history, so clicks don't re-read the file
        self._top_card_id = None  # newest card on screen; blocks duplicate prepends
        self._page_limit = HISTORY_PAGE  # grows by a page via "הצג עוד"
        self._more_ref = None  # the live "הצג עוד" button, when one is shown
        self.setWindowTitle("MyWhisper — Matan Digital")
        self.setMinimumSize(720, 560)
        self.resize(900, 680)
        theme.bind_style(self.container, lambda _theme_palette: f"#container{{background:{_theme_palette['bg']};border-radius:14px;}}", palette)

        self.body.addWidget(TitleBar(palette, ui.toggle_theme,
                                     self.showMinimized, self.close, on_max=self.toggle_max))

        mid = QWidget()
        midl = QHBoxLayout(mid)
        midl.setContentsMargins(0, 0, 0, 0)
        midl.setSpacing(0)
        self.nav = NavRail(palette, [("history", "היסטוריה"),
                                     ("dictionary", "מילון"),
                                     ("settings", "הגדרות")])
        self.nav.set_tooltips(["Ctrl+1", "Ctrl+2", "Ctrl+3"])
        self.nav.selected.connect(self._goto)
        self.stack = QStackedWidget()
        page_wrap = QFrame()
        theme.bind_style(page_wrap, lambda _theme_palette: f"background:{_theme_palette['surface']};", palette)
        pw = QVBoxLayout(page_wrap)
        pw.setContentsMargins(0, 0, 0, 0)
        pw.addWidget(self.stack)
        midl.addWidget(self.nav)
        midl.addWidget(page_wrap, 1)
        self.body.addWidget(mid, 1)
        grip = QSizeGrip(self)
        grip.setToolTip("גרור לשינוי גודל החלון")
        self.body.addWidget(grip, 0, Qt.AlignLeft)

        self.stack.addWidget(self._history_page())
        self.stack.addWidget(self._dict_page())
        self.stack.addWidget(self._settings_page())

        self._toast = Toast(self.container, palette)
        self._install_shortcuts()
        self.refresh_history()
        self.refresh_dict()
        self.search.setFocus()

    def set_palette(self, palette):
        """Repaint existing controls without replacing the native window."""
        areas = self.findChildren(QScrollArea)
        offsets = [(area, area.verticalScrollBar().value()) for area in areas]
        focus = QApplication.focusWidget()
        self.setUpdatesEnabled(False)
        try:
            self.p = palette
            theme.apply_palette(self, palette)
            for button in self.findChildren(QPushButton):
                binding = getattr(button, "_theme_icon", None)
                if binding:
                    name, role, size = binding
                    button.setIcon(icons.icon(name, palette[role], size))
            for edit in self.findChildren(QLineEdit):
                for action in edit.actions():
                    if not action.icon().isNull():
                        action.setIcon(icons.icon("search", palette["text_muted"], 16))
            self._theme_sw.blockSignals(True)
            self._theme_sw.setChecked(palette["name"] == "dark")
            self._theme_sw.blockSignals(False)
            self._invalidate_cards()
            self.refresh_history()
            self.refresh_dict()
            self._refresh_model_status()
            for area, offset in offsets:
                area.verticalScrollBar().setValue(offset)
            if focus is not None and focus.isVisible():
                focus.setFocus()
        finally:
            self.setUpdatesEnabled(True)
        QTimer.singleShot(0, lambda: self._restore_theme_offsets(offsets))

    @staticmethod
    def _restore_theme_offsets(offsets):
        for area, offset in offsets:
            area.verticalScrollBar().setValue(offset)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_toast", None) is not None and self._toast.isVisible():
            self._toast.reposition()

    def _install_shortcuts(self):
        """Window-level keyboard shortcuts. Nav keys mirror the rail order, so
        Ctrl+1/2/3 match היסטוריה / מילון / הגדרות top-to-bottom."""
        def bind(seq, fn):
            QShortcut(QKeySequence(seq), self, activated=fn)

        for i, seq in enumerate(("Ctrl+1", "Ctrl+2", "Ctrl+3")):
            bind(seq, lambda idx=i: self._goto(idx, from_nav=False))
        bind(QKeySequence.Find, self._focus_search)   # Ctrl+F
        bind("Ctrl+L", self._focus_search)            # browser-style alias
        bind("Escape", self._on_escape)
        bind("F5", self.refresh_history)

    def _focus_search(self):
        self._goto(0, from_nav=False)
        self.search.setFocus()
        self.search.selectAll()

    def _on_escape(self):
        """Esc clears an active search; on an empty box it hides to the tray —
        never quits, since the hotkey must keep working in the background."""
        if self.stack.currentIndex() == 0 and (self.search.text() or "").strip():
            self.search.clear()
            return
        self.close()  # closeEvent() hides to tray

    def _goto(self, i, from_nav=True):
        # Leaving the settings page stops a running mic test (frees the stream).
        if i != 2 and getattr(self, "_mic_testing", False):
            self._stop_mic_test()
        # The engine-status poll only runs while its page is actually on screen.
        timer = getattr(self, "_status_timer", None)
        if timer is not None:
            if i == 2:
                self._refresh_model_status()
                timer.start(2000)
            else:
                timer.stop()
        self.stack.setCurrentIndex(i)
        if not from_nav:
            self.nav.set_index(i)  # keep the rail highlight in sync

    def closeEvent(self, e):
        # X minimizes to the tray — the app keeps listening for the hotkey.
        # Really quitting is done from the tray menu ("יציאה").
        if getattr(self, "_mic_testing", False):
            self._stop_mic_test()
        if getattr(self, "_status_timer", None) is not None:
            self._status_timer.stop()  # nothing to poll while hidden
        if self._force_close:
            e.accept()
            return
        e.ignore()
        self.hide()
        self.ui.notify_minimized()

    # ---------------- history ----------------


    # ---------------- dictionary ----------------


    # ---------------- settings ----------------


    # ---------------- updates ----------------


    # ---------------- helpers ----------------
    def _tool_btn(self, icon_name, text, cb, danger=False):
        b = QPushButton(f" {text}")
        if danger:
            b.setProperty("variant", "danger")
        b._theme_icon = (icon_name, "danger" if danger else "text_muted", 16)
        b.setIcon(icons.icon(icon_name, self.p["danger"] if danger else self.p["text_muted"], 16))
        b.setMinimumHeight(34)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: cb())
        return b

    def _scroll(self, parent_layout):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setStyleSheet("background:transparent;")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        box = QVBoxLayout(inner)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(8)
        area.setWidget(inner)
        parent_layout.addWidget(area, 1)
        return box

    def _section(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("sectiontitle")
        return lbl

    def _plain(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("fieldlabel")
        return lbl

    def _hint(self, text, wrap=True):
        lbl = QLabel(text)
        lbl.setObjectName("hint")
        lbl.setWordWrap(wrap)
        return lbl

    def _muted(self, text):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setObjectName("muted")
        lbl.setStyleSheet(f"font-size:{theme.FS['body']}px; padding:24px;")
        return lbl

    @staticmethod
    def _clear(box):
        while box.count():
            item = box.takeAt(0)
            wd = item.widget()
            if wd is not None:
                wd.deleteLater()

    @staticmethod
    def _fmt_time(raw):
        from datetime import datetime
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).strftime("%d/%m/%Y %H:%M")
            except (ValueError, TypeError):
                continue
        return raw or ""


class AppUI(QObject):
    """Thread-safe controller. Worker threads call set_overlay_state /
    open_settings / request_quit (marshaled to the main thread via signals)."""

    _overlay_sig = Signal(str)
    _settings_sig = Signal()
    _quit_sig = Signal()
    _history_sig = Signal()

    def __init__(self, config, level_provider, on_change,
                 get_history, clear_history, test_sound, import_sound,
                 flag_tokens=None, add_correction=None, approve_word=None,
                 list_corrections=None, remove_correction=None,
                 apply_corrections=None, format_bidi=None, update_history=None,
                 delete_history=None, restore_history=None, suggest_similar=None,
                 english_terms=None, add_english_term=None,
                 remove_english_term=None):
        super().__init__()
        self.config = config
        self.level_provider = level_provider
        self.on_change = on_change
        self.get_history = get_history
        self.clear_history = clear_history
        self.test_sound = test_sound
        self.import_sound = import_sound
        self.flag_tokens = flag_tokens or (lambda t: [{"text": t, "word": False, "unknown": False}])
        self.add_correction = add_correction or (lambda w, r: None)
        self.approve_word = approve_word or (lambda w: None)
        self.list_corrections = list_corrections or (lambda: {})
        self.remove_correction = remove_correction or (lambda w: None)
        self.apply_corrections = apply_corrections or (lambda t: t)
        self.format_bidi = format_bidi or (lambda t: t)
        self.update_history = update_history or (lambda i, t: None)
        self.delete_history = delete_history or (lambda i: None)
        self.restore_history = restore_history or (lambda e, i: None)
        self.suggest_similar = suggest_similar or (lambda w: [])
        self.english_terms = english_terms or (lambda: [])
        self.add_english_term = add_english_term or (lambda t: None)
        self.remove_english_term = remove_english_term or (lambda t: None)
        self.notify = lambda *a, **k: None  # wired to Tray.notify by main
        self.set_hotkey = lambda h: True    # wired to Mywishper._set_hotkey by main
        self.list_input_devices = lambda: []       # wired by main
        self.set_input_device = lambda n: None      # wired by main
        self.mic_test_start = lambda n: False       # wired by main
        self.mic_test_stop = lambda: None
        self.mic_level = lambda: 0.0
        self.clip_paused = lambda: False            # clipboard history, wired by main
        self.set_clip_paused = lambda p: None
        self.clear_clips = lambda: None
        self.clip_count = lambda: 0
        self.model_status = lambda: None            # wired by main
        self.check_update = lambda: None            # wired by main
        self.do_update = lambda: False
        self.chatgpt_status = lambda: {"accounts": [], "models": [], "enabled": False}
        self.chatgpt_action = lambda action, value=None: self.chatgpt_status()
        self.chatgpt_browsers = lambda: [{"slug": "system", "name": "דפדפן ברירת המחדל — חיצוני"}]
        self.scan_history = lambda progress, ticket=None: None
        self.cancel_history_scan = lambda: None
        self.undo_history_scan = lambda: None
        self.can_undo_history_scan = lambda: False
        self.history_scan_report = lambda: {}
        self.prepare_history_scan = lambda: None
        self._minimize_hint_shown = False
        # Transcriptions that landed while the window was hidden; the history
        # page is rebuilt on the way back in instead of on every dictation.
        self._history_dirty = False

        self.p = theme.palette(config.get("theme", "dark"))
        self._apply_global_style()
        self._overlay = Overlay(level_provider)
        self._win = None

        self._overlay_sig.connect(self._overlay.set_state)
        self._settings_sig.connect(self._show_window)
        self._quit_sig.connect(QApplication.instance().quit)
        self._history_sig.connect(self._refresh_history_page)

    def _apply_global_style(self):
        qapp = QApplication.instance()
        qapp.setLayoutDirection(Qt.RightToLeft)
        qapp.setStyleSheet(theme.build_qss(self.p))

    def _show_window(self):
        fresh = self._win is None
        if fresh:
            self._win = MainWindow(self, self.p)  # its __init__ loads history
        w = self._win
        if self._history_dirty and not fresh:
            w.refresh_history()  # catch up on dictations made while hidden
        self._history_dirty = False
        w.setWindowState((w.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        w.showMaximized() if w.isMaximized() else w.showNormal()
        w.raise_()
        w.activateWindow()
        try:
            import ctypes
            hwnd = int(w.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 3 if w.isMaximized() else 5)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def toggle_theme(self):
        self.set_theme("light" if self.p["name"] == "dark" else "dark")

    def set_theme(self, name):
        if name == self.p["name"]:
            return
        self.config["theme"] = name
        self.on_change(self.config)
        self.p = theme.palette(name)
        self._apply_global_style()
        if self._win is not None:
            self._win.set_palette(self.p)

    def notify_minimized(self):
        """One-time balloon so the user knows X hid the window, not the app."""
        if not self._minimize_hint_shown:
            self._minimize_hint_shown = True
            self.notify("MyWhisper",
                        "התוכנה ממשיכה לרוץ ברקע. הקיצור עדיין פעיל; "
                        "ליציאה מלאה — קליק ימני על האייקון במגש ← יציאה.")

    def _refresh_history_page(self):
        """Redraw the history list (main thread). While the window is closed or
        hidden the work is deferred — refresh_history() re-reads the file and
        rebuilds every card — and _show_window() catches up on the way back."""
        if self._win is not None and self._win.isVisible():
            self._win.prepend_transcription()
        else:
            self._history_dirty = True

    # ---- thread-safe API ----
    def set_overlay_state(self, state):
        self._overlay_sig.emit(state)

    def notify_transcription(self):
        """Called from the transcription worker after a new entry is stored."""
        self._history_sig.emit()

    def open_settings(self):
        self._settings_sig.emit()

    def request_quit(self):
        self._quit_sig.emit()
