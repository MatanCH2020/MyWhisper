"""Settings page behavior for the Qt shell; backends are injected."""
import threading

import theme

from version import __version__ as APP_VERSION

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget

from widgets import Card, ToggleSwitch


class SettingsPageMixin:
    def _settings_page(self):
        from ui import HotkeyEdit, _combo_qss, _primary_btn_qss
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
        theme.bind_style(self._status_dot, lambda _theme_palette: f"color:{_theme_palette['text_muted']}; font-size:15px;", self.p)
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

        # hotkey — one of the first things users need when dictation feels
        # "stuck", so keep it above the lower tuning/maintenance sections.
        hc = Card()
        hc.vbox.addWidget(self._section("קיצור הקלטה"))
        row = QHBoxLayout()
        row.addWidget(self._plain("לחיצה להפעלה/עצירה"))
        row.addStretch(1)
        self._hk_edit = HotkeyEdit(self.p, self.ui.config.get("hotkey", "ctrl+space"))
        self._hk_edit.captured.connect(self._on_hotkey_captured)
        row.addWidget(self._hk_edit)
        hc.vbox.addLayout(row)
        hk_hint = QLabel("לחץ על הכפתור הכחול ואז הקש צירוף, או בחר צירוף מוכן.")
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

        # microphone
        mc = Card()
        mc.vbox.addWidget(self._section("מיקרופון"))
        mrow = QHBoxLayout()
        mrow.addWidget(self._plain("התקן קלט"))
        mrow.addStretch(1)
        self._mic_combo = QComboBox()
        self._mic_combo.setMinimumWidth(240)
        theme.bind_style(self._mic_combo, lambda _theme_palette: _combo_qss(_theme_palette), self.p)
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
        theme.bind_style(self._mic_level, lambda _theme_palette: f"QProgressBar{{background:{_theme_palette['surface_alt']};border:none;border-radius:6px;}}"
            f"QProgressBar::chunk{{background:{_theme_palette['accent']};border-radius:6px;}}", self.p)
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

        # appearance
        ap = Card()
        ap.vbox.addWidget(self._section("מראה"))
        row = QHBoxLayout()
        row.addWidget(self._plain("מצב כהה"))
        row.addStretch(1)
        self._theme_sw = ToggleSwitch(self.p, checked=(self.p["name"] == "dark"))
        self._theme_sw.setAccessibleName("מצב כהה")
        self._theme_sw.toggled.connect(
            lambda on: self.ui.set_theme("dark" if on else "light"))
        row.addWidget(self._theme_sw)
        ap.vbox.addLayout(row)
        v.addWidget(ap)

        # sound
        sc = Card()
        sc.vbox.addWidget(self._section("צליל"))
        r1 = QHBoxLayout()
        r1.addWidget(self._plain("הפעל צלילים"))
        r1.addStretch(1)
        self._snd_sw = ToggleSwitch(self.p, checked=self.ui.config.get("sounds", True))
        self._snd_sw.setAccessibleName("הפעל צלילים")
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

        # clipboard history
        cc, cbox = self._collapsible_card(
            "היסטוריית העתקות",
            "חיפוש ושחזור של טקסטים ותמונות שהועתקו",
            expanded=False)
        crow = QHBoxLayout()
        ck = (self.ui.config.get("clipboard_hotkey", "ctrl+`") or "").upper()
        crow.addWidget(self._plain(f"פתיחת הרשימה: {ck}"))
        crow.addStretch(1)
        self._clip_count_lbl = QLabel("")
        self._clip_count_lbl.setObjectName("hint")
        crow.addWidget(self._clip_count_lbl)
        cbox.addLayout(crow)
        prow = QHBoxLayout()
        prow.addWidget(self._plain("השהה שמירה"))
        prow.addStretch(1)
        self._clip_pause_sw = ToggleSwitch(self.p, checked=self.ui.clip_paused())
        self._clip_pause_sw.setAccessibleName("השהה שמירת העתקות")
        self._clip_pause_sw.toggled.connect(self._on_clip_pause_toggle)
        prow.addWidget(self._clip_pause_sw)
        cbox.addLayout(prow)
        crow2 = QHBoxLayout()
        crow2.addStretch(1)
        crow2.addWidget(self._tool_btn("trash", "נקה היסטוריית העתקות",
                                       self._clear_clips, danger=True))
        cbox.addLayout(crow2)
        cbox.addWidget(self._hint(
            "כל טקסט או תמונה שאתה מעתיק נשמר כאן, ולחיצה על הקיצור פותחת רשימה "
            "לחיפוש. בחירה מעתיקה את הפריט חזרה ללוח כדי שתדביק איפה שתרצה. "
            "סיסמאות ממנהלי סיסמאות (1Password, Bitwarden, KeePass) לא נשמרות — "
            "הן מסומנות ככאלה, והתוכנה מכבדת את הסימון. \"השהה שמירה\" עוצר את "
            "המעקב זמנית."))
        v.addWidget(cc)
        self._refresh_clip_count()

        # updates / version
        upc, ubox = self._collapsible_card(
            "עדכונים וגרסה",
            f"מותקן עכשיו: v{APP_VERSION}",
            expanded=False)
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
        ubox.addLayout(urow)
        self._upd_status = QLabel("")
        self._upd_status.setWordWrap(True)
        self._upd_status.setObjectName("hint")
        ubox.addWidget(self._upd_status)
        self._upd_now_btn = QPushButton("עדכן עכשיו")
        theme.bind_style(self._upd_now_btn, lambda _theme_palette: _primary_btn_qss(_theme_palette), self.p)
        self._upd_now_btn.setCursor(Qt.PointingHandCursor)
        self._upd_now_btn.clicked.connect(self._on_update_now)
        self._upd_now_btn.setVisible(False)
        ubox.addWidget(self._upd_now_btn)
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


    def _collapsible_card(self, title, summary="", expanded=False):
        card = Card()
        card.vbox.setSpacing(10)
        head = QHBoxLayout()
        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(3)
        labels.addWidget(self._section(title))
        if summary:
            labels.addWidget(self._hint(summary))
        head.addLayout(labels, 1)
        toggle = QPushButton("הסתר" if expanded else "פתח")
        toggle.setProperty("variant", "ghost")
        toggle.setCursor(Qt.PointingHandCursor)
        head.addWidget(toggle)
        card.vbox.addLayout(head)

        body = QWidget()
        body.setStyleSheet("background:transparent;")
        body_box = QVBoxLayout(body)
        body_box.setContentsMargins(0, 0, 0, 0)
        body_box.setSpacing(8)
        body.setVisible(expanded)
        card.vbox.addWidget(body)

        def set_open(opened):
            body.setVisible(opened)
            toggle.setText("הסתר" if opened else "פתח")

        toggle.clicked.connect(lambda _=False: set_open(not body.isVisible()))
        return card, body_box


    def _on_hotkey_captured(self, combo):
        if self.ui.set_hotkey(combo):
            self._toast.show_message(f"הקיצור עודכן ל-{combo}", msec=3000)
        else:
            QMessageBox.warning(self, "MyWhisper",
                                f"לא ניתן להגדיר את הקיצור '{combo}'. נסה צירוף אחר.")
            self._hk_edit.reset()


    def _apply_preset(self, combo):
        """Set a ready-made combo without needing the key-capture interaction."""
        if self.ui.set_hotkey(combo):
            self._hk_edit._current = combo
            self._hk_edit.setText(combo)
            self._toast.show_message(f"הקיצור עודכן ל-{combo}", msec=3000)
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
        if self.ui.clear_clips() is False:
            return
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
        from ui import ChangelogDialog
        ChangelogDialog(self, self.p).exec()


    def _on_check_update(self):
        from ui import _set_role
        self._upd_btn.setEnabled(False)
        _set_role(self._upd_status, "hint")
        self._upd_status.setText("בודק עדכונים…")
        threading.Thread(
            target=lambda: self._update_result.emit(self.ui.check_update()),
            daemon=True).start()


    def _on_update_result(self, latest):
        from ui import _set_role, _version_gt
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
                "מתבצעות בדיקות מקדימות. האפליקציה תיסגר רק לאחר שהן יצליחו, "
                "ותיפתח מחדש לאחר העדכון. כשל בבדיקה יוצג כהודעה.")
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
        from ui import _set_role
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
        if ok:
            self._toast.show_message("הצליל הוחלף", msec=3000)
        else:
            QMessageBox.warning(self, "MyWhisper", "החלפת הצליל נכשלה.")
