"""Settings page behavior for the Qt shell; backends are injected."""
from platform_support import IS_MAC
import threading

import theme

from version import __version__ as APP_VERSION

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider, QSizePolicy, QVBoxLayout, QWidget

from widgets import Card, ToggleSwitch


def shortcut_label(combo):
    return " + ".join(key.capitalize() for key in combo.split("+")) if combo else "לא הוגדר"


class SettingsSection(Card):
    """A full-width, keyboard-operable disclosure with a persistent open state."""

    def __init__(self, title):
        super().__init__(padding=5)
        self.header = QPushButton()
        self.header.setObjectName("settingsSection")
        self.header.setCheckable(True)
        self.header.setCursor(Qt.PointingHandCursor)
        heading = QHBoxLayout(self.header)
        heading.setContentsMargins(12, 4, 12, 4)
        self._caption = QLabel()
        self._caption.setObjectName("settingsCaption")
        self._caption.setAttribute(Qt.WA_TransparentForMouseEvents)
        heading.addWidget(self._caption, 1)
        self._indicator = QLabel()
        self._indicator.setAttribute(Qt.WA_TransparentForMouseEvents)
        heading.addWidget(self._indicator)
        self.header.setMinimumHeight(32)
        self.set_title(title)
        self.vbox.addWidget(self.header)
        body = QWidget()
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(9, 2, 9, 9)
        self.content.setSpacing(8)
        self.vbox.addWidget(body)
        body.hide()
        self.header.toggled.connect(body.setVisible)
        self.header.toggled.connect(self._refresh_title)
        self._refresh_title()

    def _refresh_title(self):
        self._indicator.setText("−" if self.header.isChecked() else "+")
        self.header.setAccessibleDescription("פתוח" if self.header.isChecked() else "סגור")

    def set_title(self, title):
        self._caption.setText(title)
        self.header.setAccessibleName(title)

    def set_open(self, opened):
        self.header.setChecked(opened)


