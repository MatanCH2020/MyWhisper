"""The clipboard-history picker: a popup list of everything you've copied.

Opened by its own global hotkey. Type to filter, Enter or click to put an entry
back on the clipboard, then paste it wherever you were.

Uses QListWidget rather than the hand-built card list of the history page: the
clip store is deliberately unbounded, and QListWidget only creates view items
for visible rows, so 10,000 clips cost the same to show as 20.
"""
import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCursor, QFont, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QVBoxLayout, QWidget,
)

import clips
import icons
import theme

log = logging.getLogger("clipui")

ROW_ICON = 34          # thumbnail edge for image rows
SEARCH_DEBOUNCE_MS = 120


class ClipPicker(QWidget):
    """Frameless popup: search box + clip list + key hints."""

    def __init__(self, palette, on_pick, on_delete=None, on_clear=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.p = palette
        self._on_pick = on_pick
        self._on_delete = on_delete or (lambda cid: None)
        self._on_clear = on_clear or (lambda: None)
        self._entries = []
        self.setWindowTitle("MyWhisper — היסטוריית העתקות")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(620, 460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("clipcard")
        card.setStyleSheet(
            f"#clipcard{{background:{palette['bg']};"
            f"border:1px solid {palette['border']};border-radius:{theme.RADIUS}px;}}")
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("היסטוריית העתקות")
        title.setFont(QFont(theme.pick_font(), theme.FS["title"], QFont.Bold))
        title.setStyleSheet(f"color:{palette['text']};")
        head.addWidget(title)
        head.addStretch(1)
        self._count = QLabel("")
        self._count.setObjectName("hint")
        head.addWidget(self._count)
        v.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("חיפוש…")
        self.search.addAction(icons.icon("search", palette["text_muted"], 16),
                              QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._queue_filter)
        v.addWidget(self.search)

        self.list = QListWidget()
        self.list.setUniformItemSizes(False)
        self.list.setStyleSheet(
            f"QListWidget{{background:{palette['surface']};color:{palette['text']};"
            f"border:1px solid {palette['border']};border-radius:{theme.RADIUS_SM}px;"
            f"outline:none;padding:4px;font-size:{theme.FS['body']}px;}}"
            f"QListWidget::item{{padding:7px 9px;border-radius:6px;}}"
            f"QListWidget::item:selected{{background:{palette['accent']};"
            f"color:{palette['on_accent']};}}"
            f"QListWidget::item:hover{{background:{palette['hover']};}}")
        self.list.itemActivated.connect(self._pick_item)
        self.list.itemClicked.connect(self._pick_item)
        v.addWidget(self.list, 1)

        hint = QLabel("Enter או קליק — העתקה ללוח  ·  Delete — מחיקה  ·  Esc — סגירה")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignCenter)
        v.addWidget(hint)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._apply_filter)

        QShortcut(QKeySequence("Escape"), self, activated=self.hide)
        QShortcut(QKeySequence("Delete"), self, activated=self._delete_selected)
        QShortcut(QKeySequence("Ctrl+Shift+Delete"), self, activated=self._clear_all)
        # Down from the search box moves into the list without losing the query.
        QShortcut(QKeySequence("Down"), self.search, activated=self._focus_list)

    # ---- population ----
    def show_for(self, entries):
        """Reload from *entries* and pop up, focused and ready to type."""
        self._entries = list(entries or [])
        self.search.clear()          # each open starts a fresh query
        self._apply_filter()
        self._centre()
        self.show()
        self.raise_()
        self.activateWindow()
        self._force_foreground()
        self.search.setFocus()

    def _centre(self):
        """Centre on the screen the cursor is on, not the primary one — on a
        multi-monitor setup the picker must appear where the user is working."""
        screen = None
        try:
            screen = QApplication.screenAt(QCursor.pos())
        except Exception:
            screen = None
        scr = (screen or QApplication.primaryScreen()).availableGeometry()
        self.move(scr.center().x() - self.width() // 2,
                  scr.center().y() - self.height() // 2)

    def _force_foreground(self):
        """A popup raised from a global hotkey has no foreground rights on
        Windows, so without this it can open behind the active app."""
        try:
            import ctypes
            hwnd = int(self.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 5)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _queue_filter(self):
        self._filter_timer.start(SEARCH_DEBOUNCE_MS)

    def _apply_filter(self):
        q = self.search.text()
        matches = clips.search(q, self._entries)
        self.list.clear()
        for e in matches:
            item = QListWidgetItem(clips.preview(e))
            item.setData(Qt.UserRole, e.get("id"))
            if e.get("kind") == "image":
                thumb = self._thumb(e.get("path"))
                if thumb is not None:
                    item.setIcon(thumb)
                    item.setSizeHint(QSize(0, ROW_ICON + 12))
            item.setToolTip(e.get("text") or e.get("path") or "")
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        total = len(self._entries)
        self._count.setText(f"{len(matches)} מתוך {total}" if q.strip()
                            else f"{total} פריטים")

    @staticmethod
    def _thumb(path):
        if not path:
            return None
        pm = QPixmap(str(path))
        if pm.isNull():
            return None
        return QIcon(pm.scaled(ROW_ICON, ROW_ICON, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation))

    def _focus_list(self):
        if self.list.count():
            self.list.setFocus()
            if self.list.currentRow() < 0:
                self.list.setCurrentRow(0)

    # ---- actions ----
    def _current_id(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _entry(self, cid):
        return next((e for e in self._entries if e.get("id") == cid), None)

    def _pick_item(self, item):
        entry = self._entry(item.data(Qt.UserRole))
        if entry is None:
            return
        self.hide()          # hide first, so focus returns to the user's app
        self._on_pick(entry)

    def _delete_selected(self):
        cid = self._current_id()
        if not cid:
            return
        row = self.list.currentRow()
        self._on_delete(cid)
        self._entries = [e for e in self._entries if e.get("id") != cid]
        self._apply_filter()
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _clear_all(self):
        self._on_clear()
        self._entries = []
        self._apply_filter()

    def keyPressEvent(self, e):
        # Typing anywhere goes to the search box, so the picker is usable
        # without ever clicking into the field.
        if (self.list.hasFocus() and e.text() and e.text().isprintable()
                and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.search.setFocus()
            self.search.setText(self.search.text() + e.text())
            return
        super().keyPressEvent(e)
