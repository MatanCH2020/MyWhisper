"""Dictionary page behavior for the Qt shell; backends are injected."""
import html
import threading
import theme


from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QProgressBar, QVBoxLayout, QWidget

import icons


class DictionaryPageMixin:
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

        self._scan_busy = False
        scan = QFrame()
        scan.setObjectName("card")
        scan_box = QVBoxLayout(scan)
        scan_box.setContentsMargins(14, 12, 14, 12)
        scan_box.addWidget(self._section("תיקון חכם של ההיסטוריה עם ChatGPT"))
        explanation = QLabel("סריקה לפי המשפט המלא: תיקון כתיב בעברית וכתיבת מונחים לועזיים באנגלית. "
            "תיקונים עקביים יילמדו להכתבות הבאות; תיקוני הקשר יישארו במשפט בלבד. "
            "לחיצה תשלח את היסטוריית התמלולים ל־OpenAI דרך החשבון והמודל שבחרת. "
            "הסריקה ידנית ומשתמשת במכסת החשבון; האודיו והיסטוריית ההעתקות אינם נשלחים.")
        explanation.setWordWrap(True)
        explanation.setObjectName("cardtext")
        scan_box.addWidget(explanation)
        self._scan_status = QLabel("להפעלת הסריקה, חבר חשבון והפעל עריכת טקסט בהגדרות.")
        self._scan_status.setWordWrap(True)
        self._scan_status.setObjectName("fieldlabel")
        scan_box.addWidget(self._scan_status)
        self._scan_bar = QProgressBar()
        self._scan_bar.setVisible(False)
        scan_box.addWidget(self._scan_bar)
        scan_buttons = QHBoxLayout()
        self._scan_start = self._accent_btn("סרוק ותקן את ההיסטוריה", self._start_history_scan)
        self._scan_cancel = QPushButton("בטל סריקה")
        self._scan_cancel.clicked.connect(self._cancel_history_scan)
        self._scan_cancel.hide()
        self._scan_undo = QPushButton("בטל את השינויים מהסריקה האחרונה")
        self._scan_undo.clicked.connect(self._undo_history_scan)
        self._scan_undo.setToolTip("מבטל את הסריקה האחרונה גם לאחר הפעלה מחדש, תוך שמירת עריכות חדשות.")
        scan_buttons.addWidget(self._scan_start)
        scan_buttons.addWidget(self._scan_cancel)
        scan_buttons.addWidget(self._scan_undo)
        scan_box.addLayout(scan_buttons)
        box.addWidget(scan)
        self._refresh_scan_controls()

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
        box.addWidget(self._section("תיקונים שנלמדו"))
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

    def _refresh_scan_controls(self):
        st = self.ui.chatgpt_status()
        ready = (st.get("enabled") is True and st.get("connected") and st.get("eligible")
                 and st.get("model") in {m["slug"] for m in st.get("models", [])})
        self._scan_start.setEnabled(bool(ready) and not self._scan_busy)
        self._scan_undo.setEnabled(not self._scan_busy and self.ui.can_undo_history_scan())
        self._scan_cancel.setVisible(self._scan_busy)
        self._scan_start.setToolTip("" if ready else "חבר חשבון זכאי, בחר מודל והפעל עריכת טקסט בהגדרות.")
        if not self._scan_busy and not getattr(self, "_scan_has_result", False):
            self._scan_status.setText("מוכן לסריקת כל היסטוריית התמלולים. המקור יישמר ואפשר לבטל את השינויים."
                                      if ready else "להפעלת הסריקה, חבר חשבון והפעל עריכת טקסט בהגדרות.")

    def _start_history_scan(self):
        if self._scan_busy:
            return
        self._scan_busy = True
        self._scan_status.setText("מכין את הסריקה… השינויים יישמרו רק אחרי השלמת הסריקה.")
        self._scan_bar.setRange(0, 0)
        self._scan_bar.show()
        self._refresh_scan_controls()
        def work():
            try:
                result = self.ui.scan_history(self._scan_progress.emit)
            except Exception:
                result = None
            self._scan_result.emit(result)
        threading.Thread(target=work, daemon=True).start()

    def _on_scan_progress(self, done, total):
        self._scan_bar.setRange(0, max(1, total))
        self._scan_bar.setValue(done)
        self._scan_status.setText(f"בודק היסטוריה… {done} מתוך {total} קבוצות. אפשר להמשיך להשתמש באפליקציה.")

    def _cancel_history_scan(self):
        self.ui.cancel_history_scan()
        self._scan_status.setText("מבטל סריקה…")

    def _on_scan_result(self, result):
        self._scan_busy = False
        self._scan_has_result = True
        self._scan_bar.hide()
        if result and result.status in {"completed", "partial_storage"}:
            text = (f"הסריקה הסתיימה: נבדקו {result.scanned} תמלולים; תוקנו {result.corrected}. "
                    f"נלמדו {result.learned} תיקונים ונוספו {result.english} מונחים באנגלית.")
            if result.skipped:
                text += f" {result.skipped} רשומות ששונו במהלך הסריקה נשמרו ללא דריסה."
            if result.status == "partial_storage":
                text += " חלק מתיקוני המילון לא נשמרו. בדוק את הודעת השמירה; ניתן לבטל את השינויים שנשמרו."
            self._invalidate_cards()
            self.refresh_history()
            self.refresh_dict()
        elif result and result.status == "undone":
            text = f"השינויים בוטלו. שוחזרו {result.corrected} תמלולים; עריכות חדשות נשמרו."
            self._invalidate_cards()
            self.refresh_history()
            self.refresh_dict()
        else:
            text = {"cancelled": "הסריקה בוטלה. לא נשמרו שינויים.",
                    "disabled": "העריכה בענן כבויה. לא נשמרו שינויים.",
                    "empty": "אין היסטוריה לסריקה או שינויים לביטול.",
                    "busy": "כבר מתבצעת סריקה. יש להמתין לסיום.",
                    "timeout": "הסריקה חרגה מזמן ההמתנה. לא נשמרו שינויים; אפשר לנסות שוב.",
                    "quota": "מכסת החשבון אינה זמינה. לא נשמרו שינויים; בדוק ניהול שימוש.",
                    "authorization": "ההרשאה אינה זמינה. לא נשמרו שינויים; בדוק את החיבור בהגדרות.",
                    "ineligible": "החשבון אינו זכאי לסריקה. לא נשמרו שינויים.",
                    "unsupported": "המודל אינו תומך בסריקה זו. לא נשמרו שינויים; בחר מודל אחר.",
                    "storage": "לא ניתן להשלים שמירה או ביטול. בדוק את הודעת השמירה; המקור נשמר.",
                    "invalid": "המודל החזיר תיקון שלא ניתן לאמת מול המקור. לא נשמרו שינויים.",
                    "incomplete": "לא התקבלה תשובה מלאה. לא נשמרו שינויים."}.get(
                        getattr(result, "status", ""), "הסריקה לא הושלמה. לא נשמרו שינויים; אפשר לנסות שוב.")
        self._scan_status.setText(text)
        self._refresh_scan_controls()
        self._toast.show_message(text, msec=8000)

    def _undo_history_scan(self):
        self._on_scan_result(self.ui.undo_history_scan())


    def _line_edit(self, placeholder):
        e = QLineEdit()
        e.setPlaceholderText(placeholder)
        theme.bind_style(e, lambda _theme_palette: f"QLineEdit{{background:{_theme_palette['surface']}; color:{_theme_palette['text']};"
            f" border:1px solid {_theme_palette['border']}; border-radius:8px;"
            f" padding:6px 10px; font-size:13px;}}"
            f"QLineEdit:focus{{border-color:{_theme_palette['accent']};}}", self.p)
        return e


    def _accent_btn(self, text, cb):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        theme.bind_style(b, lambda _theme_palette: f"QPushButton{{background:{_theme_palette['accent']}; color:{_theme_palette['on_accent']};"
            f" border:none; border-radius:8px; padding:6px 16px;"
            f" font-size:13px; font-weight:600;}}"
            f"QPushButton:hover{{background:{_theme_palette['accent_hover']};}}"
            f"QPushButton:disabled{{background:{_theme_palette['surface']};color:{_theme_palette['text_muted']};"
            f"border:1px solid {_theme_palette['border']};}}"
            f"QPushButton:focus{{border:2px solid {_theme_palette['on_accent']};padding:4px 14px;}}", self.p)
        b.clicked.connect(lambda: cb())
        return b


    def _delete_icon_btn(self, on_delete):
        x = QPushButton()
        x.setProperty("variant", "icon")
        x.setFixedSize(34, 32)
        x.setIcon(icons.icon("trash", self.p["danger"], 17))
        x.setToolTip("מחק")
        x.setCursor(Qt.PointingHandCursor)
        x.clicked.connect(lambda _=False: on_delete())
        return x


    def _kv_card(self, text, on_delete):
        card = QFrame()
        card.setObjectName("card")
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 9, 14, 9)
        h.setSpacing(10)
        h.addWidget(self._delete_icon_btn(on_delete))
        h.addStretch(1)
        lbl = QLabel(text)
        theme.bind_style(lbl, lambda _theme_palette: f"color:{_theme_palette['text']}; font-size:14px;", self.p)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        h.addWidget(lbl)
        return card


    def _corr_value(self, title, value):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        cap = QLabel(title)
        cap.setObjectName("hint")
        v.addWidget(cap)
        val = QLabel(f"<span dir=\"auto\">{html.escape(value)}</span>")
        val.setTextFormat(Qt.RichText)
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        theme.bind_style(val, lambda _theme_palette: f"color:{_theme_palette['text']}; font-size:14px; font-weight:600;", self.p)
        v.addWidget(val)
        return w


    def _corr_card(self, wrong, right, on_delete):
        card = QFrame()
        card.setObjectName("card")
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(12)
        h.addWidget(self._delete_icon_btn(on_delete))
        h.addStretch(1)
        h.addWidget(self._corr_value("נשמע", wrong), 1)
        arrow = QLabel("←")
        theme.bind_style(arrow, lambda _theme_palette: f"color:{_theme_palette['text_muted']}; font-size:18px; font-weight:700;", self.p)
        h.addWidget(arrow)
        h.addWidget(self._corr_value("ייכתב", right), 1)
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
                    self._corr_card(wrong, right,
                                    lambda k=wrong: self._del_corr(k)))


    def _add_eng(self):
        term = self._eng_input.text().strip()
        if not term:
            return
        if self.ui.add_english_term(term) is False:
            return
        self._invalidate_cards()
        self._eng_input.clear()
        self.refresh_dict()


    def _del_eng(self, term):
        self.ui.remove_english_term(term)
        self._invalidate_cards()
        self.refresh_dict()


    def _add_corr_manual(self):
        wrong = self._corr_wrong.text().strip()
        right = self._corr_right.text().strip()
        if not wrong or not right:
            return
        if self.ui.add_correction(wrong, right) is False:
            return
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
