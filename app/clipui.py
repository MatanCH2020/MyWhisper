"""Resizable clipboard browser with full text and zoomable image previews."""
import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCursor, QIcon, QKeySequence, QPixmap, QShortcut, QTextOption
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListView,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizeGrip, QSplitter, QVBoxLayout, QWidget,
)

import clips
import icons
import theme
from widgets import FramelessWindow, TitleBar

log = logging.getLogger("clipui")
ROW_ICON = 64
SEARCH_DEBOUNCE_MS = 120
PAGE_SIZE = 100


class ImagePreview(QScrollArea):
    """Fit large images to the viewport; zoomed images remain scrollable."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.NoFrame)
        self.setAlignment(Qt.AlignCenter)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        self.setWidget(self._label)
        self._pixmap = QPixmap()
        self._zoom = None
        self.setAccessibleName("תצוגה מקדימה של תמונה")

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self._zoom = None
        self._render()

    def fit(self):
        self._zoom = None
        self._render()

    def zoom(self, factor):
        if self._pixmap.isNull():
            return
        self._zoom = max(0.05, min(8.0, self.scale() * factor))
        self._render()

    def scale(self):
        if self._zoom is not None:
            return self._zoom
        if self._pixmap.isNull():
            return 1.0
        return min(self.viewport().width() / self._pixmap.width(),
                   self.viewport().height() / self._pixmap.height())

    def _render(self):
        if self._pixmap.isNull():
            self._label.clear()
            self._label.resize(1, 1)
            return
        scale = max(0.01, self.scale())
        size = QSize(max(1, round(self._pixmap.width() * scale)),
                     max(1, round(self._pixmap.height() * scale)))
        self._label.setPixmap(self._pixmap.scaled(size, Qt.KeepAspectRatio,
                                                Qt.SmoothTransformation))
        self._label.resize(self._label.pixmap().size())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._zoom is None:
            self._render()


class ClipPicker(FramelessWindow):
    """Click selects a preview; Copy / Enter / double-click chooses the clip."""

    def __init__(self, palette, on_pick, on_delete=None, on_clear=None, on_theme=None,
                 on_restore=None, on_pin=None, is_paused=None, on_pause=None, capture_enabled=True, capture_allowed=None):
        super().__init__()
        self.p = palette
        self._on_pick = on_pick
        self._on_delete = on_delete or (lambda cid: None)
        self._on_clear = on_clear or (lambda: None)
        self._on_restore = on_restore or (lambda e, i: False)
        self._on_pin = on_pin or (lambda cid, value: False)
        self._is_paused = is_paused or (lambda: False)
        self._on_pause = on_pause or (lambda paused: None)
        self._capture_enabled = capture_enabled
        self._capture_allowed = capture_allowed or (lambda: True)
        self._kind = "all"
        self._limit = PAGE_SIZE
        self._undo = None
        self._thumb_cache = {}
        self._entries = []
        self._by_id = {}
        self.setWindowTitle("MyWhisper — תמלולים והעתקות")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumSize(720, 460)
        self.resize(1000, 680)
        theme.bind_style(self.container, lambda p:
                         f"#container{{background:{p['bg']};border-radius:14px;}}", palette)
        self.body.addWidget(TitleBar(
            palette, on_theme or (lambda: None), self.showMinimized, self.close,
            on_max=self.toggle_max, title_text="תמלולים והעתקות", close_tip="סגור"))
        content = QWidget()
        self.body.addWidget(content, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 10, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("חיפוש בתמלולים ובהעתקות…")
        self.search.setAccessibleName("חיפוש בתמלולים ובהעתקות")
        self._search_action = self.search.addAction(
            icons.icon("search", palette['text_muted'], 16), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._queue_filter)
        header.addWidget(self.search, 1)
        self._count = QLabel()
        self._count.setObjectName("hint")
        header.addWidget(self._count)
        layout.addLayout(header)

        filters = QHBoxLayout()
        self._filters = QButtonGroup(self)
        self._filter_buttons = {}
        for kind, label in (("all", "הכול"), ("image", "תמונות"), ("text", "טקסט"),
                            ("link", "קישורים"), ("pinned", "מועדפים")):
            button = QPushButton(label)
            button.setObjectName("clipfilter")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, k=kind: self._set_filter(k))
            self._filters.addButton(button)
            self._filter_buttons[kind] = button
            filters.addWidget(button)
        self._filter_buttons["all"].setChecked(True)
        self._gallery_btn = QPushButton("גלריה")
        self._gallery_btn.setObjectName("clipfilter")
        self._gallery_btn.setCheckable(True)
        self._gallery_btn.toggled.connect(self._apply_filter)
        self._gallery_btn.hide()
        filters.addWidget(self._gallery_btn)
        layout.addLayout(filters)
        capture = QHBoxLayout()
        self._capture_status = QLabel()
        self._capture_status.setObjectName("fieldlabel")
        capture.addWidget(self._capture_status, 1)
        self._pause_btn = QPushButton()
        self._pause_btn.clicked.connect(self._toggle_pause)
        self._pause_btn.setEnabled(capture_enabled)
        capture.addWidget(self._pause_btn)
        layout.addLayout(capture)

        self.splitter = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setAccessibleName("רשימת העתקות")
        self.list.setIconSize(QSize(ROW_ICON, ROW_ICON))
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.setMovement(QListView.Static)
        theme.bind_style(self.list, lambda p: f"""