class SettingsPageMixin:
    def _settings_page(self):
        from ui import HotkeyEdit, _combo_qss, _primary_btn_qss
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(7)

        title = QLabel("הגדרות")
        title.setObjectName("settingsTitle")
        v.addWidget(title)
        # Keep the two everyday actions visible before the optional sections.
        hc = Card()
        row = QHBoxLayout()
        row.addWidget(self._section("קיצור הקלטה"))
        combo = self.ui.config.get("hotkey", "ctrl+space")
        self._hk_label = self._plain(shortcut_label(combo))
        self._hk_label.setLayoutDirection(Qt.LeftToRight)
        row.addWidget(self._hk_label)
        row.addStretch(1)
        self._hk_change = QPushButton("שינוי קיצור")
        self._hk_change.setCheckable(True)
        row.addWidget(self._hk_change)
        hc.vbox.addLayout(row)
        hc.vbox.addWidget(self._hint("לחיצה אחת מתחילה הקלטה, ולחיצה נוספת מסיימת אותה."))
        editor = QWidget()
        edit_box = QVBoxLayout(editor)
        edit_box.setContentsMargins(0, 0, 0, 0)
        edit_box.addWidget(self._hint("לבחירת קיצור אישי, לחצו על הקיצור והקישו צירוף חדש. Esc מבטל."))
        self._hk_edit = HotkeyEdit(self.p, combo)
        self._hk_edit.setAccessibleName("הקלטת קיצור מקלדת חדש")
        self._hk_edit.captured.connect(self._on_hotkey_captured)
        edit_box.addWidget(self._hk_edit)
        presets = QComboBox()
        presets.setAccessibleName("בחירת קיצור מוכן")
        presets.addItem("בחירת קיצור מוכן…")
        for preset in ("ctrl+alt+space", "ctrl+shift+space", "alt+q", "f9"):
            presets.addItem(shortcut_label(preset), preset)
        def choose_preset(index):
            if index > 0:
                self._on_hotkey_captured(presets.itemData(index))
                presets.setCurrentIndex(0)
        presets.activated.connect(choose_preset)
        theme.bind_style(presets, _combo_qss, self.p)
        edit_box.addWidget(presets)
        hc.vbox.addWidget(editor)
        editor.hide()
        self._hk_change.toggled.connect(editor.setVisible)
        v.addWidget(hc)

        mc = Card()
        mrow = QHBoxLayout()
        mrow.addWidget(self._section("מיקרופון"))
        self._mic_combo = QComboBox()
        self._mic_combo.setAccessibleName("מיקרופון להקלטה")
        self._mic_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        theme.bind_style(self._mic_combo, _combo_qss, self.p)
        self._mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        mrow.addWidget(self._mic_combo, 1)
        mrow.addWidget(self._tool_btn("refresh", "רענון", self._populate_mics))
        self._mic_test_btn = QPushButton("בדיקת מיקרופון")
        self._mic_test_btn.clicked.connect(self._toggle_mic_test)
        mrow.addWidget(self._mic_test_btn)
        mc.vbox.addLayout(mrow)
        self._mic_warning = self._hint("המיקרופון שנבחר נותק. חברו אותו ולחצו על רענון, או בחרו מיקרופון אחר.")
        mc.vbox.addWidget(self._mic_warning)
        self._mic_status = self._hint("")
        self._mic_level = QProgressBar()
        self._mic_level.setAccessibleName("עוצמת הקול מהמיקרופון")
        self._mic_level.setRange(0, 100)
        self._mic_level.setTextVisible(False)
        self._mic_level.setFixedHeight(10)
        mc.vbox.addWidget(self._mic_status)
        mc.vbox.addWidget(self._mic_level)
        self._mic_status.hide()
        self._mic_level.hide()
        self._mic_testing = False
        self._mic_detected = False
        self._mic_timer = QTimer(self)
        self._mic_timer.timeout.connect(self._update_mic_level)
        v.addWidget(mc)
        self._populate_mics()

        status = QHBoxLayout()
        self._status_lbl = self._hint("")
        status.addWidget(self._status_lbl, 1)
        self._status_help = QPushButton("עזרה")
        self._status_help.clicked.connect(self._open_status_help)
        status.addWidget(self._status_help)
        v.addLayout(status)

        v.addWidget(self._section("אפשרויות נוספות"))

        # appearance
        ap = SettingsSection("מראה וצלילים")
        row = QHBoxLayout()
        row.addWidget(self._plain("מצב כהה"))
        row.addStretch(1)
        self._theme_sw = ToggleSwitch(self.p, checked=(self.p["name"] == "dark"))
        self._theme_sw.setAccessibleName("מצב כהה")
        self._theme_sw.toggled.connect(
            lambda on: self.ui.set_theme("dark" if on else "light"))
        row.addWidget(self._theme_sw)
        ap.content.addLayout(row)
        v.addWidget(ap)

        # sound
        r1 = QHBoxLayout()
        r1.addWidget(self._plain("הפעל צלילים"))
        r1.addStretch(1)
        self._snd_sw = ToggleSwitch(self.p, checked=self.ui.config.get("sounds", True))
        self._snd_sw.setAccessibleName("הפעל צלילים")
        self._snd_sw.toggled.connect(self._on_sound_toggle)
        r1.addWidget(self._snd_sw)
        ap.content.addLayout(r1)
        sound = SettingsSection("התאמת הצלילים")
        ap.content.addWidget(sound)
        r2 = QHBoxLayout()
        r2.addWidget(self._plain("עוצמה"))
        self._vol = QSlider(Qt.Horizontal)
        # Forced LTR: the app is globally RightToLeft, but Qt does not mirror
        # QSlider::sub-page, so the filled part was drawn on the wrong side —
        # volume 0 painted a full blue bar. A level slider reads min-left /
        # max-right in either language anyway.
        self._vol.setLayoutDirection(Qt.LeftToRight)
        self._vol.setAccessibleName("עוצמת צלילי ההקלטה")
        self._vol.setRange(0, 100)
        self._vol.setValue(int(self.ui.config.get("sound_volume", 0.25) * 100))
        self._vol.valueChanged.connect(self._on_volume)
        self._vol_lbl = self._plain(f"{self._vol.value()}%")
        r2.addWidget(self._vol, 1)
        r2.addWidget(self._vol_lbl)
        sound.content.addLayout(r2)
        r3 = QHBoxLayout()
        for txt, cue in (("נגן התחלה", "start"), ("נגן סיום", "stop")):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, c=cue: self.ui.test_sound(c))
            r3.addWidget(b)
        sound.content.addLayout(r3)
        r3 = QHBoxLayout()
        for txt, cue in (("החלף התחלה…", "start"), ("החלף סיום…", "stop")):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, c=cue: self._replace_sound(c))
            r3.addWidget(b)
        sound.content.addLayout(r3)

        # clipboard history
        cc = SettingsSection("תמלולים והעתקות")
        cbox = cc.content
        crow = QHBoxLayout()
        crow.addStretch(1)
        self._clip_count_lbl = QLabel("")
        self._clip_count_lbl.setObjectName("hint")
        crow.addWidget(self._clip_count_lbl)
        open_clips = QPushButton("פתח העתקות")
        open_clips.clicked.connect(self.ui.open_clips)
        crow.addWidget(open_clips)
        cbox.addLayout(crow)
        clip_edit = HotkeyEdit(self.p, self.ui.config.get("clipboard_hotkey", "ctrl+`"))
        clip_edit.setAccessibleName("שינוי הקיצור לפתיחת ההעתקות")
        def set_clip_shortcut(combo):
            if self.ui.set_clip_hotkey(combo):
                clip_edit.accept_combo(combo)
            else:
                QMessageBox.warning(self, "MyWhisper", "הקיצור תפוס. אפשר לבחור צירוף אחר.")
        clip_edit.captured.connect(set_clip_shortcut)
        clip_row = QHBoxLayout()
        clip_row.addWidget(self._plain("קיצור לפתיחת הרשימה"))
        clip_row.addWidget(clip_edit)
        cbox.addLayout(clip_row)
        prow = QHBoxLayout()
        prow.addWidget(self._plain("השהה מעקב העתקות"))
        prow.addStretch(1)
        self._clip_pause_sw = ToggleSwitch(self.p, checked=self.ui.clip_paused())
        self._clip_pause_sw.setAccessibleName("השהה שמירת העתקות")
        self._clip_pause_sw.toggled.connect(self._on_clip_pause_toggle)
        prow.addWidget(self._clip_pause_sw)
        cbox.addLayout(prow)
        cbox.addWidget(self._hint("בזמן ההשהיה תמלולים ממשיכים להישמר."))
        crow2 = QHBoxLayout()
        crow2.addStretch(1)
        crow2.addWidget(self._tool_btn("trash", "נקה היסטוריית העתקות",
                                       self._clear_clips, danger=True))
        cbox.addLayout(crow2)
        v.addWidget(cc)
        self._refresh_clip_count()

        if self.ui.cloud_supported:
            self._cloud_panel = SettingsSection("עריכת טקסט עם ChatGPT")
            self._build_cloud_settings(self._cloud_panel.content)
            v.addWidget(self._cloud_panel)

        self._help_panel = SettingsSection("עזרה ועדכונים")
        ubox = self._help_panel.content
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
        self._upd_progress = QProgressBar()
        self._upd_progress.setAccessibleName("התקדמות העדכון")
        self._upd_progress.setRange(0, 0)
        self._upd_progress.setTextVisible(False)
        self._upd_progress.hide()
        ubox.addWidget(self._upd_progress)
        self._upd_now_btn = QPushButton("עדכן עכשיו")
        theme.bind_style(self._upd_now_btn, lambda _theme_palette: _primary_btn_qss(_theme_palette), self.p)
        self._upd_now_btn.setCursor(Qt.PointingHandCursor)
        self._upd_now_btn.clicked.connect(self._on_update_now)
        self._upd_now_btn.setVisible(False)
        ubox.addWidget(self._upd_now_btn)
        self._support_btn = QPushButton("דיווח על תקלה")
        self._support_btn.clicked.connect(self._open_support)
        ubox.addWidget(self._support_btn)
        self._technical = SettingsSection("פרטים טכניים")
        self._status_sub = self._hint("")
        self._technical.content.addWidget(self._status_sub)
        ubox.addWidget(self._technical)
        if IS_MAC:
            setup = QPushButton("הכנת ה־Mac והרשאות")
            setup.clicked.connect(self.ui.open_mac_setup)
            ubox.addWidget(setup)
        v.addWidget(self._help_panel)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_model_status)
        self._refresh_model_status()

        v.addStretch(1)
        # Wrap in a scroll area so a small window scrolls instead of squeezing
        # all the cards into an unreadable, overlapping stack.
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setStyleSheet("background:transparent;")
        area.setWidget(w)
        return area

    def _open_support(self):
        from support_ui import SupportDialog
        dialog = getattr(self, "_support_dialog", None)
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return
        self._support_dialog = SupportDialog(
            self, self.p, self.ui.support_diagnostics, self.ui.submit_support_report)
        self._support_dialog.show()


    def _build_cloud_settings(self, box):
        from ui import _combo_qss

        box.addWidget(self._hint("ההקלטה נשארת במחשב. רק כשמפעילים עריכה נשלח הטקסט ל־OpenAI, במסגרת מכסת החשבון."))
        self._cloud_busy = False
        self._cloud_connection = self._plain("")
        self._cloud_connection.setObjectName("connectionstatus")
        self._cloud_connection.setTextFormat(Qt.PlainText)
        self._cloud_connection.setWordWrap(True)
        self._cloud_connection.setAccessibleDescription("מצב החיבור לחשבון ChatGPT")
        box.addWidget(self._cloud_connection)
        self._cloud_connection_detail = self._hint("")
        self._cloud_connection_detail.setTextFormat(Qt.PlainText)
        box.addWidget(self._cloud_connection_detail)
        self._cloud_login = QPushButton("התחברות ל־ChatGPT")
        self._cloud_login.clicked.connect(lambda: self._cloud_async("login"))
        box.addWidget(self._cloud_login)
        self._cloud_relogin = QPushButton("חדש התחברות")
        self._cloud_relogin.clicked.connect(lambda: self._cloud_async("relogin"))
        box.addWidget(self._cloud_relogin)
        self._cloud_cancel = QPushButton("בטל התחברות")
        self._cloud_cancel.clicked.connect(lambda: self.ui.chatgpt_action("cancel"))
        self._cloud_cancel.hide()
        box.addWidget(self._cloud_cancel)

        self._cloud_status = self._hint("")
        self._cloud_status.setTextFormat(Qt.PlainText)
        self._cloud_status.setAccessibleName("מצב עריכת הטקסט בענן")
        box.addWidget(self._cloud_status)
        switch_row = QHBoxLayout()
        self._cloud_switch_label = self._plain("עריכת הטקסט בענן")
        switch_row.addWidget(self._cloud_switch_label)
        switch_row.addStretch(1)
        self._cloud_switch = ToggleSwitch(self.p)
        self._cloud_switch.setAccessibleName("הפעל שיפור טקסט עם ChatGPT")
        self._cloud_switch.toggled.connect(self._cloud_toggle)
        switch_row.addWidget(self._cloud_switch)
        box.addLayout(switch_row)

        self._cloud_options = SettingsSection("אפשרויות חיבור וחשבון")
        options = self._cloud_options.content
        self._cloud_browser = QComboBox()
        self._cloud_browser.setAccessibleName("דפדפן להתחברות")
        for browser in self.ui.chatgpt_browsers():
            self._cloud_browser.addItem(browser["name"], browser["slug"])
        self._cloud_browser.setCurrentIndex(max(0, self._cloud_browser.findData(self.ui.config.get("chatgpt_browser", "system"))))
        self._cloud_browser.currentIndexChanged.connect(lambda: self.ui.chatgpt_action("browser", self._cloud_browser.currentData()))
        browser_row = QHBoxLayout()
        browser_row.addWidget(self._plain("דפדפן להתחברות"))
        browser_row.addWidget(self._cloud_browser, 1)
        options.addLayout(browser_row)
        self._cloud_account = QComboBox()
        self._cloud_account.setAccessibleName("חשבון ChatGPT פעיל")
        self._cloud_account.currentIndexChanged.connect(self._cloud_account_changed)
        options.addWidget(self._cloud_account)
        self._cloud_add = QPushButton("הוספת חשבון")
        self._cloud_add.clicked.connect(lambda: self._cloud_async("login"))
        options.addWidget(self._cloud_add)
        self._cloud_logout = QPushButton("ניתוק החשבון")
        self._cloud_logout.clicked.connect(lambda: self._cloud_async("disconnect"))
        options.addWidget(self._cloud_logout)

        # The same model picker is promoted to the next step when a choice is required.
        self._cloud_model_row = QWidget()
        model_row = QHBoxLayout(self._cloud_model_row)
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.addWidget(self._plain("מודל לעריכה"))
        self._cloud_model = QComboBox()
        self._cloud_model.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._cloud_model.setAccessibleName("מודל עריכת ChatGPT")
        self._cloud_model.currentIndexChanged.connect(self._cloud_model_changed)
        model_row.addWidget(self._cloud_model, 1)
        self._cloud_refresh = QPushButton("רענן מודלים")
        self._cloud_refresh.clicked.connect(lambda: self._cloud_async("catalog"))
        model_row.addWidget(self._cloud_refresh)
        self._cloud_steps = box
        options.addWidget(self._cloud_model_row)
        for combo in (self._cloud_account, self._cloud_browser, self._cloud_model):
            theme.bind_style(combo, _combo_qss, self.p)

        self._cloud_probe = QPushButton("בדוק חיבור ועיבוד")
        self._cloud_probe.setToolTip("שולח משפט דוגמה במסגרת מכסת החשבון")
        self._cloud_probe.clicked.connect(lambda: self._cloud_async("probe"))
        options.addWidget(self._cloud_probe)
        self._cloud_probe_label = self._hint("")
        self._cloud_probe_label.setTextFormat(Qt.PlainText)
        options.addWidget(self._cloud_probe_label)
        self._cloud_usage = QPushButton("ניהול שימוש והרשאות ב־ChatGPT")
        self._cloud_usage.clicked.connect(lambda: self.ui.chatgpt_action("usage"))
        options.addWidget(self._cloud_usage)
        box.addWidget(self._cloud_options)
        self._cloud_timer = QTimer(self)
        self._cloud_timer.timeout.connect(self._refresh_cloud)
        self._cloud_timer.start(1500)
        self._refresh_cloud()

    def _open_cloud_setup(self):
        self._goto(2, from_nav=False)
        self._cloud_panel.set_open(True)
        model_action = self._cloud_model if self._cloud_model.count() > 1 else self._cloud_refresh
        target = next((widget for widget in (
            self._cloud_cancel, self._cloud_relogin, self._cloud_switch, model_action,
            self._cloud_login) if widget.isVisible() and widget.isEnabled()),
            self._cloud_options.header)
        target.setFocus()
        # Expansion relayouts the scroll area on the next Qt event cycle.
        area = self.stack.widget(2)
        QTimer.singleShot(0, area, lambda: area.ensureWidgetVisible(target))

    def _refresh_cloud(self, status=None):
        if not self.ui.cloud_supported:
            return
        self._refresh_scan_controls(status)
        if self._cloud_busy:
            return
        st = status or self.ui.chatgpt_status()
        self.mode_selector.update_status(st, supported=self.ui.cloud_supported,
            busy=self.ui._overlay.state in ("transcribing", "polishing"))
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
        self._cloud_relogin.setVisible(bool(reconnect or (connected and (not st.get("eligible") or st.get("session_expired")))))
        self._cloud_relogin.setEnabled(bool(st.get("active")))
        self._cloud_logout.setVisible(connected)
        self._cloud_logout.setEnabled(connected)
        self._cloud_model.setEnabled(connected and st.get("eligible", False))
        self._cloud_refresh.setEnabled(connected)
        model_ready = st.get("model") in {m["slug"] for m in st.get("models", [])}
        ready = (connected and st.get("eligible", False) and model_ready
                 and error not in ("authorization", "identity", "permission", "ineligible", "storage"))
        enabled = st.get("enabled") is True
        needs_model = connected and st.get("eligible", False) and (
            not model_ready or st.get("catalog_error") or error == "unsupported")
        destination = self._cloud_steps if needs_model else self._cloud_options.content
        if destination.indexOf(self._cloud_model_row) < 0:
            position = destination.indexOf(self._cloud_options) if needs_model else 0
            destination.insertWidget(position, self._cloud_model_row)
        self._cloud_model_row.setVisible(bool(connected and st.get("eligible")))
        self._cloud_switch_label.setVisible(bool(ready or enabled))
        self._cloud_switch.setVisible(bool(ready or enabled))
        self._cloud_login.setVisible(not connected and not (reconnect and st.get("active")))
        self._cloud_add.setVisible(bool(connected or reconnect))
        self._cloud_probe.setVisible(enabled)
        self._cloud_probe_label.setVisible(bool(enabled and st.get("probe")))
        self._cloud_usage.setVisible(bool(connected or error == "quota"))
        title = "עריכת טקסט עם ChatGPT · " + ("פעילה" if enabled else "כבויה")
        if title != self._cloud_panel.header.accessibleName():
            self._cloud_panel.set_title(title)
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
            if st.get("session_expired"):
                connection = "מצב חיבור: חשבון ChatGPT שמור"
                detail += "\nממתין לחידוש הרשאה במצב ענן; ניתן גם לחדש התחברות ידנית."
        else:
            connection = "מצב חיבור: לא מחובר ל־ChatGPT"
            detail = "אחרי הלחיצה משלימים את החיבור בדפדפן."
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
        self._cloud_connection_detail.setVisible(bool(connected or reconnect or error or signin_error or st.get("signing_in")))
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
            text += "\nאפשר להפעיל עריכה במתג למטה."
        if st.get("refreshing"):
            text += "\nמחדש הרשאה לחשבון…"
        elif st.get("session_expired") and connected:
            text += "\nהחשבון שמור; ההרשאה תחודש בבחירת מצב ענן."
        if st.get("catalog_error"):
            text += "\nרשימת המודלים לא נטענה. לחץ רענן מודלים."
        self._cloud_probe_label.setText(st.get("probe") or "טרם בוצעה בדיקת עיבוד למודל שנבחר.")
        self._cloud_probe.setEnabled(bool(st.get("enabled")) and not self._cloud_busy)
        self._cloud_status.setText(text)
        self._cloud_status.setVisible(bool(error or st.get("catalog_error") or st.get("refreshing") or connected and not ready))
        self._cloud_login.setToolTip("הוסף חשבון ChatGPT" if st.get("accounts") else "התחבר עם חשבון ChatGPT אישי")

    def _cloud_toggle(self, on):
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
        if action not in ("catalog", "probe"):
            self.ui.chatgpt_action("enable", False)
        self._cloud_busy = True
        for widget in (self._cloud_login, self._cloud_add, self._cloud_relogin, self._cloud_logout,
                       self._cloud_account, self._cloud_model, self._cloud_refresh, self._cloud_switch, self._cloud_probe):
            widget.setEnabled(False)
        self._cloud_switch.setEnabled(action in ("catalog", "probe") and self.ui.chatgpt_status().get("enabled", False))
        self._cloud_browser.setEnabled(False)
        if action in ("login", "relogin"):
            self._cloud_connection_detail.show()
            self._cloud_connection.setText("מצב חיבור: ממתין לאישור בדפדפן…")
            self._cloud_connection_detail.setText("השלם את ההתחברות והאישור בדפדפן. החיבור יופיע כאן לאחר שהאישור יתקבל.")
        self._cloud_cancel.setVisible(action in ("login", "relogin"))
        self._cloud_status.show()
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
        for widget in (self._cloud_login, self._cloud_add, self._cloud_relogin, self._cloud_logout,
                       self._cloud_account, self._cloud_model, self._cloud_refresh, self._cloud_switch, self._cloud_probe):
            widget.setEnabled(True)
        self._refresh_cloud(result)
        message = result.get("message", "")
        if message:
            # Successful sign-in acknowledges ChatGPT plan use explicitly while
            # keeping the separate opt-in switch off.
            if result.get("connected") and result.get("needs_welcome") and message.startswith("החשבון מחובר"):
                box = QMessageBox(self)
                box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
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
        card.set_open = set_open
        return card, body_box


    def _on_hotkey_captured(self, combo):
        if self.ui.set_hotkey(combo):
            self._hk_edit.accept_combo(combo)
            self._hk_label.setText(shortcut_label(combo))
            self._hk_change.setChecked(False)
            self._hk_change.setFocus()
            self._toast.show_message("קיצור ההקלטה עודכן", msec=3000)
        else:
            QMessageBox.warning(self, "MyWhisper", "הקיצור אינו זמין. אפשר לבחור צירוף אחר.")

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
        state = (st or {}).get("state")
        text = {
            "ready": "מוכן להכתבה",
            "released": "אפשר להכתיב. ההכנה תתחיל בלחיצה על הקיצור.",
            "loading": "מכין את ההכתבה…",
            "paused": "ההכתבה מושהית בזמן מסך מלא",
            "needs_download": "כדי להתחיל להכתיב, יש להשלים את הכנת ה־Mac.",
            "error": ("ההכנה נכשלה. אפשר לנסות שוב בהכנת ה־Mac." if IS_MAC else
                      "ההכנה נכשלה. לחצו שוב על קיצור ההקלטה כדי לנסות מחדש."),
        }.get(state, "")
        setup_hint = (st or {}).get("setup_hint")
        if setup_hint and state in ("ready", "released"):
            text = setup_hint
        self._status_lbl.setText(text)
        self._status_lbl.setVisible(bool(text))
        self._status_help.setText("הכנת ה־Mac" if IS_MAC else "עזרה")
        self._status_help.setVisible(bool(setup_hint) or state in ("error", "needs_download"))
        st = st or {}
        device = {"metal": "Apple GPU / Metal", "cuda": "NVIDIA GPU", "cpu": "CPU"}.get(st.get("device"))
        details = [f"מודל התמלול: {st['model']}"] if st.get("model") else []
        if device:
            details.append(f"מעבד התמלול: {device}")
        if st.get("fallback"):
            details.append("התמלול פועל על המעבד כי ההאצה הגרפית אינה זמינה.")
        self._status_sub.setText("\n".join(details) or "פרטי התמלול עדיין אינם זמינים.")

    def _open_status_help(self):
        if IS_MAC:
            self.ui.open_mac_setup()
        else:
            self._help_panel.set_open(True)
            self._technical.set_open(True)
            self._technical.header.setFocus()
            area = self.stack.widget(2)
            QTimer.singleShot(0, area, lambda: area.ensureWidgetVisible(self._status_sub))

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
        if IS_MAC:
            if self.ui.do_update():
                QMessageBox.information(self, "עדכון בטא ל־Mac", "הורד את קובץ ה־DMG מהגרסה שנפתחה, סגור את MyWhisper והחלף את האפליקציה ב־Applications. הנתונים נשמרים בנפרד.")
            return
        if self.ui.do_update():
            self._show_update_stage("מוריד ובודק את הגרסה החדשה…")
        else:
            self._upd_status.setText("האפליקציה עסוקה. נסה שוב אחרי סיום הפעולה.")

    def _show_update_stage(self, text, percent=-1, failed=False):
        self._upd_status.setText(f"{text} {percent}%" if percent >= 0 else text)
        self._upd_progress.setRange(0, 100 if percent >= 0 else 0)
        if percent >= 0:
            self._upd_progress.setValue(percent)
        self._upd_progress.setVisible(not failed)
        self._upd_btn.setEnabled(failed)
        self._upd_now_btn.setVisible(failed)


    def _populate_mics(self):
        self._mic_combo.blockSignals(True)
        self._mic_combo.clear()
        self._mic_combo.addItem("ברירת מחדל של המערכת", "")
        devices = self.ui.list_input_devices()
        for name in devices:
            self._mic_combo.addItem(name, name)
        current = self.ui.config.get("input_device", "")
        idx = self._mic_combo.findData(current)
        if current and idx < 0:
            self._mic_combo.addItem(f"{current} — לא מחובר", current)
            idx = self._mic_combo.count() - 1
        self._mic_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._mic_combo.blockSignals(False)
        missing = bool(current and current not in devices)
        self._missing_mic = current if missing else ""
        self._mic_warning.setVisible(missing)
        if missing and self._mic_testing:
            self._stop_mic_test()


    def _on_mic_changed(self, _idx):
        if self._mic_testing:
            self._stop_mic_test()  # the old device stream is stale now
        self.ui.set_input_device(self._mic_combo.currentData() or "")
        self._mic_warning.setVisible(bool(self._missing_mic) and self._mic_combo.currentData() == self._missing_mic)


    def _toggle_mic_test(self):
        if self._mic_testing:
            self._stop_mic_test()
            return
        device = self._mic_combo.currentData() or ""
        if not self.ui.mic_test_start(device):
            self._populate_mics()
            QMessageBox.warning(self, "MyWhisper",
                                "לא ניתן לפתוח את המיקרופון הזה. בדקו שהוא מחובר או בחרו מיקרופון אחר.")
            return
        self._mic_testing = True
        self._mic_level.show()
        self._mic_status.show()
        self._mic_detected = False
        self._mic_test_btn.setText("עצור בדיקה")
        self._mic_status.setText("אפשר לדבר עכשיו")
        self._mic_timer.start(50)


    def _stop_mic_test(self):
        self._mic_timer.stop()
        self.ui.mic_test_stop()
        self._mic_testing = False
        self._mic_test_btn.setText("בדיקת מיקרופון")
        self._mic_status.hide()
        self._mic_level.hide()
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
                                 else "אפשר לדבר עכשיו")


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
