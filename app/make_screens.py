"""Generate the README screenshots (docs/*.png) with curated demo data.

Renders the real Qt UI offscreen — no personal history/corrections are read,
so the images are safe to publish. Re-run after UI changes:

    .\\.venv\\Scripts\\python app\\make_screens.py
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
# In Qt's offscreen backend the font database can be empty on Windows, which
# turns Hebrew README screenshots into square boxes. Seed known system fonts
# before the themed UI asks theme.pick_font() for a family.
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    for font in ("segoeui.ttf", "segoeuib.ttf", "arial.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))

import corrections
import ui as ui_mod
from config import DEFAULTS

DOCS = Path(__file__).resolve().parent.parent / "docs"

DEMO_HISTORY = [
    {"id": "d1", "time": "2026-07-18 09:42",
     "text": "שלום, זה תמלול לדוגמה שנכתב עם MyWhisper — בלי לגעת במקלדת!",
     "original_text": "שלום שלום, אממ, זה תמלול לדוגמה שנכתב עם MyWhisper — בלי לגעת במקלדת!",
     "edit_status": "edited"},
    {"id": "d2", "time": "2026-07-18 09:40",
     "text": "צריך להוסיף thumbnail לסרטון החדש, ואז לעשות render לפרויקט."},
    {"id": "d3", "time": "2026-07-18 09:37",
     "text": "מה השעה עכשיו? תזכיר לי פגישה מחר בעשר בבוקר."},
]
DEMO_CORRECTIONS = {"תאמנל": "thumbnail", "וייס פר": "Whisper", "רנדר": "render"}
DEMO_TERMS = ["GitHub", "WhatsApp", "thumbnail", "render"]


def demo_tokens(text):
    """Render Hebrew links without consulting personal dictionaries."""
    return [{"text": part, "word": bool(re.fullmatch("[א-ת]+", part)),
             "unknown": False} for part in re.split("([א-ת]+)", text) if part]


def build(theme_name):
    cfg = dict(DEFAULTS)
    cfg["theme"] = theme_name
    ctl = ui_mod.AppUI(
        cfg, lambda: 0.6, lambda c: None,
        get_history=lambda: DEMO_HISTORY,
        clear_history=lambda: None,
        test_sound=lambda n: None,
        import_sound=lambda n, p: False,
        flag_tokens=demo_tokens,
        list_corrections=lambda: DEMO_CORRECTIONS,
        english_terms=lambda: DEMO_TERMS,
        format_bidi=corrections.format_bidi,
    )
    ctl.list_input_devices = lambda: ["Microphone Array (Realtek Audio)", "NVIDIA Broadcast"]
    ctl.model_status = lambda: {
        "state": "ready",
        "device": "cuda",
        "model": cfg["model"],
        "fallback": False,
    }
    ctl.clip_count = lambda: 18
    ctl.clip_paused = lambda: False
    win = ui_mod.MainWindow(ctl, ctl.p)
    win.resize(960, 660)
    # Show far off-screen so grab() renders a fully laid-out window without
    # flashing anything visible to the user.
    win.move(-4000, 200)
    win.show()
    app.processEvents()
    return win


def shoot(win, page, path):
    win.nav.set_index(page)
    win._goto(page)
    app.processEvents()
    win.grab().save(str(path), "PNG")
    print(f"wrote {path}")


def main():
    global DOCS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clipboard-only", action="store_true", help="Render only synthetic clipboard examples")
    parser.add_argument("--output-dir", type=Path, help="Optional screenshot destination")
    parser.add_argument("--overlay-only", action="store_true",
                        help="Refresh only the recording HUD screenshot")
    parser.add_argument("--chatgpt-states", action="store_true",
                        help="Render connected and failed-sign-in examples with synthetic account data")
    parser.add_argument("--mode-states", action="store_true", help="Render both dictation modes with demo data")
    parser.add_argument("--update-states", action="store_true", help="Render update progress and the account welcome")
    parser.add_argument("--scan-states", action="store_true", help="Render synthetic scan progress and persistent results")
    parser.add_argument("--fullscreen-paused", action="store_true", help="Render fullscreen suspension with demo data")
    parser.add_argument("--support-states", action="store_true", help="Render the support preview with synthetic data only")
    args = parser.parse_args()
    if args.output_dir:
        DOCS = args.output_dir.resolve()
    DOCS.mkdir(exist_ok=True)
    if args.update_states:
        from PySide6.QtCore import QTimer
        for name in ("dark", "light"):
            win = build(name)
            win._goto(2, from_nav=False)
            def capture():
                box = app.activeModalWidget()
                box.grab().save(str(DOCS / f"app-welcome-{name}.png"))
                box.accept()
            QTimer.singleShot(60, capture)
            win._on_cloud_result({"connected": True, "needs_welcome": True, "message":
                "החשבון מחובר. השימוש במסגרת חשבון ChatGPT ובכפוף למכסתו. העריכה עדיין כבויה; אפשר להפעיל אותה בנפרד."})
            if name == "dark":
                card = win._upd_btn.parentWidget()
                while not hasattr(card, "set_open"):
                    card = card.parentWidget()
                card.set_open(True)
                win._show_update_stage("מגבה את הגרסה המותקנת…", 48)
                app.processEvents()
                win.stack.widget(2).ensureWidgetVisible(win._upd_progress)
                shoot(win, 2, DOCS / "app-update-progress.png")
            win.close()
        return
    if args.mode_states:
        for cloud in (False, True):
            status = {"connected": True, "eligible": True, "enabled": cloud, "model": "gpt-6.1-terra",
                      "models": [{"slug": "gpt-6.1-terra", "display_name": "GPT-6.1 Terra"}]}
            win = build("dark")
            win.ui.chatgpt_status = lambda st=status: st
            win._refresh_cloud()
            shoot(win, 0, DOCS / ("app-mode-cloud.png" if cloud else "app-mode-local.png"))
            ov = win.ui._overlay
            ov.mode.update_status(status)
            ov.state, ov._elapsed, ov._level = "recording", 23, .7
            ov.move(-4000, 200)
            ov.show()
            app.processEvents()
            ov.grab().save(str(DOCS / ("app-overlay-cloud.png" if cloud else "app-overlay.png")), "PNG")
            ov.close()
            win.close()
        return
    if args.clipboard_only:
        shoot_clipboard()
        return
    if args.support_states:
        from support_ui import SupportDialog
        from version import __version__
        for name in ("dark", "light"):
            win = build(name)
            dialog = SupportDialog(win, win.p, lambda: {
                "app_version": __version__, "os": "Windows", "device": "cuda",
                "backend": "faster-whisper", "decode_seconds": 0.8}, lambda payload: None)
            dialog.description.setPlainText("דוגמה בלבד: לאחר לחיצה על הקיצור לא הופיע חיווי הקלטה. ציפיתי לראות את חלונית ההקלטה.")
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(DOCS / f"app-support-{name}.png"), "PNG")
            dialog.close()
            win.close()
        return
    if args.fullscreen_paused:
        win = build("light")
        win.ui.model_status = lambda: {"state": "paused", "device": "cuda",
            "model": DEFAULTS["model"], "fallback": False}
        win._refresh_model_status()
        shoot(win, 2, DOCS / "app-fullscreen-paused.png")
        win.close()
        return
    if args.scan_states:
        for palette in ("dark", "light"):
            win = build(palette)
            win.resize(1020, 760)
            win.ui.chatgpt_status = lambda: {"connected": True, "eligible": True, "enabled": True,
                "model": "demo-model", "models": [{"slug": "demo-model", "display_name": "Demo"}]}
            win._scan_busy = True
            win._scan_cancelling = False
            win._scan_started = time.monotonic()-35
            win._scan_phase_started = time.monotonic()-12
            win._scan_progress_state = {"phase": "analyzing", "checked": 12, "total": 60,
                "batch": 2, "batches": 5, "found": 4, "rejected": 1, "deadline": 45}
            win._on_scan_progress(win._scan_progress_state)
            win._scan_bar.show()
            win._refresh_scan_controls()
            shoot(win, 1, DOCS / f"app-scan-progress-{palette}.png")
            win.ui.can_undo_history_scan = lambda: True
            win._on_scan_result({"status": "completed", "scanned": 60, "total": 60, "corrected": 8,
                "learned": 2, "english": 1, "rejected": 2, "reasons": {"ambiguous": 1, "protected": 1},
                "finished_at": "2026-10-03 18:00", "model": "demo-model", "elapsed_ms": 42000,
                "details": [{"before": "גיטהאב", "after": "GitHub", "kind": "english", "learned": True},
                            {"before": "שגיעות", "after": "שגיאות", "kind": "spelling", "learned": True},
                            {"before": "המסמך החדשה", "after": "המסמך החדש", "kind": "context", "learned": False}]},
                notify=False, refresh=False)
            shoot(win, 1, DOCS / f"app-scan-results-{palette}.png")
            dialog = win._scan_details_dialog()
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(DOCS / f"app-scan-details-{palette}.png"), "PNG")
            dialog.close()
            win.close()
        return
    if args.chatgpt_states:
        for name, status in (
            ("connected", {"active": "demo", "connected": True, "eligible": True,
                           "email": "demo@example.test", "enabled": False, "model": "gpt-6.1-terra",
                           "accounts": [{"key": "demo", "label": "demo@example.test"}],
                           "models": [{"slug": "gpt-6.1-terra", "display_name": "GPT-6.1 Terra"}]}),
            ("timeout", {"connected": False, "enabled": False, "sign_in_error": "timeout"}),
        ):
            win = build("dark")
            win.ui.chatgpt_status = lambda status=status: status
            win._refresh_cloud()
            win.resize(960, 900)
            win._goto(2)
            win._cloud_panel.set_open(True)
            app.processEvents()
            from PySide6.QtWidgets import QScrollArea
            scroll = win._cloud_panel.parentWidget()
            while scroll is not None and not isinstance(scroll, QScrollArea):
                scroll = scroll.parentWidget()
            if scroll is not None:
                scroll.verticalScrollBar().setValue(win._cloud_panel.y())
            shoot(win, 2, DOCS / f"app-chatgpt-{name}-dark.png")
            win.close()
        return
    if args.overlay_only:
        shoot_overlay()
        return
    win = build("dark")
    shoot(win, 0, DOCS / "app-history-dark.png")
    shoot(win, 1, DOCS / "app-dictionary-dark.png")
    win.close()

    win = build("light")
    shoot(win, 0, DOCS / "app-history-light.png")
    win.resize(900, 680)
    shoot(win, 2, DOCS / "app-settings-light.png")
    win.close()

    shoot_clipboard()
    shoot_overlay()

    # Icon as PNG for the README header.
    from make_icon import draw
    draw(128).save(str(DOCS / "icon.png"), "PNG")
    print(f"wrote {DOCS / 'icon.png'}")


def shoot_clipboard():
    from clipui import ClipPicker
    app.setStyleSheet(ui_mod.theme.build_qss(ui_mod.theme.DARK))
    picker = ClipPicker(ui_mod.theme.DARK, on_pick=lambda entry: None)
    picker._force_foreground = lambda: None
    picker._centre = lambda: picker.move(-4000, 200)
    picker._fit_screen = lambda screen: None
    source_docs = Path(__file__).resolve().parent.parent / "docs"
    picker.show_for([
        {"id": "demo-image-1", "kind": "image", "time": "2026-10-25 10:12",
         "name": "תמלול לדוגמה", "path": str(source_docs / "app-history-light.png"), "pinned": True},
        {"id": "demo-image-2", "kind": "image", "time": "2026-10-25 10:11",
         "name": "הגדרות המיקרופון", "path": str(source_docs / "app-settings-light.png")},
        {"id": "demo-link", "kind": "text", "time": "2026-10-25 10:10",
         "text": "https://github.com/MatanCH2020/MyWhisper"},
        {"id": "demo-long", "kind": "text", "time": "2026-10-03 10:00",
         "text": "תכנון היום עם MyWhisper\n\n"
                 "אפשר לקרוא כאן את כל הטקסט, גם כשמדובר בהעתקה ארוכה.\n\n"
                 "1. לעבור על המשימות ולסדר את סדר העדיפויות.\n"
                 "2. להכין את התוכן לפרסום ב-WhatsApp וב-GitHub.\n"
                 "3. לבדוק את התוצאה ולהעתיק אותה לשדה הרצוי.\n\n"
                 "בחירה ברשימה מציגה תצוגה מלאה. כפתור ההעתקה מחזיר את הפריט ללוח."},
        {"id": "demo-short", "kind": "text", "time": "2026-10-03 09:50",
         "text": "להכין thumbnail ולעשות render לפרויקט."},
    ])
    app.processEvents()
    picker.grab().save(str(DOCS / "app-clipboard-dark.png"), "PNG")
    picker.list.setCurrentRow(3)
    app.processEvents()
    picker.grab().save(str(DOCS / "app-clipboard-text-dark.png"), "PNG")
    picker._set_filter("image")
    app.processEvents()
    picker.grab().save(str(DOCS / "app-clipboard-gallery-dark.png"), "PNG")
    app.setStyleSheet(ui_mod.theme.build_qss(ui_mod.theme.LIGHT))
    picker.set_palette(ui_mod.theme.LIGHT)
    app.processEvents()
    picker.grab().save(str(DOCS / "app-clipboard-gallery-light.png"), "PNG")
    picker._set_filter("all")
    picker.list.setCurrentRow(3)
    app.processEvents()
    picker.grab().save(str(DOCS / "app-clipboard-text-light.png"), "PNG")
    picker.close()


def shoot_overlay():
    # Match the application's global RTL, including for --overlay-only.
    from PySide6.QtCore import Qt
    app.setLayoutDirection(Qt.RightToLeft)
    ov = ui_mod.Overlay(lambda: 0.7)
    ov.move(-4000, 200)
    ov.state = "recording"
    ov.frame = 7
    ov._level = 0.7
    ov._elapsed = 23
    ov.show()
    app.processEvents()
    ov.grab().save(str(DOCS / "app-overlay.png"), "PNG")
    ov.close()
    print(f"wrote {DOCS / 'app-overlay.png'}")



if __name__ == "__main__":
    main()
