"""Resizable clipboard browser with full text and zoomable image previews."""
import logging
import unicodedata

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QKeySequence, QPainter, QPixmap, QShortcut, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListView,
    QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizeGrip, QSplitter, QVBoxLayout, QWidget, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
)

import clips
import icons
import theme
from widgets import FramelessWindow, TitleBar

log = logging.getLogger("clipui")
ROW_IMAGE_HEIGHT = 140
ENTRY_ROLE = Qt.UserRole + 1
SEARCH_DEBOUNCE_MS = 120
PAGE_SIZE = 100


def _is_rtl(text):
    return next((unicodedata.bidirectional(c) for c in text
                 if unicodedata.bidirectional(c) in ("L", "R", "AL")), "R") != "L"


class ClipDelegate(QStyledItemDelegate):
    """Content-only rows, with large aspect-preserving images and three text lines."""

    def __init__(self, picker):
        super().__init__(picker.list)
        self.picker = picker

    def sizeHint(self, option, index):
        entry = index.data(ENTRY_ROLE) or {}
        return QSize(220, ROW_IMAGE_HEIGHT + 24 if entry.get("kind") == "image" else 88)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.features &= ~QStyleOptionViewItem.HasDisplay
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        entry = index.data(ENTRY_ROLE) or {}
        rect = QRectF(option.rect.adjusted(12, 10, -12, -10))
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(QColor(self.picker.p["text"]))
        if entry.get("kind") == "image":
            pixmap = self.picker._thumb(entry.get("path"))
            if pixmap is not None:
                size = pixmap.size().scaled(rect.size().toSize(), Qt.KeepAspectRatio)
                target = QRectF(0, 0, size.width(), size.height())
                target.moveCenter(rect.center())
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
            else:
                painter.drawText(rect, Qt.AlignCenter, "התמונה אינה זמינה")
        else:
            text = index.data(Qt.DisplayRole) or ""
            font = QFont(option.font)
            font.setPixelSize(16)
            painter.setFont(font)
            rtl = _is_rtl(text)
            text_option = QTextOption()
            text_option.setTextDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
            text_option.setAlignment(Qt.AlignRight if rtl else Qt.AlignLeft)
            text_option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            text_layout = QTextLayout(text, font)
            utf16 = text.encode("utf-16-le")
            text_layout.setTextOption(text_option)
            text_layout.beginLayout()
            y = 0
            for number in range(3):
                line = text_layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(rect.width())
                line.setPosition(QPointF(0, y))
                if number == 2 and line.textStart() + line.textLength() < len(utf16) // 2:
                    # Draw an explicitly elided final line instead of clipping words.
                    tail = painter.fontMetrics().elidedText(utf16[line.textStart() * 2:].decode("utf-16-le"),
                                                           Qt.ElideRight, int(rect.width()))
                    painter.drawText(QRectF(rect.x(), rect.y() + y, rect.width(), line.height()),
                                     (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter, tail)
                else:
                    line.draw(painter, rect.topLeft())
                y += line.height() + 2
            text_layout.endLayout()
        if entry.get("pinned"):
            badge = QRectF(rect.right() - 22, rect.top(), 22, 22)
            painter.fillRect(badge, QColor(self.picker.p["surface"]))
            painter.drawPixmap(badge.toRect(), icons.icon("star", self.picker.p["text"], 16).pixmap(16, 16))
        painter.restore()


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

    def actual_size(self):
        self._zoom = 1.0
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
        self._expanded = False
        self._restore_maximized = False
        self._activation_context = None
        self._activation_timer = QTimer(self)
        self._activation_timer.setSingleShot(True)
        self._activation_timer.timeout.connect(self._retry_foreground)
        self.setWindowTitle("MyWhisper — תמלולים והעתקות")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumSize(640, 440)
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

        self._browse_controls = QWidget()
        browse = QVBoxLayout(self._browse_controls)
        browse.setContentsMargins(0, 0, 0, 0)
        browse.setSpacing(10)
        layout.addWidget(self._browse_controls)

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
        self._options_btn = QPushButton("⋯")
        self._options_btn.setAccessibleName("פעולות נוספות ומצב שמירה")
        self._options_btn.setToolTip("פעולות נוספות ומצב שמירה")
        self._options_btn.setFixedWidth(38)
        menu = QMenu(self._options_btn)
        self._menu_status = menu.addAction("")
        self._menu_status.setEnabled(False)
        self._menu_pause = menu.addAction("השהה שמירה", self._toggle_pause)
        menu.addSeparator()
        self._menu_pin = menu.addAction("הוסף למועדפים", self._toggle_pin)
        self._menu_delete = menu.addAction("מחק פריט", self._delete_selected)
        menu.addSeparator()
        self._menu_clear = menu.addAction("נקה הכול…", self._clear_all)
        menu.aboutToShow.connect(self._refresh_menu)
        self._options_btn.setMenu(menu)
        self._options_btn.hide()
        header.addWidget(self._options_btn)
        browse.addLayout(header)

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
        browse.addLayout(filters)
        self._capture_row = QWidget()
        capture = QHBoxLayout(self._capture_row)
        capture.setContentsMargins(0, 0, 0, 0)
        self._capture_status = QLabel()
        self._capture_status.setObjectName("fieldlabel")
        capture.addWidget(self._capture_status, 1)
        self._pause_btn = QPushButton()
        self._pause_btn.clicked.connect(self._toggle_pause)
        self._pause_btn.setEnabled(capture_enabled)
        capture.addWidget(self._pause_btn)
        browse.addWidget(self._capture_row)

        self.splitter = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setAccessibleName("רשימת העתקות")
        self.list.setMinimumWidth(244)
        self.list.setItemDelegate(ClipDelegate(self))
        self.list.setUniformItemSizes(False)
        self.list.setVerticalScrollMode(QListView.ScrollPerPixel)
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
        self._detail_title = QLabel()
        self._detail_title.setObjectName("sectiontitle")
        detail_layout.addWidget(self._detail_title)
        self.text_preview = QPlainTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setObjectName("cliptextpreview")
        self.text_preview.setAccessibleName("הטקסט המלא של ההעתקה")
        self.text_preview.setPlaceholderText("בחר פריט מהרשימה כדי לראות את התוכן המלא")
        self.text_preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        option = self.text_preview.document().defaultTextOption()
        option.setTextDirection(Qt.LayoutDirectionAuto)
        option.setAlignment(Qt.AlignLeading)
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
        for label, tip, callback in (("+", "הגדל תמונה", lambda: self.image_preview.zoom(1.25)),
                                     ("−", "הקטן תמונה", lambda: self.image_preview.zoom(0.8)),
                                     ("100%", "גודל מקורי", self.image_preview.actual_size),
                                     ("התאם", "התאם לחלון", self.image_preview.fit)):
            button = QPushButton(label)
            button.setAccessibleName(tip)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            tools.addWidget(button)
        detail_layout.addWidget(self._image_tools)
        self._expand_btn = QPushButton("הרחב")
        self._expand_btn.setCheckable(True)
        self._expand_btn.setAccessibleName("הרחב או החזר את תצוגת התוכן")
        self._expand_btn.toggled.connect(self._set_expanded)
        self._copy_btn = QPushButton("העתק ללוח וסגור")
        self._copy_btn.setProperty("variant", "primary")
        self._copy_btn.clicked.connect(self._copy_selected)
        primary = QHBoxLayout()
        primary.addWidget(self._copy_btn, 1)
        primary.addWidget(self._expand_btn)
        detail_layout.addLayout(primary)
        self._secondary_actions = QWidget()
        actions = QHBoxLayout(self._secondary_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self._pin_btn = QPushButton("הוסף למועדפים")
        self._pin_btn.clicked.connect(self._toggle_pin)
        self._delete_btn = QPushButton("מחק פריט")
        self._delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(self._pin_btn)
        actions.addWidget(self._delete_btn)
        detail_layout.addWidget(self._secondary_actions)
        self.splitter.addWidget(detail)
        detail.setMinimumWidth(260)
        self.splitter.setSizes([315, 585])
        self.splitter.setStretchFactor(0, 35)
        self.splitter.setStretchFactor(1, 65)
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
        hint = QLabel("Enter — העתקה וסגירה · Esc — חזרה / סגירה")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        self._footer = QWidget()
        footer = QHBoxLayout(self._footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(hint, 1)
        grip = QSizeGrip(self)
        grip.setToolTip("גרור לשינוי גודל החלון")
        footer.addWidget(grip)
        layout.addWidget(self._footer)
        self.image_preview.hide()
        self._image_tools.hide()
        self._copy_btn.setEnabled(False)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._apply_filter)
        QShortcut(QKeySequence("Escape"), self, activated=self._escape)
        delete_shortcut = QShortcut(QKeySequence("Delete"), self.list, activated=self._delete_selected)
        delete_shortcut.setContext(Qt.WidgetShortcut)
        QShortcut(QKeySequence("Ctrl+Shift+Delete"), self, activated=self._clear_all)
        QShortcut(QKeySequence("Down"), self.search, activated=self._focus_list)
        QShortcut(QKeySequence("Return"), self.search, activated=self._copy_selected)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        self.set_palette(palette)
        self.refresh_capture_status()

    def set_palette(self, palette):
        self.p = palette
        theme.apply_palette(self, palette)
        self._search_action.setIcon(icons.icon("search", palette['text_muted'], 16))
        self.update()

    def show_for(self, entries, preserve=False):
        if preserve:
            self.refresh_entries(entries)
        else:
            self._entries = list(entries or [])
            self._by_id = {e.get('id'): e for e in self._entries}
            self._limit = PAGE_SIZE
            self.search.clear()
            self._filter_timer.stop()
            self._apply_filter()
        # Keep manual placement on this monitor; follow the cursor to a new one.
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        move_screen = not getattr(self, '_positioned', False) or screen != self._cursor_screen
        maximize_on_screen = move_screen and (self.isMaximized() or
                                              (self.isMinimized() and self._restore_maximized))
        if maximize_on_screen:
            # Moving a maximized HWND alone leaves it at the previous monitor's size.
            self.showNormal()
        if move_screen:
            self._centre()
            self._fit_screen(screen)
            self._positioned = True
            self._cursor_screen = screen
        if maximize_on_screen:
            self.showMaximized()
        elif self.isMinimized():
            normal = self.normalGeometry()
            self.showNormal()
            if normal.isValid():
                self.setGeometry(normal)
            if self._restore_maximized:
                self.showMaximized()
        else:
            self.show()
        self._fit_screen(screen)
        self._force_foreground()
        self.search.setFocus()
        self.refresh_capture_status()

    def _fit_screen(self, screen):
        if screen is None or self.isMaximized() or self.isMinimized():
            return
        rect = screen.availableGeometry()
        self.setMinimumSize(min(640, rect.width()), min(440, rect.height()))
        self.resize(min(self.width(), rect.width()), min(self.height(), rect.height()))
        self.move(max(rect.left(), min(self.x(), rect.right() - self.width() + 1)),
                  max(rect.top(), min(self.y(), rect.bottom() - self.height() + 1)))

    def _set_expanded(self, expanded):
        self._expanded = expanded
        if expanded:
            self._split_sizes = self.splitter.sizes()
        self.list.setVisible(not expanded)
        self._browse_controls.setVisible(not expanded)
        self._refresh_compact()
        if not expanded and hasattr(self, "_split_sizes"):
            self.splitter.setSizes(self._split_sizes)
        self._expand_btn.setText("חזור לרשימה" if expanded else "הרחב")

    def _refresh_compact(self):
        compact = self.height() < 600
        self._capture_row.setVisible(not compact)
        self._options_btn.setVisible(compact)
        self._secondary_actions.setVisible(not compact and not self._expanded)
        self._clear_btn.setVisible(not compact and not self._expanded)
        self._footer.setVisible(not compact and not self._expanded)
        self._more_btn.setVisible(not self._expanded and
                                  getattr(self, '_match_count', 0) > self._limit)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_footer'):
            self._refresh_compact()

    def _refresh_menu(self):
        self._menu_status.setText(self._capture_status.text())
        self._menu_pause.setText(self._pause_btn.text())
        self._menu_pause.setEnabled(self._pause_btn.isEnabled())
        self._menu_pin.setText(self._pin_btn.text())
        self._menu_pin.setEnabled(self._pin_btn.isEnabled())
        self._menu_delete.setEnabled(self._delete_btn.isEnabled())
        self._menu_clear.setEnabled(bool(self._entries))

    def _focus_search(self):
        self._expand_btn.setChecked(False)
        self.search.setFocus()

    def _escape(self):
        if self._expanded:
            self._expand_btn.setChecked(False)
        else:
            self.close()

    def is_foreground(self):
        from platform_support import is_foreground
        return is_foreground(self)

    def hideEvent(self, event):
        self._activation_timer.stop()
        self._activation_context = None
        super().hideEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange and self.isMinimized():
            if not event.oldState() & Qt.WindowMinimized:
                self._restore_maximized = bool(event.oldState() & Qt.WindowMaximized)
        super().changeEvent(event)

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
        self._activation_timer.stop()
        try:
            from platform_support import bring_forward, foreground_context
            self._activation_context = foreground_context()
            if bring_forward(self):
                self._fit_screen(QApplication.screenAt(QCursor.pos()))
                return True
            self._activation_timer.start(100)
        except Exception:
            log.warning("Clipboard window activation failed", exc_info=True)
            QApplication.alert(self)
        return False

    def _retry_foreground(self):
        from platform_support import bring_forward, foreground_context
        if not self.isVisible() or self.isMinimized() or self._activation_context is None:
            return
        if self.is_foreground():
            return
        # Do not steal focus after another input or a switch to a different window.
        if foreground_context() != self._activation_context:
            return
        try:
            if not bring_forward(self):
                QApplication.alert(self)
                log.warning("Windows declined clipboard window activation")
        except Exception:
            QApplication.alert(self)
            log.warning("Clipboard window activation retry failed", exc_info=True)

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
        self._capture_status.setText("שמירה כבויה" if not self._capture_enabled else
                                     "נדרשת הרשאת לוח — פתח את הגדרות הכנת ה־Mac" if not self._capture_allowed() else
                                     "שמירה מושהית" if paused else "שמירה מקומית פעילה")
        self._capture_status.setToolTip("ההשהיה חלה על העתקות חיצוניות; תמלולים ממשיכים להישמר.")
        self._pause_btn.setText("המשך שמירה" if paused else "השהה שמירה")

    def _toggle_pause(self):
        self._on_pause(not self._is_paused())
        self.refresh_capture_status()

    def _apply_filter(self, *_):
        query = self.search.text()
        selected = self._current_id()
        matches = clips.search(query, self._entries, self._kind)
        self._match_count = len(matches)
        gallery = self._kind == "image" and self._gallery_btn.isChecked()
        self.list.setViewMode(QListView.IconMode if gallery else QListView.ListMode)
        self.list.setFlow(QListView.LeftToRight if gallery else QListView.TopToBottom)
        self.list.setWrapping(gallery)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setGridSize(QSize(220, ROW_IMAGE_HEIGHT + 24) if gallery else QSize())
        self.list.setUpdatesEnabled(False)
        self.list.blockSignals(True)
        try:
            self.list.clear()
            for entry in matches[:self._limit]:
                snippet = "" if entry.get("kind") == "image" else " ".join(entry.get("text", "").split())[:1000]
                item = QListWidgetItem(snippet)
                item.setData(Qt.UserRole, entry.get("id"))
                item.setData(ENTRY_ROLE, entry)
                item.setData(Qt.AccessibleTextRole,
                             ("מועדף. " if entry.get("pinned") else "") + (snippet or "תמונה שמורה"))
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
        self._more_btn.setVisible(not self._expanded and len(matches) > self._limit)
        self._more_btn.setText(f"הצג עוד · מוצגים {min(self._limit, len(matches))} מתוך {len(matches)}")
        images = sum(e.get("kind") == "image" for e in self._entries)
        self._filter_buttons["image"].setText(f"תמונות ({images})")
        self._clear_btn.setEnabled(bool(self._entries))

    def _thumb(self, path):
        if path in self._thumb_cache:
            return self._thumb_cache[path]
        pixmap = QPixmap(str(path)) if path else QPixmap()
        # Cache enough pixels for a 2x display, without caching original large files.
        result = None if pixmap.isNull() else pixmap.scaled(
            800, ROW_IMAGE_HEIGHT * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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
        self._expand_btn.setEnabled(entry is not None or self._expanded)
        self._pin_btn.setText("הסר ממועדפים" if entry and entry.get("pinned") else "הוסף למועדפים")
        text = entry.get('text', '') if entry and not image else ''
        if self.text_preview.toPlainText() != text:
            option = self.text_preview.document().defaultTextOption()
            option.setAlignment(Qt.AlignRight if _is_rtl(text) else Qt.AlignLeft)
            self.text_preview.document().setDefaultTextOption(option)
            self.text_preview.setPlainText(text)
        image_path = entry.get('path', '') if image else None
        if image_path != getattr(self, '_preview_path', None) or self.image_preview._pixmap.isNull():
            self.image_preview.set_pixmap(QPixmap(image_path) if image else QPixmap())
        self._preview_path = image_path
        if image and self.image_preview._pixmap.isNull():
            self._detail_title.setText("התמונה אינה זמינה בדיסק")
            self._copy_btn.setEnabled(False)
        else:
            self._detail_title.setText("" if entry else "לא נמצאו פריטים" if self._entries else "עדיין אין העתקות שמורות")
        self._detail_title.setVisible(bool(self._detail_title.text()))
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
