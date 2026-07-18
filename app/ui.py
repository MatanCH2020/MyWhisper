"""MyWhisper UI (Qt / PySide6) — professional themed shell.

A frameless branded window with a side nav rail and three pages (history,
dictionary, settings), light/dark themes, search, per-item actions and native
RTL rendering. Everything runs on the Qt main thread; worker threads talk to the
UI only through AppUI's thread-safe signals.
"""
import html
import math

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

import icons
import theme
from widgets import Card, FramelessWindow, NavRail, TitleBar, ToggleSwitch

MAX_HISTORY_CARDS = 100

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
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_state(self, state):
        self.state = state
        if state in ("recording", "transcribing"):
            self.show()
            self.raise_()
        else:
            self.hide()

    def _tick(self):
        if self.state in ("recording", "transcribing"):
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
    def __init__(self, parent, palette, word, on_save, on_approve):
        super().__init__(parent)
        self.setWindowTitle("תיקון מילה")
        self.setStyleSheet(f"QDialog{{background:{palette['bg']};}}")
        self.resize(380, 220)
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
        hint.setStyleSheet(f"color:{p['text_muted']}; font-size:11px;")
        lay.addWidget(hint)
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


def _primary_btn_qss(p):
    """Inline primary-button style. Set directly on the widget so it never
    depends on the global [variant="primary"] property selector being matched."""
    return (
        f"QPushButton{{background:{p['accent']};color:{p['on_accent']};"
        f"border:none;border-radius:9px;padding:7px 18px;font-size:13px;font-weight:600;}}"
        f"QPushButton:hover{{background:{p['accent_hover']};}}")


def _qt_key_name(key, text):
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
        name = _qt_key_name(key, e.text())
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
    """A transcription card: timestamp, RTL clickable text, hover actions."""

    def __init__(self, win, entry_id, text, time_str):
        super().__init__()
        self.setObjectName("card")
        p = win.p
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 12)
        v.setSpacing(6)

        top = QHBoxLayout()
        ts = QLabel(time_str)
        ts.setStyleSheet(f"color:{p['text_muted']}; font-size:11px;")
        top.addWidget(ts)
        top.addStretch(1)
        self._actions = QWidget()
        ah = QHBoxLayout(self._actions)
        ah.setContentsMargins(0, 0, 0, 0)
        ah.setSpacing(2)
        copy = self._icon_btn("copy", p["text_muted"], lambda: win.copy_text(text))
        trash = self._icon_btn("trash", p["danger"], lambda: win.delete_entry(entry_id))
        ah.addWidget(copy)
        ah.addWidget(trash)
        self._actions.setVisible(False)
        top.addWidget(self._actions)
        v.addLayout(top)

        body = QLabel(win.card_html(entry_id, text))
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setOpenExternalLinks(False)
        body.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.TextSelectableByMouse)
        body.setStyleSheet(f"color:{p['text']}; font-size:14px;")
        body.linkActivated.connect(win.on_word_clicked)
        v.addWidget(body)

    def _icon_btn(self, name, color, cb):
        b = QPushButton()
        b.setProperty("variant", "icon")
        b.setFixedSize(28, 26)
        b.setIcon(icons.icon(name, color, 16))
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: cb())
        return b

    def enterEvent(self, _e):
        self._actions.setVisible(True)

    def leaveEvent(self, _e):
        self._actions.setVisible(False)


