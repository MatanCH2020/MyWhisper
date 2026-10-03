"""OAuth links open an installed executable, bypassing embedded URL handlers."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import external_browser as browser

URL = "https://auth.openai.com/api/accounts/authorize?state=test&code_challenge=test"
BROWSERS = {"chrome": {"name": "Google Chrome", "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe"},
            "edge": {"name": "Microsoft Edge", "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"}}


class ExternalBrowserTests(unittest.TestCase):
    def test_explicit_chrome_launches_literal_url_without_shell(self):
        with patch.object(browser, "installed_browsers", return_value=BROWSERS), \
             patch.object(browser.subprocess, "Popen") as launch:
            self.assertTrue(browser.open_external_browser(URL, "chrome"))
        self.assertEqual(launch.call_args.args, ([BROWSERS["chrome"]["path"], URL],))
        self.assertIs(launch.call_args.kwargs["shell"], False)

    def test_system_resolves_native_default(self):
        with patch.object(browser, "installed_browsers", return_value=BROWSERS), \
             patch.object(browser, "_default_browser", return_value="edge"), \
             patch.object(browser.subprocess, "Popen") as launch:
            self.assertTrue(browser.open_external_browser(URL))
        self.assertEqual(launch.call_args.args[0][0], BROWSERS["edge"]["path"])

    def test_embedded_default_falls_back_to_installed_browser(self):
        with patch.object(browser, "installed_browsers", return_value=BROWSERS), \
             patch.object(browser, "_default_browser", return_value=""), \
             patch.object(browser.subprocess, "Popen") as launch:
            self.assertTrue(browser.open_external_browser(URL))
        self.assertEqual(launch.call_args.args[0][0], BROWSERS["chrome"]["path"])

    def test_arbitrary_executable_or_missing_browser_rejected(self):
        with patch.object(browser, "installed_browsers", return_value=BROWSERS), \
             patch.object(browser.subprocess, "Popen") as launch:
            self.assertFalse(browser.open_external_browser(URL, r"C:\evil.exe"))
            self.assertFalse(browser.open_external_browser(URL, "firefox"))
        launch.assert_not_called()

    def test_insecure_or_non_openai_urls_rejected(self):
        with patch.object(browser.subprocess, "Popen") as launch:
            for url in ("http://auth.openai.com/api/accounts/authorize", "https://evil.test/",
                        "https://auth.openai.com.evil.test/api/accounts/authorize", "file:///C:/evil.exe"):
                self.assertFalse(browser.open_external_browser(url, "chrome"))
        launch.assert_not_called()

    def test_usage_link_uses_selected_external_browser(self):
        with patch.object(browser, "installed_browsers", return_value=BROWSERS), \
             patch.object(browser.subprocess, "Popen") as launch:
            self.assertTrue(browser.open_external_browser("https://chatgpt.com/settings/usage", "chrome"))
        self.assertEqual(launch.call_args.args[0][0], BROWSERS["chrome"]["path"])

    def test_launch_failure_returns_false(self):
        with patch.object(browser, "installed_browsers", return_value=BROWSERS), \
             patch.object(browser.subprocess, "Popen", side_effect=OSError):
            self.assertFalse(browser.open_external_browser(URL, "chrome"))

    def test_registry_selects_known_browser_only(self):
        with patch.object(browser, "_registry_value", side_effect=["MSEdgeHTM", '"' + BROWSERS["edge"]["path"] + '" --single-argument "%1"']):
            self.assertEqual(browser._default_browser(BROWSERS), "edge")
        with patch.object(browser, "_registry_value", side_effect=["Embedded", r'"C:\Apps\Codex.exe" "%1"']):
            self.assertEqual(browser._default_browser(BROWSERS), "")