QListWidget {{ background:{p['surface']}; color:{p['text']};
    border:1px solid {p['border']}; border-radius:8px; padding:4px; }}
QListWidget::item {{ padding:8px; border-radius:6px; }}
QListWidget::item:selected {{ background:{p['nav_sel']}; color:{p['text']}; }}
QListWidget::item:hover {{ background:{p['hover']}; }}
""", palette)
        self.list.itemActivated.connect(self._pick_item)
        self.list.currentItemChanged.connect(self._preview_item)
        self.splitter.addWidget(self.list)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(8, 0, 0, 0)
        self._detail_title = QLabel("תצוגה מלאה")
        self._detail_title.setObjectName("sectiontitle")
        detail_layout.addWidget(self._detail_title)
        self.text_preview = QPlainTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setAccessibleName("הטקסט המלא של ההעתקה")
        self.text_preview.setPlaceholderText("בחר פריט מהרשימה כדי לראות את התוכן המלא")
        self.text_preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        option = self.text_preview.document().defaultTextOption()
        option.setTextDirection(Qt.LayoutDirectionAuto)
        option.setAlignment(Qt.AlignRight)
        self.text_preview.document().setDefaultTextOption(option)
        self.image_preview = ImagePreview()
        theme.bind_style(self.image_preview, lambda p:
                         f"QScrollArea{{background:{p['surface']};border:1px solid {p['border']};}}"
                         f"QLabel{{background:{p['surface']};}}", palette)
        detail_layout.addWidget(self.text_preview, 1)
        detail_layout.addWidget(self.image_preview, 1)
        self._image_tools = QWidget()
        tools = QHBoxLayout(self._image_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        for label, callback in (("הגדל +", lambda: self.image_preview.zoom(1.25)),
                                ("הקטן −", lambda: self.image_preview.zoom(0.8)),
                                ("התאם לחלון", self.image_preview.fit)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            tools.addWidget(button)
        detail_layout.addWidget(self._image_tools)
        self._copy_btn = QPushButton("העתק ללוח וסגור")
        self._copy_btn.setProperty("variant", "primary")
        self._copy_btn.clicked.connect(self._copy_selected)
        detail_layout.addWidget(self._copy_btn)
        actions = QHBoxLayout()
        self._pin_btn = QPushButton("הוסף למועדפים")
        self._pin_btn.clicked.connect(self._toggle_pin)
        self._delete_btn = QPushButton("מחק פריט")
        self._delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(self._pin_btn)
        actions.addWidget(self._delete_btn)
        detail_layout.addLayout(actions)
        self.splitter.addWidget(detail)
        self.splitter.setSizes([340, 560])
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        layout.addWidget(self.splitter, 1)
        self._more_btn = QPushButton("הצג עוד")
        self._more_btn.clicked.connect(self._show_more)
        layout.addWidget(self._more_btn)
        feedback = QHBoxLayout()
        self._feedback = QLabel()
        self._feedback.setTextFormat(Qt.PlainText)
        self._feedback.setWordWrap(True)
        self._feedback.setObjectName("fieldlabel")
        feedback.addWidget(self._feedback, 1)
        self._undo_btn = QPushButton("בטל מחיקה")
        self._undo_btn.clicked.connect(self._undo_delete)
        self._undo_btn.hide()
        feedback.addWidget(self._undo_btn)
        self._clear_btn = QPushButton("נקה הכול…")
        self._clear_btn.clicked.connect(self._clear_all)
        feedback.addWidget(self._clear_btn)
        layout.addLayout(feedback)
        hint = QLabel("בחר פריט לתצוגה מלאה · Enter או לחיצה כפולה — העתקה · Delete — מחיקה · Esc — סגירה")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        footer = QHBoxLayout()
        footer.addWidget(hint, 1)
        grip = QSizeGrip(self)
        grip.setToolTip("גרור לשינוי גודל החלון")
        footer.addWidget(grip)
        layout.addLayout(footer)
        self.image_preview.hide()
        self._image_tools.hide()
        self._copy_btn.setEnabled(False)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._apply_filter)
        QShortcut(QKeySequence("Escape"), self, activated=self.close)
        delete_shortcut = QShortcut(QKeySequence("Delete"), self.list, activated=self._delete_selected)
        delete_shortcut.setContext(Qt.WidgetShortcut)
        QShortcut(QKeySequence("Ctrl+Shift+Delete"), self, activated=self._clear_all)
        QShortcut(QKeySequence("Down"), self.search, activated=self._focus_list)
        QShortcut(QKeySequence("Return"), self.search, activated=self._copy_selected)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search.setFocus)
        self.set_palette(palette)
        self.refresh_capture_status()

    def set_palette(self, palette):
        self.p = palette
        theme.apply_palette(self, palette)
        self._search_action.setIcon(icons.icon("search", palette['text_muted'], 16))
        self.update()

    def show_for(self, entries):
        self._entries = list(entries or [])
        self._by_id = {e.get('id'): e for e in self._entries}
        self._limit = PAGE_SIZE
        self.search.clear()
        self._filter_timer.stop()
        self._apply_filter()
        # Keep manual placement on this monitor; follow the cursor to a new one.
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if not getattr(self, '_positioned', False) or screen != self._cursor_screen:
            self._centre()
            self._positioned = True
            self._cursor_screen = screen
        self.show()
        self.raise_()
        self.activateWindow()
        self._force_foreground()
        self.search.setFocus()
        self.refresh_capture_status()

    def refresh_entries(self, entries):
        """Refresh a visible picker without losing search, selection or scroll."""
        selected = self._current_id()
        scroll = self.list.verticalScrollBar().value()
        self._entries = list(entries or [])
        self._by_id = {e.get('id'): e for e in self._entries}
        self._filter_timer.stop()
        matches = clips.search(self.search.text(), self._entries, self._kind)
        position = next((i for i, entry in enumerate(matches) if entry['id'] == selected), 0)
        self._limit = max(self._limit, (position // PAGE_SIZE + 1) * PAGE_SIZE)
        self._apply_filter()
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.UserRole) == selected:
                self.list.setCurrentRow(row)
                break
        self.list.verticalScrollBar().setValue(scroll)

    def _centre(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is not None:
            rect = screen.availableGeometry()
            self.move(rect.center().x() - self.width() // 2,
                      rect.center().y() - self.height() // 2)

    def _force_foreground(self):
        try:
            from platform_support import bring_forward
            bring_forward(self)
        except Exception:
            pass

    def _queue_filter(self):
        self._limit = PAGE_SIZE
        self._filter_timer.start(SEARCH_DEBOUNCE_MS)

    def _set_filter(self, kind):
        self._kind = kind
        self._filter_buttons[kind].setChecked(True)
        self._limit = PAGE_SIZE
        self.search.clear()
        self._filter_timer.stop()
        self._gallery_btn.setVisible(kind == "image")
        self._gallery_btn.blockSignals(True)
        self._gallery_btn.setChecked(kind == "image")
        self._gallery_btn.blockSignals(False)
        self._apply_filter()

    def _show_more(self):
        self._limit += PAGE_SIZE
        self.refresh_entries(self._entries)

    def refresh_capture_status(self):
        paused = self._is_paused()
        self._capture_status.setText("שמירת העתקות כבויה בהגדרות" if not self._capture_enabled else
                                     "נדרשת הרשאת לוח — פתח את הגדרות הכנת ה־Mac" if not self._capture_allowed() else
                                     "שמירת העתקות מושהית · תמלולים ממשיכים להישמר" if paused else
                                     "שמירת העתקות פעילה · נשמר מקומית במחשב")
        self._pause_btn.setText("המשך שמירה" if paused else "השהה שמירה")

    def _toggle_pause(self):
        self._on_pause(not self._is_paused())
        self.refresh_capture_status()

    def _apply_filter(self, *_):
        query = self.search.text()
        selected = self._current_id()
        matches = clips.search(query, self._entries, self._kind)
        gallery = self._kind == "image" and self._gallery_btn.isChecked()
        self.list.setViewMode(QListView.IconMode if gallery else QListView.ListMode)
        self.list.setFlow(QListView.LeftToRight if gallery else QListView.TopToBottom)
        self.list.setWrapping(gallery)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setIconSize(QSize(112, 90) if gallery else QSize(ROW_ICON, ROW_ICON))
        self.list.setGridSize(QSize(158, 146) if gallery else QSize())
        self.list.setUpdatesEnabled(False)
        self.list.blockSignals(True)
        try:
            self.list.clear()
            for entry in matches[:self._limit]:
                kind = clips.category(entry)
                label = {"image": "תמונה", "link": "קישור", "text": "טקסט"}[kind]
                prefix = "מועדף · " if entry.get("pinned") else ""
                snippet = (entry.get("name") or "תמונה") if kind == "image" else clips.preview(entry, 100)
                item = QListWidgetItem(f"{prefix}{label} · {entry.get('time', '')}\n{snippet}")
                item.setData(Qt.UserRole, entry.get("id"))
                if entry.get("kind") == "image":
                    thumb = self._thumb(entry.get("path"))
                    if thumb is not None:
                        item.setIcon(thumb)
                item.setSizeHint(QSize(158, 146) if gallery else QSize(0, 70))
                item.setToolTip("בחר לתצוגה מלאה")
                self.list.addItem(item)
            if self.list.count():
                chosen = next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.UserRole) == selected), 0)
                self.list.setCurrentRow(chosen)
        finally:
            self.list.blockSignals(False)
            self.list.setUpdatesEnabled(True)
        self._preview_item(self.list.currentItem())
        total = len(self._entries)
        self._count.setText(f"{len(matches)} מתוך {total}")
        self._more_btn.setVisible(len(matches) > self._limit)
        self._more_btn.setText(f"הצג עוד · מוצגים {min(self._limit, len(matches))} מתוך {len(matches)}")
        images = sum(e.get("kind") == "image" for e in self._entries)
        self._filter_buttons["image"].setText(f"תמונות ({images})")
        self._clear_btn.setEnabled(bool(self._entries))

    def _thumb(self, path):
        if path in self._thumb_cache:
            return self._thumb_cache[path]
        pixmap = QPixmap(str(path)) if path else QPixmap()
        result = None if pixmap.isNull() else QIcon(pixmap.scaled(
            144, 112, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if len(self._thumb_cache) >= 128:
            self._thumb_cache.clear()
        self._thumb_cache[path] = result
        return result

    def _preview_item(self, item, previous=None):
        entry = self._entry(item.data(Qt.UserRole)) if item else None
        image = bool(entry and entry.get('kind') == 'image')
        self.text_preview.setVisible(not image)
        self.image_preview.setVisible(image)
        self._image_tools.setVisible(image)
        self._copy_btn.setEnabled(entry is not None)
        self._pin_btn.setEnabled(entry is not None)
        self._delete_btn.setEnabled(entry is not None)
        self._pin_btn.setText("הסר ממועדפים" if entry and entry.get("pinned") else "הוסף למועדפים")
        self.text_preview.setPlainText(entry.get('text', '') if entry and not image else '')
        self.image_preview.set_pixmap(QPixmap(entry.get('path', '')) if image else QPixmap())
        if image and self.image_preview._pixmap.isNull():
            self._detail_title.setText("התמונה אינה זמינה בדיסק")
            self._copy_btn.setEnabled(False)
        else:
            self._detail_title.setText("תצוגת תמונה" if image else "הטקסט המלא" if entry else
                                       "לא נמצאו פריטים" if self._entries else "עדיין אין העתקות שמורות")
        self.text_preview.setPlaceholderText("נסה מסנן אחר או נקה את החיפוש. תמונות ניתנות לחיפוש לפי שם ותאריך, ללא זיהוי טקסט בתמונה."
                                             if self._entries else "העתק טקסט או צלם מסך כדי להתחיל. פריטים שסומנו כפרטיים אינם נשמרים.")

    def _focus_list(self):
        if self.list.count():
            self.list.setFocus()
            if self.list.currentRow() < 0:
                self.list.setCurrentRow(0)

    def _current_id(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _entry(self, cid):
        return self._by_id.get(cid)

    def _copy_selected(self):
        item = self.list.currentItem()
        if item is not None:
            self._pick_item(item)

    def _pick_item(self, item):
        entry = self._entry(item.data(Qt.UserRole))
        if entry is not None and self._copy_btn.isEnabled():
            try:
                result = self._on_pick(entry)
            except Exception:
                log.exception("Clipboard selection failed")
                result = False
            if result is False:
                self._feedback.setText("ההעתקה לא הצליחה. הפריט נשמר; אפשר לנסות שוב.")
                return
            self.close()

    def _toggle_pin(self):
        entry = self._entry(self._current_id())
        if entry is None:
            return
        pinned = not entry.get("pinned", False)
        if not self._on_pin(entry["id"], pinned):
            self._feedback.setText("לא ניתן לשמור את המועדף. נסה שוב.")
            return
        entry["pinned"] = pinned
        self._apply_filter()

    def _delete_selected(self):
        cid = self._current_id()
        if not cid:
            return
        row = self.list.currentRow()
        removed = self._on_delete(cid)
        if not removed:
            self._feedback.setText("המחיקה לא נשמרה. הפריט נשאר ברשימה.")
            return
        self._undo = removed
        self._undo_btn.show()
        self._feedback.setText("הפריט נמחק מהרשימה. אפשר לבטל את המחיקה האחרונה.")
        self._entries = [e for e in self._entries if e.get("id") != cid]
        self._by_id.pop(cid, None)
        self._apply_filter()
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _undo_delete(self):
        if not self._undo:
            return
        entry, index = self._undo
        if not self._on_restore(entry, index):
            self._feedback.setText("שחזור הפריט לא הצליח. אפשר לנסות שוב.")
            return
        if entry["id"] not in self._by_id:
            self._entries.insert(min(index, len(self._entries)), entry)
            self._by_id[entry["id"]] = entry
        self._undo = None
        self._undo_btn.hide()
        self._feedback.setText("הפריט שוחזר.")
        self._apply_filter()

    def _clear_all(self):
        box = QMessageBox(self)
        box.setWindowTitle("MyWhisper")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"למחוק לצמיתות את כל {len(self._entries)} ההעתקות?")
        wipe = box.addButton("מחק הכל", QMessageBox.DestructiveRole)
        cancel = box.addButton("ביטול", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is not wipe:
            return
        if self._on_clear() is False:
            self._feedback.setText("הניקוי לא הושלם. בדוק את הודעת השמירה ונסה שוב.")
            return
        self._entries = []
        self._by_id = {}
        self._undo = None
        self._undo_btn.hide()
        self._thumb_cache.clear()
        self._feedback.setText("היסטוריית ההעתקות נוקתה.")
        self._apply_filter()

    def keyPressEvent(self, event):
        if (self.list.hasFocus() and event.text() and event.text().isprintable()
                and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.search.setFocus()
            self.search.setText(self.search.text() + event.text())
            return
        super().keyPressEvent(event)
