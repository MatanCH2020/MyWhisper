"""History page behavior for the Qt shell; backends are injected."""
import html


from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSizeGrip, QVBoxLayout, QWidget

import icons
import theme


class HistoryPageMixin:
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
        from ui import HistoryCard
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
                                self._fmt_time(e.get("time", "")), e.get("original_text")))
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
        from ui import HISTORY_PAGE
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
        theme.bind_style(title, lambda _theme_palette: f"color:{_theme_palette['text']};", p)
        v.addWidget(title)
        v.addSpacing(6)

        hk = (self.ui.config.get("hotkey", "ctrl+space") or "").upper()
        sub = QLabel(f"הקיצור שלך: <b style='color:{p['accent']}'>{html.escape(hk)}</b>")
        sub.setTextFormat(Qt.RichText)
        sub.setAlignment(Qt.AlignCenter)
        theme.bind_style(sub, lambda _theme_palette: f"color:{_theme_palette['text_muted']}; font-size:13px;", p)
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
        tip = QLabel("תמלול מקומי כברירת מחדל. עריכת טקסט בענן — לבחירתך.")
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
        from ui import HISTORY_PAGE
        self._page_limit += HISTORY_PAGE
        self.refresh_history()


    def prepend_transcription(self):
        """Show a just-finished transcription without rebuilding the whole list.

        A full refresh_history() costs hundreds of ms for a long history, and
        this runs right after every dictation. Falls back to a full refresh when
        a search filter is active (the new entry may not match) or when the list
        is not in its plain state."""
        from ui import HistoryCard
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
                           self._fmt_time(e.get("time", "")), e.get("original_text")))
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


    def show_original(self, entry_id):
        entry = next((e for e in self.ui.get_history() if e.get("id") == entry_id), {})
        source = entry.get("original_text")
        if not isinstance(source, str):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("התמלול המקומי — לפני עריכת OpenAI")
        dialog.resize(640, 420)
        box = QVBoxLayout(dialog)
        box.addWidget(self._hint("התמלול לאחר מילון התיקונים, לפני העריכה בענן."))
        editor = QPlainTextEdit(source)
        editor.setReadOnly(True)
        box.addWidget(editor, 1)
        row = QHBoxLayout()
        copy = QPushButton("העתק מקור")
        copy.clicked.connect(lambda: self.copy_text(source))
        row.addWidget(copy)
        close = QPushButton("סגור")
        close.clicked.connect(dialog.accept)
        row.addWidget(close)
        row.addWidget(QSizeGrip(dialog))
        box.addLayout(row)
        dialog.exec()

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
        from ui import CorrectionDialog
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
            if self.ui.add_correction(w, new) is False:
                return False
            saved = self.ui.update_history(entry_id, self.ui.apply_corrections(text))
            self._invalidate_cards()
            self.refresh_history()
            self.refresh_dict()
            return saved

        def on_approve(w):
            if self.ui.approve_word(w) is False:
                return False
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
