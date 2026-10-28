"""Privacy boundary, delivery acknowledgement and UI retries; no live network."""
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import support
from support import ReportError
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from support_ui import SupportDialog
import theme

qapp = QApplication.instance() or QApplication([])
TECH = {"app_version": "1.14.1", "os": "Windows", "device": "cuda", "decode_seconds": 0.8}


class Reply:
    def __init__(self, body=None, status=201):
        self.status = status
        self.body = json.dumps(body if body is not None else {"ok": True, "report_id": "MW-123ABC"}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.body[:limit]


class Opener:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.requests = reply or Reply(), error, []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return self.reply


def payload():
    return support.make_report("other", "Synthetic support verification only", "", TECH)


class SupportTest(unittest.TestCase):
    def test_allowlist_never_forwards_personal_or_nested_data(self):
        data = dict(TECH, transcript="PRIVATE", clipboard="PRIVATE", initial_prompt="PRIVATE",
                    token="PRIVATE", fallback_reason="C:/Users/PRIVATE", log={"text": "PRIVATE"},
                    peak=float("nan"), rms=True, capture_seconds=-1)
        self.assertEqual(support.clean_diagnostics(data), TECH)
        self.assertNotIn("PRIVATE", json.dumps(support.make_report("other", "Synthetic example", "", data)))

    def test_diagnostics_does_not_load_model_or_inspect_files(self):
        class Fake:
            device = "cpu"
            last_diagnostics = {"elapsed": 1.25, "duration": 3, "silent": False, "text": "PRIVATE"}
            def load(self):
                raise AssertionError("must not load")
        data = support.diagnostics({"backend": "faster-whisper", "model": "ivrit-ai/whisper-large-v3-ct2",
                                    "input_device": "PRIVATE", "initial_prompt": "PRIVATE"}, Fake())
        self.assertEqual(data["decode_seconds"], 1.25)
        self.assertNotIn("PRIVATE", json.dumps(data))

    def test_report_requires_description_and_required_metadata(self):
        for description, metadata in (("short", TECH), ("x" * 6001, TECH), ("valid long description", {})):
            with self.assertRaises(ReportError):
                support.make_report("other", description, "", metadata)

    def test_sends_exact_reviewed_payload_and_requires_commit_receipt(self):
        data, opener = payload(), Opener()
        self.assertEqual(support.submit_report(data, endpoint="https://example.invalid/report", opener=opener), "MW-123ABC")
        request, timeout = opener.requests[0]
        self.assertEqual(json.loads(request.data), data)
        self.assertEqual(request.method, "POST")
        self.assertEqual(timeout, 10)
        self.assertNotIn("Authorization", request.headers)

    def test_unknown_fields_or_nonfinite_values_cannot_reach_network(self):
        for altered in (dict(payload(), transcript="PRIVATE"), dict(payload(), schema_version=True)):
            opener = Opener()
            with self.assertRaises(ReportError):
                support.submit_report(altered, endpoint="https://example.invalid/report", opener=opener)
            self.assertEqual(opener.requests, [])
        data = payload()
        data["diagnostics"]["secret"] = "PRIVATE"
        with self.assertRaises(ReportError):
            support.submit_report(data, endpoint="https://example.invalid/report", opener=Opener())

    def test_missing_receipt_partial_response_or_error_are_never_success(self):
        for reply in (Reply({"ok": True}), Reply({"ok": False, "report_id": "MW-123"}),
                      Reply({"ok": True, "report_id": "<script>"}), Reply(status=202)):
            with self.assertRaises(ReportError):
                support.submit_report(payload(), endpoint="https://example.invalid/report", opener=Opener(reply))

    def test_timeout_redacts_sensitive_exception_and_does_not_retry(self):
        opener = Opener(error=TimeoutError("PRIVATE TOKEN AND TEXT"))
        with self.assertRaises(ReportError) as caught:
            support.submit_report(payload(), endpoint="https://example.invalid/report", opener=opener)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(len(opener.requests), 1)

    def test_redirect_policy_never_resends_report_to_another_host(self):
        self.assertIsNone(support._NoRedirect().redirect_request(None, None, 307, "", {}, "https://other.invalid"))

    def test_rate_limit_and_service_failure_preserve_safe_error(self):
        for status in (400, 401, 409, 413, 429, 503):
            error = urllib.error.HTTPError("https://example.invalid", status, "PRIVATE", {}, io.BytesIO(b"PRIVATE"))
            with self.assertRaises(ReportError) as caught:
                support.submit_report(payload(), endpoint="https://example.invalid/report", opener=Opener(error=error))
            self.assertNotIn("PRIVATE", str(caught.exception))


class SupportDialogTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.fail = True
        def send(data):
            self.calls.append(data.copy())
            if self.fail:
                raise ReportError("Synthetic timeout")
            return "MW-123ABC"
        self.dialog = SupportDialog(None, theme.DARK, lambda: TECH, send)
        self.dialog.show()

    def tearDown(self):
        self.dialog.close()

    def wait_done(self):
        for _ in range(200):
            qapp.processEvents()
            if not self.dialog._busy:
                return
            QTest.qWait(5)
        self.fail_test("worker did not finish")

    def fail_test(self, message):
        raise AssertionError(message)

    def test_open_and_preview_do_not_submit(self):
        qapp.processEvents()
        self.assertEqual(self.calls, [])
        self.assertEqual(json.loads(self.dialog.preview.toPlainText()), TECH)

    def test_failed_send_preserves_draft_and_retry_identity_then_shows_receipt(self):
        self.dialog.description.setPlainText("Synthetic support verification only")
        self.dialog._submit()
        self.wait_done()
        self.assertTrue(self.dialog.isVisible())
        self.assertTrue(self.dialog.send_button.isEnabled())
        self.assertEqual(self.dialog.description.toPlainText(), "Synthetic support verification only")
        self.fail = False
        self.dialog._submit()
        self.wait_done()
        self.assertEqual(self.calls[0], self.calls[1])
        self.assertIn("MW-123ABC", self.dialog.status.text())
        self.assertFalse(self.dialog.send_button.isEnabled())

    def test_edited_report_after_failure_gets_new_identity(self):
        self.dialog.description.setPlainText("Synthetic first description")
        self.dialog._submit()
        self.wait_done()
        self.dialog.description.setPlainText("Synthetic changed description")
        self.dialog._submit()
        self.wait_done()
        self.assertNotEqual(self.calls[0]["request_id"], self.calls[1]["request_id"])


if __name__ == "__main__":
    unittest.main()
