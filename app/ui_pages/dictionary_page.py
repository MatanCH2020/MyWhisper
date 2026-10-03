"""Dictionary page behavior for the Qt shell; backends are injected."""
import html
import threading
import time
import theme


from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

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
        self._scan_status.setObjectName("scanstatus")
        self._scan_status.setTextFormat(Qt.PlainText)
        self._scan_status.setAccessibleName("מצב סריקת ההיסטוריה")
        scan_box.addWidget(self._scan_status)
        self._scan_activity = QLabel("")
        self._scan_activity.setWordWrap(True)
        self._scan_activity.setTextFormat(Qt.PlainText)
        self._scan_activity.setObjectName("fieldlabel")
        scan_box.addWidget(self._scan_activity)
        self._scan_summary = QWidget()
        stats = QGridLayout(self._scan_summary)
        stats.setContentsMargins(0, 4, 0, 4)
        self._scan_stats = {}
        for index, (key, title) in enumerate((("checked", "תמלולים שנבדקו"), ("corrected", "תמלולים שתוקנו"),
                                             ("learned", "תיקונים שנלמדו"), ("english", "מונחים באנגלית שנוספו"))):
            label = QLabel(title + ": 0")
            label.setObjectName("fieldlabel")
            label.setWordWrap(True)
            self._scan_stats[key] = label
            stats.addWidget(label, index // 2, index % 2)
        self._scan_summary.hide()
        scan_box.addWidget(self._scan_summary)
        self._scan_note = QLabel("")
        self._scan_note.setTextFormat(Qt.PlainText)
        self._scan_note.setWordWrap(True)
        self._scan_note.setObjectName("cardtext")
        scan_box.addWidget(self._scan_note)
        self._scan_bar = QProgressBar()
        self._scan_bar.setObjectName("scanprogress")
        self._scan_bar.setAccessibleName("התקדמות סריקת התמלולים")
        self._scan_bar.setVisible(False)
        scan_box.addWidget(self._scan_bar)
        scan_buttons = QHBoxLayout()
        self._scan_start = self._accent_btn("סרוק ותקן את ההיסטוריה", self._start_history_scan)
        self._scan_cancel = QPushButton("בטל סריקה")
        self._scan_cancel.clicked.connect(self._cancel_history_scan)
        self._scan_cancel.hide()
        self._scan_undo = QPushButton("בטל את התיקונים שנשמרו")
        self._scan_undo.clicked.connect(self._undo_history_scan)
        self._scan_undo.setToolTip("מבטל את הסריקה האחרונה שנשמרה, גם לאחר הפעלה מחדש, תוך שמירת עריכות חדשות.")
        self._scan_details = QPushButton("הצג תוצאות")
        self._scan_details.clicked.connect(self._show_scan_details)
        scan_buttons.addWidget(self._scan_start)
        scan_buttons.addWidget(self._scan_cancel)
        scan_box.addLayout(scan_buttons)
        scan_secondary = QHBoxLayout()
        scan_secondary.addWidget(self._scan_details)
        scan_secondary.addWidget(self._scan_undo)
        scan_box.addLayout(scan_secondary)
        self._scan_elapsed_timer = QTimer(self)
        self._scan_elapsed_timer.setInterval(1000)
        self._scan_elapsed_timer.timeout.connect(self._update_scan_activity)
        self._scan_report = {}
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
        report = self.ui.history_scan_report()
        if report:
            self._on_scan_result(report, notify=False, refresh=False)
        return w

    def _refresh_scan_controls(self):
        st = self.ui.chatgpt_status()
        ready = (st.get("enabled") is True and st.get("connected") and st.get("eligible")
                 and st.get("model") in {m["slug"] for m in st.get("models", [])})
        self._scan_start.setEnabled(bool(ready) and not self._scan_busy)
        self._scan_undo.setEnabled(not self._scan_busy and self.ui.can_undo_history_scan())
        self._scan_cancel.setVisible(self._scan_busy)
        self._scan_start.setText("סורק…" if self._scan_busy else "סרוק ותקן את ההיסטוריה")
        self._scan_details.setEnabled(bool(self._scan_report) and not self._scan_busy)
        self._scan_start.setToolTip("" if ready else "חבר חשבון זכאי, בחר מודל והפעל עריכת טקסט בהגדרות.")
        if not self._scan_busy and not getattr(self, "_scan_has_result", False):
            self._scan_status.setText("מוכן לסריקת כל היסטוריית התמלולים. המקור יישמר ואפשר לבטל את השינויים."
                                      if ready else "להפעלת הסריקה, חבר חשבון והפעל עריכת טקסט בהגדרות.")

    def _start_history_scan(self):
        if self._scan_busy:
            return
        self._scan_busy = True
        self._scan_cancelling = False
        self._scan_started = time.monotonic()
        self._scan_phase_started = self._scan_started
        self._scan_progress_state = {"phase": "preparing", "checked": 0, "total": 0}
        ticket = self.ui.prepare_history_scan()
        self._scan_status.setText("מכין את הסריקה… השינויים יישמרו רק אחרי השלמת הסריקה.")
        self._scan_activity.setText("קורא את ההיסטוריה ומכין קבוצות לבדיקה.")
        self._scan_note.setText("התיקונים יישמרו רק לאחר סיום תקין. אפשר להמשיך לעבוד או לבטל את הסריקה.")
        self._scan_summary.hide()
        self._scan_cancel.setEnabled(True)
        self._scan_bar.setRange(0, 0)
        self._scan_bar.show()
        self._refresh_scan_controls()
        self._scan_elapsed_timer.start()
        def work():
            try:
                result = self.ui.scan_history(self._scan_progress.emit, ticket)
            except Exception:
                result = None
            self._scan_result.emit(result)
        threading.Thread(target=work, daemon=True).start()

    def _on_scan_progress(self, update):
        if not self._scan_busy:
            return
        previous = self._scan_progress_state
        if (update.get("phase"), update.get("batch")) != (previous.get("phase"), previous.get("batch")):
            self._scan_phase_started = time.monotonic()
        self._scan_progress_state = dict(update)
        self._scan_bar.setRange(0, max(1, update.get("total", 0)))
        self._scan_bar.setValue(update.get("checked", 0))
        self._update_scan_activity()

    def _update_scan_activity(self):
        if not self._scan_busy:
            return
        state = self._scan_progress_state
        elapsed = int(time.monotonic() - self._scan_started)
        waiting = int(time.monotonic() - self._scan_phase_started)
        checked, total = state.get("checked", 0), state.get("total", 0)
        if self._scan_cancelling:
            title = "מבטל סריקה… לא יוחלו תיקונים מהסריקה הזו."
        else:
            title = {"preparing": "מכין את היסטוריית התמלולים לבדיקה…",
                     "analyzing": f"ממתין לתשובת ChatGPT — קבוצה {state.get('batch', 0)} מתוך {state.get('batches', 0)}",
                     "validating": "מאמת את התיקונים מול המקור…",
                     "saving": "שומר את התיקונים והמילון…"}.get(state.get("phase"), "סורק…")
        self._scan_status.setText(title)
        activity = f"נבדקו {checked} מתוך {total} תמלולים · זמן שחלף {elapsed // 60}:{elapsed % 60:02d}"
        if state.get("phase") == "analyzing" and not self._scan_cancelling:
            activity += f" · ממתין {waiting} שניות (עד {int(state.get('deadline', 45))} לקבוצה)"
        self._scan_activity.setText(activity)
        self._scan_note.setText(f"נמצאו עד כה {state.get('found', 0)} תיקונים מאומתים; "
                               f"נדחו {state.get('rejected', 0)} הצעות. השינויים עדיין לא נשמרו.")

    def _cancel_history_scan(self):
        self._scan_cancelling = True
        self._scan_cancel.setEnabled(False)
        self.ui.cancel_history_scan()
        self._scan_status.setText("מבטל סריקה…")

    def _on_scan_result(self, result, *, notify=True, refresh=True):
        result = result if isinstance(result, dict) else vars(result) if result else {"status": "internal"}
        status = result.get("status")
        self._scan_busy = False
        self._scan_has_result = True
        self._scan_elapsed_timer.stop()
        self._scan_report = result
        self._scan_bar.hide()
        self._scan_summary.setVisible(status != "undone")
        checked = result.get("scanned", 0)
        corrected = result.get("corrected", 0)
        total = result.get("total", checked)
        for key, count in (("checked", f"{checked} מתוך {total}"), ("corrected", corrected),
                           ("learned", result.get("learned", 0)), ("english", result.get("english", 0))):
            titles = {"checked": "תמלולים שנבדקו", "corrected": "תמלולים שתוקנו",
                      "learned": "תיקונים שנלמדו", "english": "מונחים באנגלית שנוספו"}
            self._scan_stats[key].setText(f"{titles[key]}: {count}")
        note = ""
        if status in {"completed", "partial_storage"}:
            text = f"הסריקה הושלמה — נבדקו {checked} תמלולים; תוקנו {corrected}."
            note = ("לחץ על ‘הצג תוצאות’ כדי לראות מה השתנה ומה נלמד. המקור נשמר בהיסטוריה."
                    if corrected else "לא נמצאו תיקונים מאומתים להחלה. ההיסטוריה והמילון נשארו ללא שינוי.")
            if corrected and not result.get("learned"):
                note += " לא נוספו כללי החלפה חדשים; למילון מתווספים רק תיקונים עקביים למילים שאינן מוכרות."
            if status == "partial_storage":
                note += " חלק מהשמירה לא הושלם. בדוק את הודעת השמירה; ניתן לבטל את השינויים שנשמרו."
        elif status == "undone":
            text = f"השינויים בוטלו — שוחזרו {corrected} תמלולים."
            note = "תוספות הסריקה הוסרו מהמילון; עריכות חדשות נשמרו."
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
                    "invalid": "הסריקה נעצרה: מבנה תשובת המודל אינו תקין. לא נשמרו שינויים.",
                    "incomplete": "לא התקבלה תשובה מלאה. לא נשמרו שינויים."}.get(
                        status, "הסריקה לא הושלמה. לא נשמרו שינויים; אפשר לנסות שוב.")
            note = f"נבדקו {checked} מתוך {total} תמלולים לפני העצירה. אין להסתמך על הסריקה כסריקה מלאה."
        rejected = result.get("rejected", 0)
        if rejected:
            explanations = {"source": "קטע שלא נמצא במקור", "ambiguous": "קטע שמופיע יותר מפעם אחת",
                            "protected": "שינוי מספר או שלילה", "overlap": "תיקונים חופפים",
                            "english": "מונח באנגלית שאינו תקין", "schema": "הצעה במבנה לא תקין",
                            "format": "תיקון שאינו תקין"}
            reasons = " · ".join(f"{explanations.get(key, 'תיקון שלא אומת')}: {count}"
                                 for key, count in result.get("reasons", {}).items())
            note += f"\nנדחו {rejected} הצעות בלי לעצור את שאר הסריקה. {reasons}"
        if result.get("skipped"):
            note += f"\n{result['skipped']} רשומות ששונו בינתיים לא נדרסו."
        if result.get("report_saved") is False:
            note += "\nלא ניתן לשמור את הסיכום להפעלה הבאה."
        seconds = result.get("elapsed_ms", 0) // 1000
        self._scan_activity.setText(f"{result.get('finished_at', '')} · זמן סריקה {seconds // 60}:{seconds % 60:02d}"
                                    + (f" · מודל {result['model']}" if result.get("model") else ""))
        self._scan_status.setText(text)
        self._scan_note.setText(note)
        if refresh and status in {"completed", "partial_storage", "undone"}:
            self._invalidate_cards()
            self.refresh_history()
            self.refresh_dict()
        self._refresh_scan_controls()
        if notify:
            self._toast.show_message(text, msec=8000)
            if not self.isVisible():
                self.ui.notify("MyWhisper — סריקת היסטוריה", text)

    def _show_scan_details(self):
        self._scan_details_dialog().exec()

    def _scan_details_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("תוצאות סריקת ההיסטוריה")
        dialog.resize(820, 520)
        dialog.setMinimumSize(640, 360)
        theme.bind_style(dialog, lambda p: f"QDialog{{background:{p['bg']};}} "
            f"QTableWidget{{background:{p['surface']};color:{p['text']};gridline-color:{p['border']};}} "
            f"QHeaderView::section{{background:{p['surface_alt']};color:{p['text']};padding:8px;border:1px solid {p['border']};}}", self.p)
        box = QVBoxLayout(dialog)
        summary = QLabel(self._scan_status.text() + "\n" + self._scan_note.text())
        summary.setTextFormat(Qt.PlainText)
        summary.setWordWrap(True)
        box.addWidget(summary)
        details = self._scan_report.get("details", [])
        table = QTableWidget(len(details), 4)
        table.setHorizontalHeaderLabels(["לפני", "אחרי", "סוג התיקון", "כלל חדש להכתבות הבאות"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().hide()
        for index, item in enumerate(details):
            values = (item.get("before", ""), item.get("after", ""),
                      {"english": "מונח באנגלית", "spelling": "כתיב", "context": "לפי ההקשר"}.get(item.get("kind"), "תיקון"),
                      "נלמד במילון" if item.get("learned") else "ללא כלל חדש")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                table.setItem(index, column, cell)
        for column in range(4):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch if column < 2 else QHeaderView.ResizeToContents)
        table.resizeRowsToContents()
        box.addWidget(table, 1)
        close = QPushButton("סגור")
        close.clicked.connect(dialog.accept)
        box.addWidget(close)
        return dialog

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
