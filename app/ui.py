"""MyWhisper UI (Qt / PySide6) — professional themed shell.

A frameless branded window with a side nav rail and three pages (history,
dictionary, settings), light/dark themes, search, per-item actions and native
RTL rendering. Everything runs on the Qt main thread; worker threads talk to the
UI only through AppUI's thread-safe signals.
"""
import html
import math
import re
import threading

from version import __version__ as APP_VERSION

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

import icons
import theme
from widgets import Card, FramelessWindow, NavRail, TitleBar, ToggleSwitch

# Cards rendered per page. Qt re-lays out the whole scroll area on every insert,
# so the cost of a refresh grows with the number of cards on screen, not with the
# size of history.json — 100 cards cost ~490ms per rebuild, 25 cost ~100ms.
# "הצג עוד" adds another page.
HISTORY_PAGE = 25
MAX_HISTORY_CARDS = HISTORY_PAGE  # first page; grows via _page_limit

# recording-overlay geometry
NUM_BARS, BAR_W, BAR_GAP = 13, 5, 4
MAX_H, MIN_H, PAD_X, PAD_Y, LABEL_H = 34, 4, 18, 14, 22


class Overlay(QWidget):
    """Frameless top-center HUD with animated bars while recording/transcribing."""

    def __init__(self, level_provider):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.level_provider = level_provider
        self.state = "idle"
        self.frame = 0
        self._w = PAD_X * 2 + NUM_BARS * BAR_W + (NUM_BARS - 1) * BAR_GAP
        self._h = PAD_Y * 2 + MAX_H + LABEL_H
        self.resize(self._w + 20, self._h + 20)
        scr = QApplication.primaryScreen().geometry()
        self.move((scr.width() - self.width()) // 2, 40)
        # Started only while the HUD is visible — an always-on 30fps timer would
        # wake the GUI thread ~30x/second for the whole life of the process,
        # which is idle almost all of the time.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_state(self, state):
        self.state = state
        if state in ("recording", "transcribing"):
            self.show()
            self.raise_()
            if not self._timer.isActive():
                self._timer.start(33)
        else:
            self._timer.stop()
            self.hide()

    def _tick(self):
        self.frame += 1
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ox, oy = 10, 10
        p.setBrush(QColor(28, 30, 38, 240))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(ox, oy, self._w, self._h, 16, 16)
        baseline = oy + PAD_Y + MAX_H
        if self.state == "recording":
            color, label = QColor("#ff5b5b"), "מקליט..."
            level = max(0.0, min(1.0, self.level_provider()))
            for i in range(NUM_BARS):
                wave = math.sin(self.frame * 0.3 + i * 0.55) * 0.5 + 0.5
                amp = (0.08 + 0.06 * wave) + level * (0.35 + 0.65 * wave)
                self._bar(p, ox, i, baseline, MIN_H + amp * (MAX_H - MIN_H), color)
        else:
            color, label = QColor("#f0b429"), "מתמלל..."
            for i in range(NUM_BARS):
                wave = math.sin(self.frame * 0.25 - i * 0.5) * 0.5 + 0.5
                h = MIN_H + (0.2 + 0.8 * wave) * (MAX_H - MIN_H) * 0.7
                self._bar(p, ox, i, baseline, h, color)
        p.setPen(QColor("#dddddd"))
        p.setFont(QFont(theme.pick_font(), 9, QFont.Bold))
        p.drawText(ox, oy + self._h - LABEL_H, self._w, LABEL_H, Qt.AlignCenter, label)
        p.end()

    def _bar(self, p, ox, i, baseline, h, color):
        x = ox + PAD_X + i * (BAR_W + BAR_GAP)
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(x, int(baseline - h), BAR_W, int(h), 2, 2)


class CorrectionDialog(QDialog):
    def __init__(self, parent, palette, word, on_save, on_approve,
                 suggestions=None):
        super().__init__(parent)
        self.setWindowTitle("תיקון מילה")
        self.setStyleSheet(f"QDialog{{background:{palette['bg']};}}")
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
            sug_label.setStyleSheet(
                f"color:{p['text_muted']}; font-size:12px; margin-top:4px;"
            )
            lay.addWidget(sug_label)
            chips_row = QHBoxLayout()
            chips_row.setContentsMargins(0, 0, 0, 0)
            chips_row.setSpacing(6)
            for sug in suggestions:
                chip = QPushButton(sug)
                chip.setCursor(Qt.PointingHandCursor)
                chip.setStyleSheet(
                    f"QPushButton{{"
                    f"  background:{p['surface']};"
                    f"  color:{p['accent']};"
                    f"  border:1px solid {p['accent']};"
                    f"  border-radius:12px;"
                    f"  padding:3px 12px;"
                    f"  font-size:13px;"
                    f"}}"
                    f"QPushButton:hover{{"
                    f"  background:{p['accent']};"
                    f"  color:{p['on_accent']};"
                    f"}}"
                )
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
            self._on_save(self._word, new)
        self.accept()

    def _approve(self):
        self._on_approve(self._word)
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
        self.setStyleSheet(f"QDialog{{background:{palette['bg']};}}")
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
        title.setStyleSheet(f"color:{palette['text']};background:transparent;")
        hl.addWidget(title)
        intro = self._read_intro()
        if intro:
            sub = QLabel(intro)
            sub.setWordWrap(True)
            sub.setStyleSheet(
                f"color:{palette['text_muted']};font-size:13px;background:transparent;")
            hl.addWidget(sub)
        lay.addWidget(header)

        # --- divider ---
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background:{palette['border']};border:none;")
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
                card.setStyleSheet(
                    f"#clcard{{background:{bg};border:2px solid {p['accent']};"
                    f"border-radius:14px;}}")
            else:
                card.setStyleSheet(
                    f"#clcard{{background:{p['surface']};"
                    f"border:1px solid {p['border']};border-radius:14px;}}")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(18, 15, 18, 16)
            cl.setSpacing(11)

            hr = QHBoxLayout()
            hr.setSpacing(8)
            badge = QLabel(("v" + v["ver"]) if v["ver"] else v["head"])
            badge.setStyleSheet(
                f"background:{p['accent'] if is_current else p['surface_alt']};"
                f"color:{p['on_accent'] if is_current else p['text']};"
                f"border-radius:8px;padding:3px 11px;font-weight:800;font-size:13px;")
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
        f"QPushButton:hover{{background:{p['accent_hover']};}}")


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

    def __init__(self, win, entry_id, text, time_str):
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
        self._actions = QWidget()
        ah = QHBoxLayout(self._actions)
        ah.setContentsMargins(0, 0, 0, 0)
        ah.setSpacing(2)
        # Two icon variants per button (dim / full) — swapping a prebuilt QIcon
        # is far cheaper than a QGraphicsOpacityEffect on every card.
        dim = _blend(p["text_muted"], p["surface"], 0.42)
        dim_danger = _blend(p["danger"], p["surface"], 0.42)
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
        b.setFixedSize(28, 26)
        b.setToolTip(tip)
        b._dim = icons.icon(name, dim_color, 16)
        b._full = icons.icon(name, full_color, 16)
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


