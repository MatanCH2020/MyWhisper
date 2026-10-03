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
        v.addWidget(self._chatgpt_card())

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


    def _chatgpt_card(self):
        card = Card()
        card.vbox.addWidget(self._section("שיפור הטקסט עם ChatGPT"))
        card.vbox.addWidget(self._hint(
            "תמלול מקומי כברירת מחדל. עריכת טקסט בענן — לבחירתך. "
            "התוספת מסירה חזרות מקריות והיסוסים ומשפרת ניסוח ופיסוק. "
            "היא אינה מבטיחה תיקון של כל טעות בזיהוי הדיבור."))
        self._cloud_busy = False
        self._cloud_connection = QLabel()
        self._cloud_connection.setObjectName("connectionstatus")
        self._cloud_connection.setTextFormat(Qt.PlainText)
        self._cloud_connection.setWordWrap(True)
        self._cloud_connection.setAccessibleName("מצב החיבור לחשבון ChatGPT")
        card.vbox.addWidget(self._cloud_connection)
        self._cloud_connection_detail = QLabel()
        self._cloud_connection_detail.setTextFormat(Qt.PlainText)
        self._cloud_connection_detail.setObjectName("fieldlabel")
        self._cloud_connection_detail.setWordWrap(True)
        card.vbox.addWidget(self._cloud_connection_detail)
        self._cloud_account = QComboBox()
        self._cloud_account.setAccessibleName("חשבון ChatGPT פעיל")
        self._cloud_account.currentIndexChanged.connect(self._cloud_account_changed)
        card.vbox.addWidget(self._cloud_account)
        browser_row = QHBoxLayout()
        browser_row.addWidget(self._plain("דפדפן להתחברות"))
        self._cloud_browser = QComboBox()
        self._cloud_browser.setAccessibleName("דפדפן חיצוני להתחברות ChatGPT")
        for browser in self.ui.chatgpt_browsers():
            self._cloud_browser.addItem(browser["name"], browser["slug"])
        self._cloud_browser.setCurrentIndex(max(0, self._cloud_browser.findData(self.ui.config.get("chatgpt_browser", "system"))))
        self._cloud_browser.currentIndexChanged.connect(lambda: self.ui.chatgpt_action("browser", self._cloud_browser.currentData()))
        browser_row.addWidget(self._cloud_browser, 1)
        card.vbox.addLayout(browser_row)
        card.vbox.addWidget(self._hint("ההתחברות נפתחת בדפדפן החיצוני שנבחר, עם פרופיל הדפדפן הרגיל שלך. "
                                      "בחר את הדפדפן שבו כבר התחברת לחשבון ChatGPT."))
        row = QHBoxLayout()
        self._cloud_login = QPushButton("Continue with ChatGPT")
        self._cloud_login.setAccessibleName("התחברות עם ChatGPT")
        self._cloud_login.clicked.connect(lambda: self._cloud_async("login"))
        row.addWidget(self._cloud_login)
        self._cloud_relogin = QPushButton("חדש התחברות")
        self._cloud_relogin.clicked.connect(lambda: self._cloud_async("relogin"))
        row.addWidget(self._cloud_relogin)
        self._cloud_logout = QPushButton("ניתוק")
        self._cloud_logout.clicked.connect(lambda: self._cloud_async("disconnect"))
        row.addWidget(self._cloud_logout)
        self._cloud_cancel = QPushButton("בטל התחברות")
        self._cloud_cancel.clicked.connect(lambda: self.ui.chatgpt_action("cancel"))
        self._cloud_cancel.hide()
        row.addWidget(self._cloud_cancel)
        card.vbox.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self._plain("מודל עריכת טקסט"))
        self._cloud_model = QComboBox()
        self._cloud_model.setAccessibleName("מודל עריכת ChatGPT")
        self._cloud_model.currentIndexChanged.connect(self._cloud_model_changed)
        row.addWidget(self._cloud_model, 1)
        self._cloud_refresh = QPushButton("רענן מודלים")
        self._cloud_refresh.clicked.connect(lambda: self._cloud_async("catalog"))
        row.addWidget(self._cloud_refresh)
        card.vbox.addLayout(row)
        card.vbox.addWidget(self._hint(
            "טקסט ההכתבה יישלח ל־OpenAI לעריכה. האודיו והתמלול נשארים במחשב. "
            "השימוש כפוף לזכאות ולמכסת החשבון. התחברות לבדה אינה מפעילה עריכה. "
            "בכשל או בעיכוב מעל שתי שניות יישמר ויודבק התמלול המקומי."
            " כשעוברים לחלון אחר בזמן העיבוד, התוצאה תמתין בהיסטוריה."))
        row = QHBoxLayout()
        row.addWidget(self._plain("הפעל עריכה בענן"))
        row.addStretch(1)
        self._cloud_switch = ToggleSwitch(self.p, checked=False)
        self._cloud_switch.setAccessibleName("הפעל שיפור טקסט עם ChatGPT")
        self._cloud_switch.toggled.connect(self._cloud_toggle)
        row.addWidget(self._cloud_switch)
        card.vbox.addLayout(row)
        self._cloud_status = QLabel()
        self._cloud_status.setWordWrap(True)
        self._cloud_status.setTextFormat(Qt.PlainText)
        self._cloud_status.setObjectName("fieldlabel")
        self._cloud_status.setAccessibleName("מצב עריכת הטקסט בענן")
        card.vbox.addWidget(self._cloud_status)
        usage = QPushButton("ניהול שימוש והרשאות ב-ChatGPT")
        usage.clicked.connect(lambda: self.ui.chatgpt_action("usage"))
        card.vbox.addWidget(usage)
        self._cloud_timer = QTimer(self)
        self._cloud_timer.timeout.connect(self._refresh_cloud)
        self._cloud_timer.start(1500)
        self._refresh_cloud()
        return card

    def _refresh_cloud(self, status=None):
        self._refresh_scan_controls()
        if self._cloud_busy:
            return
        st = status or self.ui.chatgpt_status()
        for combo, items, current in (
            (self._cloud_account, [("בחר חשבון", "")] + [(a["label"], a["key"]) for a in st.get("accounts", [])], st.get("active", "")),
            (self._cloud_model, [("בחר מודל זמין בחשבון", "")] + [(m["display_name"], m["slug"]) for m in st.get("models", [])], st.get("model", ""))):
            existing = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]
            combo.blockSignals(True)
            if existing != items:
                combo.clear()
                for label, key in items:
                    combo.addItem(label, key)
            if combo.currentData() != current:
                combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.blockSignals(False)
        connected = st.get("connected", False)
        error = st.get("error")
        reconnect = error in ("authorization", "identity") or (bool(st.get("active")) and not connected)
        self._cloud_account.setVisible(bool(st.get("accounts")))
        self._cloud_relogin.setVisible(reconnect or (connected and not st.get("eligible")))
        self._cloud_relogin.setEnabled(bool(st.get("active")))
        self._cloud_logout.setVisible(connected)
        self._cloud_logout.setEnabled(connected)
        self._cloud_model.setEnabled(connected and st.get("eligible", False))
        self._cloud_refresh.setEnabled(connected)
        model_ready = st.get("model") in {m["slug"] for m in st.get("models", [])}
        ready = (connected and st.get("eligible", False) and model_ready
                 and error not in ("authorization", "identity", "permission", "ineligible", "storage"))
        self._cloud_switch.setEnabled(bool(ready) or st.get("enabled") is True)
        self._cloud_switch.blockSignals(True)
        self._cloud_switch.setChecked(st.get("enabled") is True)
        self._cloud_switch.blockSignals(False)
        if error == "storage":
            connection = "מצב חיבור: לא ניתן לקרוא או לשמור הרשאות"
            detail = "ההרשאות המוצפנות אינן זמינות. התמלול ממשיך מקומית."
        elif reconnect:
            connection = "מצב חיבור: נדרשת התחברות מחדש"
            detail = "ההרשאה לחשבון אינה פעילה. לחץ על ‘חדש התחברות’ ואשר בדפדפן."
        elif connected:
            connection = "מצב חיבור: מחובר ל־ChatGPT"
            detail = "חשבון פעיל: " + (st.get("email") or "ChatGPT")
        else:
            connection = "מצב חיבור: לא מחובר ל־ChatGPT"
            detail = "לחץ על Continue with ChatGPT והשלם את האישור בדפדפן. כניסה לדפדפן לבדה אינה מחברת את MyWhisper."
        signin_error = st.get("sign_in_error")
        if signin_error:
            failure = {
                "timeout": "האישור לא התקבל בזמן. לחץ על ההתחברות ופתח ניסיון חדש בדפדפן.",
                "cancelled": "ניסיון ההתחברות בוטל. אפשר להתחבר שוב כשתרצה.",
                "permission": "ההרשאה לא אושרה. נסה שוב ואשר את החיבור בדפדפן.",
                "identity": "אימות זהות החשבון נכשל. נסה להתחבר מחדש.",
                "authorization": "ההרשאה נדחתה או פגה. נסה להתחבר מחדש.",
                "browser": "הדפדפן לא נפתח. בחר דפדפן מותקן ונסה שוב.",
                "storage": "לא ניתן לשמור את הרשאות החיבור במחשב.",
            }.get(signin_error, "החיבור לא הושלם. בדוק את האינטרנט ונסה שוב.")
            if connected:
                detail += "\nהניסיון האחרון לא הושלם: " + failure
            else:
                detail = failure
        if st.get("signing_in"):
            connection = "מצב חיבור: ממתין לאישור בדפדפן…"
            detail = "השלם את ההתחברות והאישור בדפדפן. החיבור יופיע כאן לאחר שהאישור יתקבל."
        self._cloud_connection.setText(connection)
        self._cloud_connection_detail.setText(detail)
        self._cloud_switch.setToolTip("" if ready else "יש לחבר חשבון זכאי ולבחור מודל זמין לפני הפעלה.")
        text = ("עריכת הטקסט: פעילה" if st.get("enabled") else "עריכת הטקסט: כבויה — התמלול נשאר מקומי")
        if connected and not st.get("eligible", False):
            text += "\nהחשבון מחובר, אך לא אושרה הרשאת עריכה או שהחשבון אינו זכאי."
        elif error:
            text += "\n" + {"quota": "המכסה אינה זמינה. העריכה כובתה; בדוק ניהול שימוש לפני הפעלה מחדש.",
                    "ineligible": "החשבון אינו זכאי לעריכה. התמלול ממשיך מקומית.",
                    "storage": "שמירת ההרשאות המוצפנות אינה זמינה; התמלול ממשיך מקומית.",
                    "unsupported": "המודל או האפשרות שנבחרו אינם נתמכים; בחר מודל אחר."}.get(
                        error, "נדרש חיבור מחדש לחשבון ChatGPT; התמלול ממשיך מקומית.")
        elif connected and not model_ready:
            text += "\nבחר מודל זמין ואז הפעל עריכה בנפרד."
        elif connected and not st.get("enabled"):
            text += "\nהחשבון מחובר. להפעלת השיפור יש להפעיל את המתג ולאשר שליחת טקסט."
        self._cloud_status.setText(text)
        self._cloud_login.setToolTip("הוסף חשבון ChatGPT" if st.get("accounts") else "התחבר עם חשבון ChatGPT אישי")

    def _cloud_toggle(self, on):
        if on:
            box = QMessageBox(self)
            box.setWindowTitle("הפעלת עריכת טקסט בענן")
            box.setText("טקסט ההכתבה יישלח ל־OpenAI לעריכה. האודיו והתמלול נשארים במחשב. "
                        "השימוש כפוף לזכאות ולמכסת החשבון.")
            approve = box.addButton("הפעל עריכה בענן", QMessageBox.AcceptRole)
            cancel = box.addButton("ביטול", QMessageBox.RejectRole)
            box.setDefaultButton(cancel)
            box.setEscapeButton(cancel)
            box.exec()
            on = box.clickedButton() is approve
        result = self.ui.chatgpt_action("enable", bool(on))
        self._refresh_cloud(result)
        if result.get("message"):
            self._toast.show_message(result["message"], msec=5000)

    def _cloud_model_changed(self):
        model = self._cloud_model.currentData()
        if model:
            self._refresh_cloud(self.ui.chatgpt_action("model", model))

    def _cloud_account_changed(self):
        key = self._cloud_account.currentData()
        if key and key != self.ui.chatgpt_status().get("active"):
            self._cloud_async("select", key)
        else:
            self._refresh_cloud()

    def _cloud_async(self, action, value=None):
        if self._cloud_busy:
            return
        # Disabling is synchronous and cancels any edit/refresh immediately.
        if action != "catalog":
            self.ui.chatgpt_action("enable", False)
        self._cloud_busy = True
        for widget in (self._cloud_login, self._cloud_relogin, self._cloud_logout,
                       self._cloud_account, self._cloud_model, self._cloud_refresh, self._cloud_switch):
            widget.setEnabled(False)
        self._cloud_switch.setEnabled(action == "catalog" and self.ui.chatgpt_status().get("enabled", False))
        self._cloud_browser.setEnabled(False)
        if action in ("login", "relogin"):
            self._cloud_connection.setText("מצב חיבור: ממתין לאישור בדפדפן…")
            self._cloud_connection_detail.setText("השלם את ההתחברות והאישור בדפדפן. החיבור יופיע כאן לאחר שהאישור יתקבל.")
        self._cloud_cancel.setVisible(action in ("login", "relogin"))
        self._cloud_status.setText("השלם התחברות ואישור בדפדפן…" if action in ("login", "relogin") else "מעבד…")
        def work():
            try:
                result = self.ui.chatgpt_action(action, value)
            except Exception:
                result = {**self.ui.chatgpt_status(), "message": "החיבור לא הושלם. אפשר לנסות שוב."}
            self._cloud_result.emit(result)
        threading.Thread(target=work, daemon=True).start()

    def _on_cloud_result(self, result):
        self._cloud_busy = False
        self._cloud_cancel.hide()
        self._cloud_browser.setEnabled(True)
        for widget in (self._cloud_login, self._cloud_relogin, self._cloud_logout,
                       self._cloud_account, self._cloud_model, self._cloud_refresh, self._cloud_switch):
            widget.setEnabled(True)
        self._refresh_cloud(result)
        message = result.get("message", "")
        if message:
            # Successful sign-in acknowledges ChatGPT plan use explicitly while
            # keeping the separate opt-in switch off.
            if result.get("connected") and result.get("needs_welcome") and message.startswith("החשבון מחובר"):
                box = QMessageBox(self)
                box.setWindowTitle("החשבון מחובר ל-MyWhisper")
                box.setText(message)
                got_it = box.addButton("הבנתי", QMessageBox.AcceptRole)
                box.exec()
                if box.clickedButton() is got_it:
                    self.ui.chatgpt_action("welcome")
            else:
                self._toast.show_message(message, msec=7000)

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
