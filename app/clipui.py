"""Resizable clipboard browser with full text and zoomable image previews."""
import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCursor, QIcon, QKeySequence, QPixmap, QShortcut, QTextOption
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
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

    def __init__(self, palette, on_pick, on_delete=None, on_clear=None, on_theme=None):
        super().__init__()
        self.p = palette
        self._on_pick = on_pick
        self._on_delete = on_delete or (lambda cid: None)
        self._on_clear = on_clear or (lambda: None)
        self._entries = []
        self._by_id = {}
        self.setWindowTitle("MyWhisper — היסטוריית העתקות")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumSize(720, 460)
        self.resize(1000, 680)
        theme.bind_style(self.container, lambda p:
                         f"#container{{background:{p['bg']};border-radius:14px;}}", palette)
        self.body.addWidget(TitleBar(
            palette, on_theme or (lambda: None), self.showMinimized, self.close,
            on_max=self.toggle_max, title_text="היסטוריית העתקות", close_tip="סגור"))
        content = QWidget()
        self.body.addWidget(content, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 10, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("חיפוש בהעתקות…")
        self.search.setAccessibleName("חיפוש בהיסטוריית העתקות")
        self._search_action = self.search.addAction(
            icons.icon("search", palette['text_muted'], 16), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._queue_filter)
        header.addWidget(self.search, 1)
        self._count = QLabel()
        self._count.setObjectName("hint")
        header.addWidget(self._count)
        layout.addLayout(header)

        self.splitter = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setAccessibleName("רשימת העתקות")
        self.list.setIconSize(QSize(ROW_ICON, ROW_ICON))
        self.list.setUniformItemSizes(True)
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
        self.splitter.addWidget(detail)
        self.splitter.setSizes([340, 560])
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        layout.addWidget(self.splitter, 1)
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
        QShortcut(QKeySequence("Delete"), self.list, activated=self._delete_selected)
        QShortcut(QKeySequence("Ctrl+Shift+Delete"), self, activated=self._clear_all)
        QShortcut(QKeySequence("Down"), self.search, activated=self._focus_list)
        QShortcut(QKeySequence("Return"), self.search, activated=self._copy_selected)
        self.set_palette(palette)

    def set_palette(self, palette):
        self.p = palette
        theme.apply_palette(self, palette)
        self._search_action.setIcon(icons.icon("search", palette['text_muted'], 16))
        self.update()

    def show_for(self, entries):
        self._entries = list(entries or [])
        self._by_id = {e.get('id'): e for e in self._entries}
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

    def _centre(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is not None:
            rect = screen.availableGeometry()
            self.move(rect.center().x() - self.width() // 2,
                      rect.center().y() - self.height() // 2)

    def _force_foreground(self):
        try:
            import ctypes
            hwnd = int(self.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 3 if self.isMaximized() else 5)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _queue_filter(self):
        self._filter_timer.start(SEARCH_DEBOUNCE_MS)

    def _apply_filter(self):
        query = self.search.text()
        matches = clips.search(query, self._entries)
        self.list.setUpdatesEnabled(False)
        self.list.blockSignals(True)
        try:
            self.list.clear()
            for entry in matches:
                item = QListWidgetItem(clips.preview(entry))
                item.setData(Qt.UserRole, entry.get("id"))
                if entry.get("kind") == "image":
                    thumb = self._thumb(entry.get("path"))
                    if thumb is not None:
                        item.setIcon(thumb)
                item.setSizeHint(QSize(0, ROW_ICON + 18))
                item.setToolTip("בחר לתצוגה מלאה")
                self.list.addItem(item)
            if self.list.count():
                self.list.setCurrentRow(0)
        finally:
            self.list.blockSignals(False)
            self.list.setUpdatesEnabled(True)
        self._preview_item(self.list.currentItem())
        total = len(self._entries)
        self._count.setText(f"{len(matches)} מתוך {total}" if query.strip() else f"{total} פריטים")

    @staticmethod
    def _thumb(path):
        pixmap = QPixmap(str(path)) if path else QPixmap()
        return None if pixmap.isNull() else QIcon(pixmap.scaled(
            ROW_ICON, ROW_ICON, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _preview_item(self, item, previous=None):
        entry = self._entry(item.data(Qt.UserRole)) if item else None
        image = bool(entry and entry.get('kind') == 'image')
        self.text_preview.setVisible(not image)
        self.image_preview.setVisible(image)
        self._image_tools.setVisible(image)
        self._copy_btn.setEnabled(entry is not None)
        self.text_preview.setPlainText(entry.get('text', '') if entry and not image else '')
        self.image_preview.set_pixmap(QPixmap(entry.get('path', '')) if image else QPixmap())
        if image and self.image_preview._pixmap.isNull():
            self._detail_title.setText("התמונה אינה זמינה בדיסק")
            self._copy_btn.setEnabled(False)
        else:
            self._detail_title.setText("תצוגת תמונה" if image else "הטקסט המלא")

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
            self.close()
            self._on_pick(entry)

    def _delete_selected(self):
        cid = self._current_id()
        if not cid:
            return
        row = self.list.currentRow()
        if not self._on_delete(cid):
            return
        self._entries = [e for e in self._entries if e.get("id") != cid]
        self._by_id.pop(cid, None)
        self._apply_filter()
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))

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
        if box.clickedButton() is not wipe or self._on_clear() is False:
            return
        self._entries = []
        self._by_id = {}
        self._apply_filter()

    def keyPressEvent(self, event):
        if (self.list.hasFocus() and event.text() and event.text().isprintable()
                and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.search.setFocus()
            self.search.setText(self.search.text() + event.text())
            return
        super().keyPressEvent(event)
