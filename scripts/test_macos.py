"""Run portable and Mac suites; Windows native/cloud suites run in Windows CI."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
WINDOWS_SUITES = {"test_chatgpt", "test_delivery_guard", "test_fullscreen_pause",
                  "test_stability", "test_transcriber", "test_unified_history", "test_updater"}

if __name__ == "__main__":
    # Existing QClipboard tests exercise shared storage/privacy contracts using Qt.
    # Native NSPasteboard behavior has its own mock-based suite.
    sys.path.insert(0, str(ROOT / "app"))
    import clipwatch
    clipwatch.IS_MAC = False
    suite = unittest.TestSuite()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if path.stem not in WINDOWS_SUITES:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromName("tests." + path.stem))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