class MainWindow(FramelessWindow):
    """The branded shell: title bar + nav rail + stacked pages."""

    def __init__(self, ui, palette):
        super().__init__()
        self.ui = ui
        self.p = palette
        self._force_close = False  # set by AppUI._rebuild for a real close
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

        self.refresh_history()
        self.refresh_dict()

    def _goto(self, i):
        # Leaving the settings page stops a running mic test (frees the stream).
        if i != 2 and getattr(self, "_mic_testing", False):
            self._stop_mic_test()
        self.stack.setCurrentIndex(i)

    def closeEvent(self, e):
        # X minimizes to the tray — the app keeps listening for the hotkey.
        # Really quitting is done from the tray menu ("יציאה").
        if getattr(self, "_mic_testing", False):
            self._stop_mic_test()
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
        self.search.setPlaceholderText("חיפוש בהיסטוריה…")
        self.search.addAction(icons.icon("search", self.p["text_muted"], 16),
                              QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.refresh_history)
        bar.addWidget(self.search, 1)
        refresh = self._tool_btn("refresh", "רענן", self.refresh_history)
        clear = self._tool_btn("trash", "נקה הכל", self._clear_all, danger=True)
        bar.addWidget(refresh)
        bar.addWidget(clear)
        v.addLayout(bar)
        self._hist_box = self._scroll(v)
        return w

    def refresh_history(self):
        self._clear(self._hist_box)
        q = (self.search.text() if hasattr(self, "search") else "").strip().lower()
        entries = self.ui.get_history()
        shown = 0
        empty = True
        for e in entries:
            text = (e.get("text", "") or "").strip()
            if q and q not in text.lower():
                continue
            empty = False
            self._hist_box.addWidget(HistoryCard(self, e.get("id", ""), text,
                                                 self._fmt_time(e.get("time", ""))))
            shown += 1
            if shown >= MAX_HISTORY_CARDS:
                break
        if empty:
            self._hist_box.addWidget(self._muted(
                "לא נמצאו תוצאות" if q else "אין עדיין תמלולים"))
        self._hist_box.addStretch(1)

    def card_html(self, entry_id, text):
        highlight = self.ui.config.get("highlight_unknown", True)
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
        return f'<div dir="rtl">{"".join(parts)}</div>'

    def on_word_clicked(self, href):
        # href is "<entry_id>:<token_index>" — a stable id, so the link stays
        # valid even if new transcriptions shifted the list meanwhile.
        entry_id, _, ti = href.rpartition(":")
        try:
            ti = int(ti)
        except ValueError:
            return
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
            self.refresh_history()
            self.refresh_dict()

        def on_approve(w):
            self.ui.approve_word(w)
            self.refresh_history()

        CorrectionDialog(self, self.p, word, on_save, on_approve).exec()

    def copy_text(self, text):
        if not text:
            return
        out = self.ui.format_bidi(text) if self.ui.config.get("bidi_isolate", True) else text
        QApplication.clipboard().setText(out)

    def delete_entry(self, entry_id):
        self.ui.delete_history(entry_id)
        self.refresh_history()

    def _clear_all(self):
        self.ui.clear_history()
        self.refresh_history()

    # ---------------- dictionary ----------------
    def _dict_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        bar = QHBoxLayout()
        bar.addWidget(self._section("תיקונים שנלמדו  ·  שגוי ← נכון"))
        bar.addStretch(1)
        bar.addWidget(self._tool_btn("refresh", "רענן", self.refresh_dict))
        v.addLayout(bar)
        self._dict_box = self._scroll(v)
        return w

    def refresh_dict(self):
        self._clear(self._dict_box)
        corr = self.ui.list_corrections()
        if not corr:
            self._dict_box.addWidget(self._muted("עדיין אין תיקונים שנלמדו"))
            self._dict_box.addStretch(1)
            return
        for wrong, right in corr.items():
            card = QFrame()
            card.setObjectName("card")
            h = QHBoxLayout(card)
            h.setContentsMargins(14, 8, 14, 8)
            x = QPushButton()
            x.setProperty("variant", "icon")
            x.setFixedSize(28, 26)
            x.setIcon(icons.icon("trash", self.p["danger"], 16))
            x.setCursor(Qt.PointingHandCursor)
            x.clicked.connect(lambda _=False, k=wrong: self._del_corr(k))
            h.addWidget(x)
            h.addStretch(1)
            lbl = QLabel(f"{wrong}　←　{right}")
            lbl.setStyleSheet(f"color:{self.p['text']}; font-size:14px;")
            h.addWidget(lbl)
            self._dict_box.addWidget(card)
        self._dict_box.addStretch(1)

    def _del_corr(self, wrong):
        self.ui.remove_correction(wrong)
        self.refresh_dict()
        self.refresh_history()

    # ---------------- settings ----------------
    def _settings_page(self):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(14)

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
        self._mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        mrow.addWidget(self._mic_combo)
        mrow.addWidget(self._tool_btn("refresh", "רענן", self._populate_mics))
        mc.vbox.addLayout(mrow)
        mic_hint = QLabel("בחר את המיקרופון להקלטה. \"ברירת מחדל של המערכת\" עוקב אחר "
                          "ההתקן שמוגדר ב-Windows. אם הרשימה ריקה — אין מיקרופון מחובר.")
        mic_hint.setWordWrap(True)
        mic_hint.setStyleSheet(f"color:{self.p['text_muted']}; font-size:11px;")
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
        self._mic_status.setStyleSheet(f"color:{self.p['text_muted']}; font-size:11px;")
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
        hk_hint.setStyleSheet(f"color:{self.p['text_muted']}; font-size:11px;")
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

        # permissions / run as admin
        pc = Card()
        pc.vbox.addWidget(self._section("הרשאות"))
        pr = QHBoxLayout()
        pr.addWidget(self._plain("הקיצור לא עובד בכלל?"))
        pr.addStretch(1)
        admin_btn = QPushButton("הפעל מחדש כמנהל")
        admin_btn.setStyleSheet(_primary_btn_qss(self.p))
        admin_btn.setCursor(Qt.PointingHandCursor)
        admin_btn.clicked.connect(self._on_run_as_admin)
        pr.addWidget(admin_btn)
        pc.vbox.addLayout(pr)
        adm_hint = QLabel("קיצורים גלובליים דורשים לפעמים הרשאות מנהל. הכפתור יפעיל את "
                          "האפליקציה מחדש עם הרשאות מוגברות (אישור UAC). אם אין לך הרשאות "
                          "מנהל במחשב — שינוי הקיצור למעלה הוא הפתרון.")
        adm_hint.setWordWrap(True)
        adm_hint.setStyleSheet(f"color:{self.p['text_muted']}; font-size:11px;")
        pc.vbox.addWidget(adm_hint)
        v.addWidget(pc)

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

    def _on_run_as_admin(self):
        if self.ui.relaunch_as_admin() is False:
            QMessageBox.warning(self, "MyWhisper",
                                "לא ניתן היה להפעיל כמנהל — ייתכן שאין לך הרשאות מנהל "
                                "במחשב, או שהפעולה בוטלה. נסה לשנות את הקיצור במקום.")

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
        if self._mic_detected:
            self._mic_status.setText("✓ קלט זוהה — המיקרופון עובד")
            self._mic_status.setStyleSheet("color:#2ea043; font-size:11px; font-weight:bold;")
        else:
            self._mic_status.setText("דבר עכשיו כדי לבדוק…")
            self._mic_status.setStyleSheet(f"color:{self.p['text_muted']}; font-size:11px;")

    def _on_sound_toggle(self, on):
        self.ui.config["sounds"] = bool(on)
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
        lbl.setStyleSheet(f"color:{self.p['text']}; font-size:13px;")
        return lbl

    def _muted(self, text):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{self.p['text_muted']}; font-size:13px; padding:24px;")
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

    def __init__(self, config, level_provider, on_change,
                 get_history, clear_history, test_sound, import_sound,
                 flag_tokens=None, add_correction=None, approve_word=None,
                 list_corrections=None, remove_correction=None,
                 apply_corrections=None, format_bidi=None, update_history=None,
                 delete_history=None):
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
        self.notify = lambda *a, **k: None  # wired to Tray.notify by main
        self.set_hotkey = lambda h: True    # wired to Mywishper._set_hotkey by main
        self.relaunch_as_admin = lambda: False
        self.list_input_devices = lambda: []       # wired by main
        self.set_input_device = lambda n: None      # wired by main
        self.mic_test_start = lambda n: False       # wired by main
        self.mic_test_stop = lambda: None
        self.mic_level = lambda: 0.0
        self._minimize_hint_shown = False

        self.p = theme.palette(config.get("theme", "dark"))
        self._apply_global_style()
        self._overlay = Overlay(level_provider)
        self._win = None

        self._overlay_sig.connect(self._overlay.set_state)
        self._settings_sig.connect(self._show_window)
        self._quit_sig.connect(QApplication.instance().quit)

    def _apply_global_style(self):
        qapp = QApplication.instance()
        qapp.setLayoutDirection(Qt.RightToLeft)
        qapp.setStyleSheet(theme.build_qss(self.p))

    def _show_window(self):
        if self._win is None:
            self._win = MainWindow(self, self.p)
        w = self._win
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

    def _rebuild(self):
        idx = self._win.stack.currentIndex() if self._win else 0
        geo = self._win.geometry() if self._win else None
        if self._win is not None:
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

    def notify_minimized(self):
        """One-time balloon so the user knows X hid the window, not the app."""
        if not self._minimize_hint_shown:
            self._minimize_hint_shown = True
            self.notify("MyWhisper",
                        "התוכנה ממשיכה לרוץ ברקע. הקיצור עדיין פעיל; "
                        "ליציאה מלאה — קליק ימני על האייקון במגש ← יציאה.")

    # ---- thread-safe API ----
    def set_overlay_state(self, state):
        self._overlay_sig.emit(state)

    def open_settings(self):
        self._settings_sig.emit()

    def request_quit(self):
        self._quit_sig.emit()
