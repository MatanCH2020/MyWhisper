"""Central logging setup — UTF-8 rotating file + safe console output.

The app usually runs headless (via run_mywishper.vbs), so plain prints vanish;
and when a console *is* attached, Windows code pages choke on Hebrew (charmap
errors). setup() routes every module's logging to mywhisper.log (UTF-8) and,
when stdout exists, mirrors to the console with UTF-8 reconfiguration.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "mywhisper.log"


def setup():
    root = logging.getLogger()
    if root.handlers:  # already configured
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname).1s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