class MainWindow(FramelessWindow):
    """The branded shell: title bar + nav rail + stacked pages."""

    _update_result = Signal(object)  # latest version string (or None), off-thread

    def __init__(self, ui, palette):
        super().__init__()
        self.ui = ui
        self.p = palette
        self._force_close = False  # set by AppUI._rebuild for a real close
        self._update_result.connect(self._on_update_result)
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
        self.container.setStyleSheet(f"#container{{background:{palette['bg']};border-radius:14px;}}")

        self.body.addWidget(TitleBar(palette, ui.toggle_theme,
                                     self.showMinimized, self.close))

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
        page_wrap.setStyleSheet(f"background:{palette['surface']};")
        pw = QVBoxLayout(page_wrap)
        pw.setContentsMargins(0, 0, 0, 0)
        pw.addWidget(self.stack)
        midl.addWidget(self.nav)
        midl.addWidget(page_wrap, 1)
        self.body.addWidget(mid, 1)

        self.stack.addWidget(self._history_page())
        self.stack.addWidget(self._dict_page())
        self.stack.addWidget(self._settings_page())

        self._toast = Toast(self.container, palette)
        self._install_shortcuts()
        self.refresh_history()
        self.refresh_dict()

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
    def _history_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("חיפוש בהיסטוריה…   (Ctrl+F)")
        self.search.addAction(icons.icon("search", self.p["text_muted"], 16),
                              QLineEdit.LeadingPosition)
        # Debounced: rebuilding up to MAX_HISTORY_CARDS cards on every keystroke
        # made typing in the search box stutter.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.refresh_history)
        self.search.textChanged.connect(self._on_search_changed)
        bar.addWidget(self.search, 1)
        refresh = self._tool_btn("refresh", "רענן", self.refresh_history)
        clear = self._tool_btn("trash", "נקה הכל", self._clear_all, danger=True)
        bar.addWidget(refresh)
        bar.addWidget(clear)
        v.addLayout(bar)
        self._hist_box = self._scroll(v)
        return w

    def refresh_history(self):
        # Adding ~100 cards one by one re-lays out the scroll area on every
        # insert, which costs far more than building the widgets themselves.
        # Freeze painting/layout for the whole rebuild and thaw once at the end.
        host = self._hist_box.parentWidget()
        if host is not None:
            host.setUpdatesEnabled(False)
        try:
            self._clear(self._hist_box)
            self._more_ref = None  # the old footer was just deleted
            q = (self.search.text() if hasattr(self, "search") else "").strip().lower()
            entries = self.ui.get_history()
            self._entries = entries  # reused by on_word_clicked, no re-read
            self._top_card_id = entries[0].get("id") if entries else None
            matches = [e for e in entries
                       if not q or q in (e.get("text", "") or "").lower()]
            limit = min(self._page_limit, len(matches))
            for e in matches[:limit]:
                self._hist_box.addWidget(
                    HistoryCard(self, e.get("id", ""),
                                (e.get("text", "") or "").strip(),
                                self._fmt_time(e.get("time", ""))))
            if not matches:
                self._hist_box.addWidget(
                    self._muted(f"לא נמצאו תוצאות עבור “{self.search.text().strip()}”")
                    if q else self._empty_state())
            elif len(matches) > limit:
                self._hist_box.addWidget(self._more_btn(len(matches) - limit))
            self._hist_box.addStretch(1)
        finally:
            if host is not None:
                host.setUpdatesEnabled(True)

    def _on_search_changed(self):
        # A new query starts from page 1 — otherwise a wide search inherits the
        # expanded limit from the previous one and rebuilds far more than needed.
        self._page_limit = HISTORY_PAGE
        self._search_timer.start(200)

    def _empty_state(self):
        """First-run panel: a bare 'no transcriptions yet' line told the user
        nothing about how to make one. Shows the live hotkey and the 3 steps."""
        p = self.p
        card = QFrame()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(28, 30, 28, 30)
        v.setSpacing(0)

        icon = QLabel()
        icon.setPixmap(icons.pixmap("mic", p["accent"], 44))
        icon.setAlignment(Qt.AlignCenter)
        v.addWidget(icon)
        v.addSpacing(14)

        title = QLabel("עוד לא הכתבת כלום")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont(theme.pick_font(), 15, QFont.Bold))
        title.setStyleSheet(f"color:{p['text']};")
        v.addWidget(title)
        v.addSpacing(6)

        hk = (self.ui.config.get("hotkey", "ctrl+space") or "").upper()
        sub = QLabel(f"הקיצור שלך: <b style='color:{p['accent']}'>{html.escape(hk)}</b>")
        sub.setTextFormat(Qt.RichText)
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color:{p['text_muted']}; font-size:13px;")
        v.addWidget(sub)
        v.addSpacing(20)

        for n, step in enumerate((
                "עמוד עם הסמן בכל שדה טקסט — דפדפן, וורד, ווטסאפ.",
                f"לחץ {hk} ודבר. מחוון ההקלטה יופיע בראש המסך.",
                "לחץ שוב — הטקסט יתומלל ויודבק במקום שבו הסמן נמצא."), 1):
            row = QLabel(
                f"<span style='color:{p['accent']};font-weight:700;'>{n}.</span>"
                f"&nbsp;&nbsp;<span style='color:{p['text_muted']};'>"
                f"{html.escape(step)}</span>")
            row.setTextFormat(Qt.RichText)
            row.setWordWrap(True)
            row.setStyleSheet("font-size:13px;")
            v.addWidget(row)
            v.addSpacing(8)

        v.addSpacing(6)
        tip = QLabel("הכול רץ מקומית על המחשב שלך — בלי אינטרנט ובלי חשבון.")
        tip.setAlignment(Qt.AlignCenter)
        tip.setWordWrap(True)
        tip.setObjectName("hint")
        v.addWidget(tip)
        return card

    def _more_btn(self, remaining):
        """'Show more' footer — renders the next page of cards on click."""
        b = QPushButton(f"הצג עוד  ({remaining} נוספים)")
        self._more_ref = b  # so prepend_transcription can keep its count current
        b.setObjectName("morebtn")  # styled by build_qss
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self._show_more)
        return b

    def _show_more(self):
        self._page_limit += HISTORY_PAGE
        self.refresh_history()

    def prepend_transcription(self):
        """Show a just-finished transcription without rebuilding the whole list.

        A full refresh_history() costs hundreds of ms for a long history, and
        this runs right after every dictation. Falls back to a full refresh when
        a search filter is active (the new entry may not match) or when the list
        is not in its plain state."""
        if (self.search.text() or "").strip():
            self.refresh_history()
            return
        entries = self.ui.get_history()
        self._entries = entries
        if not entries:
            return
        e = entries[0]
        text = (e.get("text", "") or "").strip()
        if not text:
            return
        # Guard against a double notification adding the same entry twice.
        if e.get("id") and e.get("id") == self._top_card_id:
            return
        self._top_card_id = e.get("id")
        # The placeholder ("no transcriptions yet") and the trailing stretch both
        # live in the box — drop the placeholder, keep cards under the cap.
        if not any(isinstance(self._hist_box.itemAt(i).widget(), HistoryCard)
                   for i in range(self._hist_box.count())):
            self._clear(self._hist_box)
            self._hist_box.addStretch(1)
        self._hist_box.insertWidget(
            0, HistoryCard(self, e.get("id", ""), text,
                           self._fmt_time(e.get("time", ""))))
        cards = [i for i in range(self._hist_box.count())
                 if isinstance(self._hist_box.itemAt(i).widget(), HistoryCard)]
        for i in reversed(cards[self._page_limit:]):
            w = self._hist_box.takeAt(i).widget()
            if w is not None:
                w.deleteLater()
        # One more entry now sits behind the fold — keep the footer count honest.
        if self._more_ref is not None:
            remaining = len(entries) - self._page_limit
            if remaining > 0:
                self._more_ref.setText(f"הצג עוד  ({remaining} נוספים)")

    def card_html(self, entry_id, text):
        highlight = self.ui.config.get("highlight_unknown", True)
        key = (entry_id, text, highlight)
        cached = self._html_cache.get(key)
        if cached is not None:
            return cached
        parts = []
        for i, tok in enumerate(self.ui.flag_tokens(text)):
            t = html.escape(tok["text"]).replace("\n", "<br>")
            if not tok.get("word"):
                parts.append(t)
                continue
            if tok.get("unknown") and highlight:
                style = f"color:{self.p['unknown_fg']};text-decoration:underline;font-weight:bold;"
            else:
                style = f"color:{self.p['text']};text-decoration:none;"
            parts.append(f'<a href="{entry_id}:{i}" style="{style}">{t}</a>')
        out = f'<div dir="rtl">{"".join(parts)}</div>'
        self._html_cache[key] = out
        return out

    def _invalidate_cards(self):
        """Drop cached card HTML after a dictionary change — approved/corrected
        words must stop rendering as unknown immediately."""
        self._html_cache.clear()

    def on_word_clicked(self, href):
        # href is "<entry_id>:<token_index>" — a stable id, so the link stays
        # valid even if new transcriptions shifted the list meanwhile.
        entry_id, _, ti = href.rpartition(":")
        try:
            ti = int(ti)
        except ValueError:
            return
        entry = next((e for e in self._entries if e.get("id") == entry_id), None)
        if entry is None:  # added since the last refresh — fall back to disk
            entry = next((e for e in self.ui.get_history()
                          if e.get("id") == entry_id), None)
        if entry is None:
            return
        text = (entry.get("text", "") or "").strip()
        tokens = self.ui.flag_tokens(text)
        if not (0 <= ti < len(tokens)):
            return
        word = tokens[ti]["text"]

        def on_save(w, new):
            self.ui.add_correction(w, new)
            self.ui.update_history(entry_id, self.ui.apply_corrections(text))
            self._invalidate_cards()
            self.refresh_history()
            self.refresh_dict()

        def on_approve(w):
            self.ui.approve_word(w)
            self._invalidate_cards()
            self.refresh_history()

        suggestions = self.ui.suggest_similar(word)
        CorrectionDialog(self, self.p, word, on_save, on_approve,
                         suggestions=suggestions).exec()

    def copy_text(self, text):
        if not text:
            return
        out = self.ui.format_bidi(text) if self.ui.config.get("bidi_isolate", True) else text
        QApplication.clipboard().setText(out)
        self._toast.show_message("הטקסט הועתק", msec=2500)

    def delete_entry(self, entry_id):
        # delete_history returns (entry, index) so the toast can put it back.
        removed = self.ui.delete_history(entry_id)
        self._invalidate_cards()
        self.refresh_history()
        if not removed:
            return
        entry, index = removed
        self._toast.show_message(
            "התמלול נמחק", action="בטל",
            on_action=lambda: self._undo_delete(entry, index))

    def _undo_delete(self, entry, index):
        self.ui.restore_history(entry, index)
        self._invalidate_cards()
        self.refresh_history()

    def _clear_all(self):
        # Deleting the whole history is irreversible (history.clear() unlinks the
        # file), and the button sits right next to "refresh" — always confirm.
        n = len(self._entries or self.ui.get_history())
        if not n:
            return
        # Built with explicit buttons rather than QMessageBox.question(), whose
        # standard buttons render as English "Yes"/"No" inside this all-Hebrew UI.
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("MyWhisper")
        box.setText(f"למחוק את כל ההיסטוריה? {n} תמלולים יימחקו לצמיתות, "
                    "ואי אפשר לשחזר אותם.")
        delete_btn = box.addButton("מחק הכל", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("ביטול", QMessageBox.RejectRole)
        box.setDefaultButton(cancel_btn)      # Enter cancels
        box.setEscapeButton(cancel_btn)       # Esc cancels
        box.exec()
        if box.clickedButton() is not delete_btn:
            return
        self.ui.clear_history()
        self._invalidate_cards()
        self.refresh_history()

    # ---------------- dictionary ----------------
    def _dict_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        bar = QHBoxLayout()
        bar.addWidget(self._section("מילון"))
        bar.addStretch(1)
        bar.addWidget(self._tool_btn("refresh", "רענן", self.refresh_dict))
        v.addLayout(bar)
        box = self._scroll(v)

        # --- English glossary: terms kept in Latin during transcription ---
        box.addWidget(self._section("מונחים באנגלית  ·  יישארו באנגלית בתמלול"))
        erow = QHBoxLayout()
        self._eng_input = self._line_edit("הוסף מונח באנגלית (למשל GitHub)")
        self._eng_input.returnPressed.connect(self._add_eng)
        erow.addWidget(self._eng_input, 1)
        erow.addWidget(self._accent_btn("הוסף", self._add_eng))
        box.addLayout(erow)
        eng_container = QWidget()
        eng_container.setStyleSheet("background:transparent;")
        self._eng_box = QVBoxLayout(eng_container)
        self._eng_box.setContentsMargins(0, 0, 0, 0)
        self._eng_box.setSpacing(6)
        box.addWidget(eng_container)

        # --- Learned corrections: "what was heard" -> "how to write it" ---
        box.addWidget(self._section("תיקונים שנלמדו  ·  שגוי ← נכון"))
        crow = QHBoxLayout()
        self._corr_wrong = self._line_edit("מה נשמע (עברית)")
        self._corr_right = self._line_edit("איך לכתוב")
        self._corr_right.returnPressed.connect(self._add_corr_manual)
        crow.addWidget(self._corr_wrong, 1)
        crow.addWidget(self._corr_right, 1)
        crow.addWidget(self._accent_btn("הוסף", self._add_corr_manual))
        box.addLayout(crow)
        corr_container = QWidget()
        corr_container.setStyleSheet("background:transparent;")
        self._dict_box = QVBoxLayout(corr_container)
        self._dict_box.setContentsMargins(0, 0, 0, 0)
        self._dict_box.setSpacing(6)
        box.addWidget(corr_container)

        box.addStretch(1)
        return w

    def _line_edit(self, placeholder):
        e = QLineEdit()
        e.setPlaceholderText(placeholder)
        e.setStyleSheet(
            f"QLineEdit{{background:{self.p['surface']}; color:{self.p['text']};"
            f" border:1px solid {self.p['border']}; border-radius:8px;"
            f" padding:6px 10px; font-size:13px;}}"
            f"QLineEdit:focus{{border-color:{self.p['accent']};}}"
        )
        return e

    def _accent_btn(self, text, cb):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:{self.p['accent']}; color:{self.p['on_accent']};"
            f" border:none; border-radius:8px; padding:6px 16px;"
            f" font-size:13px; font-weight:600;}}"
            f"QPushButton:hover{{background:{self.p['accent_hover']};}}"
        )
        b.clicked.connect(lambda: cb())
        return b

    def _kv_card(self, text, on_delete):
        card = QFrame()
        card.setObjectName("card")
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 8, 14, 8)
        x = QPushButton()
        x.setProperty("variant", "icon")
        x.setFixedSize(28, 26)
        x.setIcon(icons.icon("trash", self.p["danger"], 16))
        x.setCursor(Qt.PointingHandCursor)
        x.clicked.connect(lambda _=False: on_delete())
        h.addWidget(x)
        h.addStretch(1)
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{self.p['text']}; font-size:14px;")
        h.addWidget(lbl)
        return card

    def refresh_dict(self):
        # English glossary
        self._clear(self._eng_box)
        terms = self.ui.english_terms()
        if not terms:
            self._eng_box.addWidget(self._muted("אין מונחים באנגלית"))
        else:
            for term in terms:
                self._eng_box.addWidget(
                    self._kv_card(term, lambda t=term: self._del_eng(t)))
        # Learned corrections
        self._clear(self._dict_box)
        corr = self.ui.list_corrections()
        if not corr:
            self._dict_box.addWidget(self._muted("עדיין אין תיקונים שנלמדו"))
        else:
            for wrong, right in corr.items():
                self._dict_box.addWidget(
                    self._kv_card(f"{wrong}　←　{right}",
                                  lambda k=wrong: self._del_corr(k)))

    def _add_eng(self):
        term = self._eng_input.text().strip()
        if not term:
            return
        self.ui.add_english_term(term)
        self._eng_input.clear()
        self.refresh_dict()

    def _del_eng(self, term):
        self.ui.remove_english_term(term)
        self.refresh_dict()

    def _add_corr_manual(self):
        wrong = self._corr_wrong.text().strip()
        right = self._corr_right.text().strip()
        if not wrong or not right:
            return
        self.ui.add_correction(wrong, right)
        self._corr_wrong.clear()
        self._corr_right.clear()
        self._invalidate_cards()
        self.refresh_dict()
        self.refresh_history()

    def _del_corr(self, wrong):
        self.ui.remove_correction(wrong)
        self._invalidate_cards()
        self.refresh_dict()
        self.refresh_history()

    # ---------------- settings ----------------
    def _settings_page(self):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(14)

        # engine status — the model is released after idle_release_minutes and
        # reloaded on demand, which was invisible until now.
        stc = Card()
        stc.vbox.addWidget(self._section("מנוע התמלול"))
        srow = QHBoxLayout()
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color:{self.p['text_muted']}; font-size:15px;")
        srow.addWidget(self._status_dot)
        self._status_lbl = self._plain("בודק…")
        srow.addWidget(self._status_lbl)
        srow.addStretch(1)
        stc.vbox.addLayout(srow)
        self._status_sub = QLabel("")
        self._status_sub.setWordWrap(True)
        self._status_sub.setObjectName("hint")
        stc.vbox.addWidget(self._status_sub)
        v.addWidget(stc)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_model_status)
        self._refresh_model_status()

        # appearance
        ap = Card()
        ap.vbox.addWidget(self._section("מראה"))
        row = QHBoxLayout()
        row.addWidget(self._plain("מצב כהה"))
        row.addStretch(1)
        self._theme_sw = ToggleSwitch(self.p, checked=(self.p["name"] == "dark"))
        self._theme_sw.toggled.connect(
            lambda on: self.ui.set_theme("dark" if on else "light"))
        row.addWidget(self._theme_sw)
        ap.vbox.addLayout(row)
        v.addWidget(ap)

        # microphone
        mc = Card()
        mc.vbox.addWidget(self._section("מיקרופון"))
        mrow = QHBoxLayout()
        mrow.addWidget(self._plain("התקן קלט"))
        mrow.addStretch(1)
        self._mic_combo = QComboBox()
        self._mic_combo.setMinimumWidth(240)
        self._mic_combo.setStyleSheet(_combo_qss(self.p))
        self._mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        mrow.addWidget(self._mic_combo)
        mrow.addWidget(self._tool_btn("refresh", "רענן", self._populate_mics))
        mc.vbox.addLayout(mrow)
        mic_hint = QLabel("בחר את המיקרופון להקלטה. \"ברירת מחדל של המערכת\" עוקב אחר "
                          "ההתקן שמוגדר ב-Windows. אם הרשימה ריקה — אין מיקרופון מחובר.")
        mic_hint.setWordWrap(True)
        mic_hint.setObjectName("hint")
        mc.vbox.addWidget(mic_hint)
        # live test: open the selected mic and show the input level
        trow = QHBoxLayout()
        self._mic_test_btn = QPushButton("בדוק מיקרופון")
        self._mic_test_btn.setCursor(Qt.PointingHandCursor)
        self._mic_test_btn.clicked.connect(self._toggle_mic_test)
        trow.addWidget(self._mic_test_btn)
        self._mic_level = QProgressBar()
        self._mic_level.setRange(0, 100)
        self._mic_level.setTextVisible(False)
        self._mic_level.setFixedHeight(12)
        self._mic_level.setStyleSheet(
            f"QProgressBar{{background:{self.p['surface_alt']};border:none;border-radius:6px;}}"
            f"QProgressBar::chunk{{background:{self.p['accent']};border-radius:6px;}}")
        trow.addWidget(self._mic_level, 1)
        mc.vbox.addLayout(trow)
        self._mic_status = QLabel("")
        self._mic_status.setObjectName("hint")
        mc.vbox.addWidget(self._mic_status)
        self._mic_testing = False
        self._mic_detected = False
        self._mic_timer = QTimer(self)
        self._mic_timer.timeout.connect(self._update_mic_level)
        v.addWidget(mc)
        self._populate_mics()

        # sound
        sc = Card()
        sc.vbox.addWidget(self._section("צליל"))
        r1 = QHBoxLayout()
        r1.addWidget(self._plain("הפעל צלילים"))
        r1.addStretch(1)
        self._snd_sw = ToggleSwitch(self.p, checked=self.ui.config.get("sounds", True))
        self._snd_sw.toggled.connect(self._on_sound_toggle)
        r1.addWidget(self._snd_sw)
        sc.vbox.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(self._plain("עוצמה"))
        self._vol = QSlider(Qt.Horizontal)
        # Forced LTR: the app is globally RightToLeft, but Qt does not mirror
        # QSlider::sub-page, so the filled part was drawn on the wrong side —
        # volume 0 painted a full blue bar. A level slider reads min-left /
        # max-right in either language anyway.
        self._vol.setLayoutDirection(Qt.LeftToRight)
        self._vol.setRange(0, 100)
        self._vol.setValue(int(self.ui.config.get("sound_volume", 0.25) * 100))
        self._vol.valueChanged.connect(self._on_volume)
        self._vol_lbl = self._plain(f"{self._vol.value()}%")
        r2.addWidget(self._vol, 1)
        r2.addWidget(self._vol_lbl)
        sc.vbox.addLayout(r2)
        r3 = QHBoxLayout()
        for txt, cue in (("נגן התחלה", "start"), ("נגן סיום", "stop")):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, c=cue: self.ui.test_sound(c))
            r3.addWidget(b)
        for txt, cue in (("החלף התחלה…", "start"), ("החלף סיום…", "stop")):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, c=cue: self._replace_sound(c))
            r3.addWidget(b)
        sc.vbox.addLayout(r3)
        v.addWidget(sc)

        # smart processing — optional local LLM polish via Ollama (opt-in)
        lc = Card()
        lc.vbox.addWidget(self._section("עיבוד חכם — LLM מקומי (ניסיוני)"))
        lrow = QHBoxLayout()
        lrow.addWidget(self._plain("שיפור לשוני עם Ollama"))
        lrow.addStretch(1)
        self._llm_sw = ToggleSwitch(self.p, checked=self.ui.config.get("llm_polish", False))
        self._llm_sw.toggled.connect(self._on_llm_toggle)
        lrow.addWidget(self._llm_sw)
        lc.vbox.addLayout(lrow)
        lmrow = QHBoxLayout()
        lmrow.addWidget(self._plain("מודל"))
        lmrow.addStretch(1)
        self._llm_combo = QComboBox()
        self._llm_combo.setMinimumWidth(240)
        self._llm_combo.setStyleSheet(_combo_qss(self.p))
        self._llm_combo.currentIndexChanged.connect(self._on_llm_model_changed)
        lmrow.addWidget(self._llm_combo)
        lmrow.addWidget(self._tool_btn("refresh", "רענן", self._populate_llm_models))
        lc.vbox.addLayout(lmrow)
        cmprow = QHBoxLayout()
        cmprow_lbl = self._plain("מצב השוואה (הדבק את שתי הגרסאות)")
        cmprow.addWidget(cmprow_lbl)
        cmprow.addStretch(1)
        self._llm_cmp_sw = ToggleSwitch(self.p, checked=self.ui.config.get("llm_compare", False))
        self._llm_cmp_sw.toggled.connect(self._on_llm_compare_toggle)
        cmprow.addWidget(self._llm_cmp_sw)
        lc.vbox.addLayout(cmprow)
        styrow = QHBoxLayout()
        styrow.addWidget(self._plain("ניסוח מקצועי (שכתוב קרוב למקור)"))
        styrow.addStretch(1)
        self._llm_style_sw = ToggleSwitch(
            self.p, checked=self.ui.config.get("llm_style", "correct") == "rewrite")
        self._llm_style_sw.toggled.connect(self._on_llm_style_toggle)
        styrow.addWidget(self._llm_style_sw)
        lc.vbox.addLayout(styrow)
        self._llm_status = QLabel("")
        self._llm_status.setObjectName("hint")
        lc.vbox.addWidget(self._llm_status)
        llm_hint = QLabel(
            "מריץ מודל שפה מקומי (Ollama) לשיפור הטקסט אחרי התמלול — הכול נשאר "
            "במחשב, והמודל מנסח בעצמו בלי להשתמש במילון הידני. כשמצב “ניסוח "
            "מקצועי” כבוי המודל מתקן רק שגיאות כתיב ופיסוק; כשהוא דלוק המודל "
            "משכתב את המשפט בצורה מקצועית יותר תוך שמירה קרובה למקור. ⚠️ ניסיוני: "
            "מוסיף כמה שניות לכל תמלול (בעיקר בפעם הראשונה). אם משהו משתבש — התמלול "
            "המקורי נשמר. דורש GPU חזק.")
        llm_hint.setWordWrap(True)
        llm_hint.setObjectName("hint")
        lc.vbox.addWidget(llm_hint)
        v.addWidget(lc)
        self._populate_llm_models()

        # hotkey
        hc = Card()
        hc.vbox.addWidget(self._section("קיצור מקלדת"))
        row = QHBoxLayout()
        row.addWidget(self._plain("קיצור להקלטה"))
        row.addStretch(1)
        self._hk_edit = HotkeyEdit(self.p, self.ui.config.get("hotkey", "ctrl+space"))
        self._hk_edit.captured.connect(self._on_hotkey_captured)
        row.addWidget(self._hk_edit)
        hc.vbox.addLayout(row)
        hk_hint = QLabel("לחץ על הכפתור הכחול ואז הקש צירוף (למשל Ctrl+Alt+Space), "
                         "או בחר צירוף מוכן למטה. אם הקיצור לא מגיב — הצירוף כנראה תפוס "
                         "בתוכנה אחרת; נסה אחד אחר.")
        hk_hint.setWordWrap(True)
        hk_hint.setObjectName("hint")
        hc.vbox.addWidget(hk_hint)
        presets = QHBoxLayout()
        presets.addWidget(self._plain("מהיר:"))
        for combo in ("ctrl+alt+space", "ctrl+shift+space", "alt+q", "f9"):
            pb = QPushButton(combo)
            pb.setCursor(Qt.PointingHandCursor)
            pb.clicked.connect(lambda _=False, c=combo: self._apply_preset(c))
            presets.addWidget(pb)
        presets.addStretch(1)
        hc.vbox.addLayout(presets)
        v.addWidget(hc)

        # clipboard history
        cc = Card()
        cc.vbox.addWidget(self._section("היסטוריית העתקות"))
        crow = QHBoxLayout()
        ck = (self.ui.config.get("clipboard_hotkey", "ctrl+`") or "").upper()
        crow.addWidget(self._plain(f"פתיחת הרשימה: {ck}"))
        crow.addStretch(1)
        self._clip_count_lbl = QLabel("")
        self._clip_count_lbl.setObjectName("hint")
        crow.addWidget(self._clip_count_lbl)
        cc.vbox.addLayout(crow)
        prow = QHBoxLayout()
        prow.addWidget(self._plain("השהה שמירה"))
        prow.addStretch(1)
        self._clip_pause_sw = ToggleSwitch(self.p, checked=self.ui.clip_paused())
        self._clip_pause_sw.toggled.connect(self._on_clip_pause_toggle)
        prow.addWidget(self._clip_pause_sw)
        cc.vbox.addLayout(prow)
        crow2 = QHBoxLayout()
        crow2.addStretch(1)
        crow2.addWidget(self._tool_btn("trash", "נקה היסטוריית העתקות",
                                       self._clear_clips, danger=True))
        cc.vbox.addLayout(crow2)
        cc.vbox.addWidget(self._hint(
            "כל טקסט או תמונה שאתה מעתיק נשמר כאן, ולחיצה על הקיצור פותחת רשימה "
            "לחיפוש. בחירה מעתיקה את הפריט חזרה ללוח כדי שתדביק איפה שתרצה. "
            "סיסמאות ממנהלי סיסמאות (1Password, Bitwarden, KeePass) לא נשמרות — "
            "הן מסומנות ככאלה, והתוכנה מכבדת את הסימון. \"השהה שמירה\" עוצר את "
            "המעקב זמנית."))
        v.addWidget(cc)
        self._refresh_clip_count()

        # updates / version
        upc = Card()
        upc.vbox.addWidget(self._section("עדכונים"))
        urow = QHBoxLayout()
        urow.addWidget(self._plain(f"גרסה נוכחית: v{APP_VERSION}"))
        urow.addStretch(1)
        self._cl_btn = QPushButton("מה חדש?")
        self._cl_btn.setCursor(Qt.PointingHandCursor)
        self._cl_btn.clicked.connect(self._show_changelog)
        urow.addWidget(self._cl_btn)
        self._upd_btn = QPushButton("בדוק עדכונים")
        self._upd_btn.setCursor(Qt.PointingHandCursor)
        self._upd_btn.clicked.connect(self._on_check_update)
        urow.addWidget(self._upd_btn)
        upc.vbox.addLayout(urow)
        self._upd_status = QLabel("")
        self._upd_status.setWordWrap(True)
        self._upd_status.setObjectName("hint")
        upc.vbox.addWidget(self._upd_status)
        self._upd_now_btn = QPushButton("עדכן עכשיו")
        self._upd_now_btn.setStyleSheet(_primary_btn_qss(self.p))
        self._upd_now_btn.setCursor(Qt.PointingHandCursor)
        self._upd_now_btn.clicked.connect(self._on_update_now)
        self._upd_now_btn.setVisible(False)
        upc.vbox.addWidget(self._upd_now_btn)
        v.addWidget(upc)

        v.addStretch(1)
        # Wrap in a scroll area so a small window scrolls instead of squeezing
        # all the cards into an unreadable, overlapping stack.
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setStyleSheet("background:transparent;")
        area.setWidget(w)
        return area

    def _on_hotkey_captured(self, combo):
        if self.ui.set_hotkey(combo):
            QMessageBox.information(self, "MyWhisper",
                                    f"הקיצור עודכן ל-{combo}. נסה אותו עכשיו בכל שדה טקסט.")
        else:
            QMessageBox.warning(self, "MyWhisper",
                                f"לא ניתן להגדיר את הקיצור '{combo}'. נסה צירוף אחר.")
            self._hk_edit.reset()

    def _apply_preset(self, combo):
        """Set a ready-made combo without needing the key-capture interaction."""
        if self.ui.set_hotkey(combo):
            self._hk_edit._current = combo
            self._hk_edit.setText(combo)
            QMessageBox.information(self, "MyWhisper",
                                    f"הקיצור עודכן ל-{combo}. נסה אותו עכשיו בכל שדה טקסט.")
        else:
            QMessageBox.warning(self, "MyWhisper",
                                f"'{combo}' תפוס בתוכנה אחרת. נסה צירוף אחר.")

    def _refresh_clip_count(self):
        n = self.ui.clip_count()
        self._clip_count_lbl.setText(f"{n} פריטים שמורים" if n else "עדיין ריק")

    def _on_clip_pause_toggle(self, on):
        self.ui.set_clip_paused(bool(on))

    def _clear_clips(self):
        n = self.ui.clip_count()
        if not n:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("MyWhisper")
        box.setText(f"למחוק את היסטוריית ההעתקות? {n} פריטים יימחקו לצמיתות.")
        wipe = box.addButton("מחק הכל", QMessageBox.DestructiveRole)
        cancel = box.addButton("ביטול", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is not wipe:
            return
        self.ui.clear_clips()
        self._refresh_clip_count()
        self._toast.show_message("היסטוריית ההעתקות נמחקה", msec=3000)

    def _refresh_model_status(self):
        st = self.ui.model_status()
        if not st:  # not wired (tests / standalone UI) — hide the row
            self._status_lbl.setText("—")
            self._status_sub.setText("")
            return
        ok = "#2ea043"
        state = st.get("state")
        dev = (st.get("device") or "").lower()
        if state == "ready":
            where = "על ה-GPU" if dev == "cuda" else "על המעבד (CPU)"
            color, text = ok, f"טעון ומוכן — רץ {where}"
            sub = ("המודל שמור בזיכרון, כך שהתמלול מתחיל מיד."
                   if dev == "cuda" else
                   "רץ על המעבד — איטי בהרבה מ-GPU. בדוק דרייבר NVIDIA וספריות CUDA.")
        elif state == "loading":
            color, text = self.p["accent"], "נטען…"
            sub = "בהרצה הראשונה המודל גם יורד מהרשת (~1.5–3GB) — פעם אחת בלבד."
        else:
            color, text = self.p["text_muted"], "משוחרר מהזיכרון"
            sub = ("שוחרר כדי לפנות משאבים אחרי חוסר פעילות או בזמן משחק במסך מלא. "
                   "ייטען מחדש אוטומטית בלחיצה הבאה על הקיצור.")
        if st.get("fallback"):
            sub += "  ⚠️ טעינת ה-GPU נכשלה, לכן בוצעה נפילה למעבד."
        self._status_dot.setStyleSheet(f"color:{color}; font-size:15px;")
        self._status_lbl.setText(text)
        self._status_sub.setText(sub)
        model = st.get("model")
        self._status_lbl.setToolTip(model or "")

    def _show_changelog(self):
        ChangelogDialog(self, self.p).exec()

    # ---------------- updates ----------------
    def _on_check_update(self):
        self._upd_btn.setEnabled(False)
        _set_role(self._upd_status, "hint")
        self._upd_status.setText("בודק עדכונים…")
        threading.Thread(
            target=lambda: self._update_result.emit(self.ui.check_update()),
            daemon=True).start()

    def _on_update_result(self, latest):
        self._upd_btn.setEnabled(True)
        if not latest:
            self._upd_status.setText("בדיקת העדכונים נכשלה — בדוק את החיבור לאינטרנט.")
            self._upd_now_btn.setVisible(False)
            return
        if _version_gt(latest, APP_VERSION):
            self._upd_status.setText(f"עדכון זמין: v{latest} (מותקן: v{APP_VERSION})")
            self._upd_now_btn.setVisible(True)
        else:
            _set_role(self._upd_status, "statusok")
            self._upd_status.setText("✓ מותקנת הגרסה האחרונה")
            self._upd_now_btn.setVisible(False)

    def _on_update_now(self):
        if self.ui.do_update():
            QMessageBox.information(
                self, "MyWhisper",
                "העדכון החל בחלון נפרד. האפליקציה תיסגר ותיפתח מחדש אוטומטית "
                "בסיום. אל תסגור את חלון העדכון.")
        else:
            QMessageBox.warning(
                self, "MyWhisper",
                "לא ניתן להפעיל את העדכון. עדכן ידנית בעזרת פקודת ההתקנה מה-README.")

    def _populate_mics(self):
        self._mic_combo.blockSignals(True)
        self._mic_combo.clear()
        self._mic_combo.addItem("ברירת מחדל של המערכת", "")
        for name in self.ui.list_input_devices():
            self._mic_combo.addItem(name, name)
        current = self.ui.config.get("input_device", "")
        idx = self._mic_combo.findData(current)
        self._mic_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._mic_combo.blockSignals(False)

    def _on_mic_changed(self, _idx):
        if self._mic_testing:
            self._stop_mic_test()  # the old device stream is stale now
        self.ui.set_input_device(self._mic_combo.currentData() or "")

    def _toggle_mic_test(self):
        if self._mic_testing:
            self._stop_mic_test()
            return
        device = self._mic_combo.currentData() or ""
        if not self.ui.mic_test_start(device):
            QMessageBox.warning(self, "MyWhisper",
                                "לא ניתן לפתוח את המיקרופון הזה. בחר התקן אחר מהרשימה.")
            return
        self._mic_testing = True
        self._mic_detected = False
        self._mic_test_btn.setText("עצור בדיקה")
        self._mic_status.setText("דבר עכשיו כדי לבדוק…")
        self._mic_timer.start(50)

    def _stop_mic_test(self):
        self._mic_timer.stop()
        self.ui.mic_test_stop()
        self._mic_testing = False
        self._mic_test_btn.setText("בדוק מיקרופון")
        self._mic_level.setValue(0)

    def _update_mic_level(self):
        lvl = self.ui.mic_level()
        self._mic_level.setValue(int(max(0.0, min(1.0, lvl)) * 100))
        if lvl > 0.06:
            self._mic_detected = True
        # Only restyle on an actual transition — this runs every 50ms.
        role = "statusok" if self._mic_detected else "hint"
        if self._mic_status.objectName() != role:
            _set_role(self._mic_status, role)
        self._mic_status.setText("✓ קלט זוהה — המיקרופון עובד" if self._mic_detected
                                 else "דבר עכשיו כדי לבדוק…")

    def _on_sound_toggle(self, on):
        self.ui.config["sounds"] = bool(on)
        self.ui.on_change(self.ui.config)

    def _populate_llm_models(self):
        self._llm_combo.blockSignals(True)
        self._llm_combo.clear()
        models = self.ui.llm_list_models()
        if models:
            for m in models:
                self._llm_combo.addItem(m, m)
            idx = self._llm_combo.findData(self.ui.config.get("llm_model", ""))
            self._llm_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self._llm_combo.setEnabled(True)
            self._llm_status.setText(f"Ollama זוהה · {len(models)} מודלים מותקנים")
        else:
            self._llm_combo.addItem("— לא זוהה Ollama —", "")
            self._llm_combo.setEnabled(False)
            self._llm_status.setText(
                "Ollama לא זוהה. התקן והפעל אותו (ollama.com), הורד מודל, ולחץ רענן.")
        self._llm_combo.blockSignals(False)

    def _on_llm_toggle(self, on):
        self.ui.config["llm_polish"] = bool(on)
        # Adopt the currently shown model if none is saved yet, so enabling it
        # actually does something without a second click.
        if on and not self.ui.config.get("llm_model") and self._llm_combo.currentData():
            self.ui.config["llm_model"] = self._llm_combo.currentData()
        self.ui.on_change(self.ui.config)

    def _on_llm_model_changed(self, _idx):
        self.ui.config["llm_model"] = self._llm_combo.currentData() or ""
        self.ui.on_change(self.ui.config)

    def _on_llm_compare_toggle(self, on):
        self.ui.config["llm_compare"] = bool(on)
        # Comparison needs a model; adopt the shown one if none saved yet.
        if on and not self.ui.config.get("llm_model") and self._llm_combo.currentData():
            self.ui.config["llm_model"] = self._llm_combo.currentData()
        self.ui.on_change(self.ui.config)

    def _on_llm_style_toggle(self, on):
        # Off = "correct" (fix errors only); On = "rewrite" (professional
        # rephrase kept close to the original).
        self.ui.config["llm_style"] = "rewrite" if on else "correct"
        self.ui.on_change(self.ui.config)

    def _on_volume(self, val):
        self._vol_lbl.setText(f"{val}%")
        self.ui.config["sound_volume"] = round(val / 100.0, 3)
        self.ui.on_change(self.ui.config)

    def _replace_sound(self, cue):
        path, _ = QFileDialog.getOpenFileName(
            self, "בחר קובץ שמע", "",
            "Audio (*.wav *.mp3 *.m4a *.ogg *.flac *.aac);;All files (*.*)")
        if not path:
            return
        ok = self.ui.import_sound(cue, path)
        QMessageBox.information(self, "MyWhisper",
                               "הצליל הוחלף בהצלחה." if ok else "החלפת הצליל נכשלה.")

    # ---------------- helpers ----------------
    def _tool_btn(self, icon_name, text, cb, danger=False):
        b = QPushButton(f" {text}")
        if danger:
            b.setProperty("variant", "danger")
        b.setIcon(icons.icon(icon_name, self.p["danger"] if danger else self.p["text_muted"], 16))
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
                 remove_english_term=None, llm_list_models=None):
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
        self.llm_list_models = llm_list_models or (lambda: [])
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
        w.showNormal()
        w.raise_()
        w.activateWindow()
        try:
            import ctypes
            hwnd = int(w.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 5)
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
        QTimer.singleShot(0, self._rebuild)

    @staticmethod
    def _scroll_of(page):
        """The QScrollArea inside a stack page, if it has one."""
        if page is None:
            return None
        return page if isinstance(page, QScrollArea) else page.findChild(QScrollArea)

    def _rebuild(self):
        idx = self._win.stack.currentIndex() if self._win else 0
        geo = self._win.geometry() if self._win else None
        # Preserve how far down the page the user was. Without this, switching
        # theme silently jumped every page back to the top, so the control they
        # were looking at moved out from under the cursor.
        offset = 0
        if self._win is not None:
            area = self._scroll_of(self._win.stack.currentWidget())
            if area is not None:
                offset = area.verticalScrollBar().value()
            self._win._force_close = True  # real close, not minimize-to-tray
            self._win.close()
            self._win.deleteLater()
            self._win = None
        self.p = theme.palette(self.config.get("theme", "dark"))
        self._apply_global_style()
        self._win = MainWindow(self, self.p)
        if geo is not None:
            self._win.setGeometry(geo)
        self._win.nav.set_index(idx)
        self._win._goto(idx)
        self._show_window()
        if offset:
            # After the layout settles, or the scrollbar range is still 0.
            QTimer.singleShot(0, lambda: self._restore_scroll(idx, offset))

    def _restore_scroll(self, idx, offset):
        if self._win is None:
            return
        area = self._scroll_of(self._win.stack.widget(idx))
        if area is not None:
            area.verticalScrollBar().setValue(offset)

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
