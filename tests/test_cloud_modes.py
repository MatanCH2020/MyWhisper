"""Mode interaction and session-bound cloud requests, without live accounts."""
import asyncio
import json
import time
import unittest
from unittest.mock import Mock, patch

from tests.test_chatgpt import connected, completed, MemoryStore
from chatgpt_auth import ChatGPTAuth, REQUIRED_SCOPES
from cloud_http import CloudHTTP
from text_editor import TextEditor
import httpx
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication, QMessageBox
from mode_controls import ModeSelector

qapp = QApplication.instance() or QApplication([])
READY = {"connected": True, "eligible": True, "model": "gpt-6-luna",
         "models": [{"slug": "gpt-6-luna", "display_name": "GPT-6 Luna"}]}


class ModeTests(unittest.TestCase):
    def test_one_click_each_way_and_no_focus(self):
        mode = ModeSelector(nonactivating=True)
        self.addCleanup(mode.deleteLater)
        mode.update_status(READY)
        spy = QSignalSpy(mode.selected)
        QTest.mouseClick(mode.cloud, Qt.LeftButton)
        self.assertEqual(spy.at(0), [True])
        mode.update_status({**READY, "enabled": True})
        self.assertIn("OpenAI", mode.detail.text())
        self.assertIn("GPT-6 Luna", mode.detail.text())
        self.assertTrue(mode.cloud.isChecked())
        self.assertEqual(mode.cloud.focusPolicy(), Qt.NoFocus)
        QTest.mouseClick(mode.local, Qt.LeftButton)
        self.assertEqual(spy.at(1), [False])

    def test_no_account_is_local_and_busy_locks_both_modes(self):
        mode = ModeSelector()
        self.addCleanup(mode.deleteLater)
        mode.update_status({})
        self.assertTrue(mode.local.isChecked())
        self.assertFalse(mode.cloud.isEnabled())
        mode.update_status({**READY, "enabled": True}, busy=True)
        self.assertTrue(mode.cloud.isChecked())
        self.assertFalse(mode.cloud.isEnabled())
        self.assertFalse(mode.local.isEnabled())


class SessionTests(unittest.TestCase):
    def setup_auth(self, handler):
        http = CloudHTTP(httpx.MockTransport(handler))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        connected(auth)
        return auth, TextEditor(auth, http, deadline=.4)

    def test_expired_enabled_session_refreshes_once_before_inference(self):
        paths = []
        async def handler(request):
            paths.append(request.url.path)
            if request.url.path.endswith('/token'):
                await asyncio.sleep(.02)
                return httpx.Response(200, json={"token_type": "bearer", "expires_in": 3600,
                    "scope": " ".join(REQUIRED_SCOPES), "access_token": "new", "refresh_token": "rotated"})
            self.assertEqual(request.headers['Authorization'], 'Bearer new')
            return httpx.Response(200, content=completed("done"))
        auth, editor = self.setup_auth(handler)
        auth._active()['expires_at'] = 0
        auth.set_enabled(True)
        auth.schedule_refresh()
        self.assertEqual(editor.edit("original", "gpt-6-luna").text, "done")
        self.assertEqual(paths, ['/api/accounts/oauth/token', '/v1/responses'])

    def test_old_recording_cannot_be_sent_after_account_or_mode_change(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=completed("wrong account"))
        auth, editor = self.setup_auth(handler)
        auth.set_enabled(True)
        old = auth.generation
        auth.set_enabled(False)
        auth.set_enabled(True)
        self.assertEqual(editor.edit("private", "gpt-6-luna", generation=old).status, "disabled")
        self.assertEqual(requests, [])

    def test_refresh_uses_the_total_deadline_without_starting_late_inference(self):
        paths = []
        async def handler(request):
            paths.append(request.url.path)
            await asyncio.sleep(.2)
            return httpx.Response(200, json={"token_type": "bearer", "expires_in": 3600,
                "scope": " ".join(REQUIRED_SCOPES), "access_token": "new", "refresh_token": "rotated"})
        auth, editor = self.setup_auth(handler)
        auth._active()['expires_at'] = 0
        auth.set_enabled(True)
        editor.deadline = .04
        started = time.monotonic()
        result = editor.edit('original', 'gpt-6-luna')
        self.assertEqual(result.status, 'timeout')
        self.assertLess(time.monotonic() - started, .15)
        self.assertEqual(paths, ['/api/accounts/oauth/token'])

    def test_catalog_error_does_not_mislabel_saved_identity(self):
        auth, _ = self.setup_auth(lambda request: httpx.Response(503))
        with self.assertRaises(Exception):
            auth.catalog()
        self.assertTrue(auth.status()['connected'])
        self.assertEqual(auth.status()['models'], READY['models'])

    def test_email_can_expand_but_unbounded_output_is_rejected(self):
        auth, editor = self.setup_auth(lambda request: httpx.Response(200, content=completed('a' * 500)))
        auth.set_enabled(True)
        self.assertEqual(editor.edit('Draft a short email.', 'gpt-6-luna').status, 'edited')
        self.assertFalse(editor._valid_output('a' * 100001, 'a' * 50000))


if __name__ == '__main__':
    unittest.main()
