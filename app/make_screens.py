"""Generate the README screenshots (docs/*.png) with curated demo data.

Renders the real Qt UI offscreen — no personal history/corrections are read,
so the images are safe to publish. Re-run after UI changes:

    .\\.venv\\Scripts\\python app\\make_screens.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

import corrections
import ui as ui_mod
from config import DEFAULTS

DOCS = Path(__file__).resolve().parent.parent / "docs"

DEMO_HISTORY = [
    {"id": "d1", "time": "2026-07-18 09:42",
     "text": "שלום, זה תמלול לדוגמה שנכתב עם MyWhisper — בלי לגעת במקלדת!"},
    {"id": "d2", "time": "2026-07-18 09:40",
     "text": "צריך להוסיף thumbnail לסרטון החדש, ואז לעשות render לפרויקט."},
    {"id": "d3", "time": "2026-07-18 09:37",
     "text": "מה השעה עכשיו? תזכיר לי פגישה מחר בעשר בבוקר."},
]
DEMO_CORRECTIONS = {"תאמנל": "thumbnail", "וייס פר": "Whisper", "רנדר": "render"}


def build(theme_name):
    cfg = dict(DEFAULTS)
    cfg["theme"] = theme_name
    ctl = ui_mod.AppUI(
        cfg, lambda: 0.6, lambda c: None,
        get_history=lambda: DEMO_HISTORY,
        clear_history=lambda: None,
        test_sound=lambda n: None,
        import_sound=lambda n, p: False,
        flag_tokens=corrections.flag_tokens,
        list_corrections=lambda: DEMO_CORRECTIONS,
        format_bidi=corrections.format_bidi,
    )
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
    DOCS.mkdir(exist_ok=True)

    win = build("dark")
    shoot(win, 0, DOCS / "app-history-dark.png")
    shoot(win, 1, DOCS / "app-dictionary-dark.png")
    win.close()

    win = build("light")
    shoot(win, 0, DOCS / "app-history-light.png")
    shoot(win, 2, DOCS / "app-settings-light.png")
    win.close()

    # Recording HUD (the floating overlay).
    ov = ui_mod.Overlay(lambda: 0.7)
    ov.move(-4000, 200)
    ov.state = "recording"
    ov.frame = 7
    ov.show()
    app.processEvents()
    ov.grab().save(str(DOCS / "app-overlay.png"), "PNG")
    ov.close()
    print(f"wrote {DOCS / 'app-overlay.png'}")

    # Icon as PNG for the README header.
    from make_icon import draw
    draw(128).save(str(DOCS / "icon.png"), "PNG")
    print(f"wrote {DOCS / 'icon.png'}")


if __name__ == "__main__":
    main()
