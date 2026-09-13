"""Dictionary page behavior for the Qt shell; backends are injected."""
import html


from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

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
            f"QPushButton:focus{{border:2px solid {self.p['on_accent']};padding:4px 14px;}}"
        )
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
        lbl.setStyleSheet(f"color:{self.p['text']}; font-size:14px;")
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
        val.setStyleSheet(f"color:{self.p['text']}; font-size:14px; font-weight:600;")
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
        arrow.setStyleSheet(
            f"color:{self.p['text_muted']}; font-size:18px; font-weight:700;")
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
