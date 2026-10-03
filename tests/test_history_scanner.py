"""Grounded lexical edits, opt-in/cancellation, safe learning and undo."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import corrections
import history
import httpx
from cloud_http import CloudHTTP
from chatgpt_auth import ChatGPTAuth
from history_scanner import HistoryScanner, filter_changes, validate_changes
from tests.test_chatgpt import MemoryStore, connected, completed, event, SlowStream


def proposal(key, before="גיטהאב", after="GitHub", kind="english", reusable=True):
    return {"id": key, "before": before, "after": after, "kind": kind, "reusable": reusable}


class ScannerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for module, attr in ((history, "HISTORY_PATH"), (corrections, "CORRECTIONS_PATH"),
                             (corrections, "DICTIONARY_PATH"), (corrections, "ENGLISH_TERMS_PATH")):
            self.enterContext(patch.object(module, attr, root / attr))
        self.enterContext(patch.object(corrections, "_DEFAULT_ENGLISH_TERMS", []))
        self.enterContext(patch.object(corrections, "is_known", return_value=False))
        history.add("אני לא שולח 298 קבצים לגיטהאב היום.")
        self.key = history.load()[0]["id"]
        # Attached Hebrew prefixes are part of the exact phrase and cannot be
        # blindly rewritten globally. Use a separate unprefixed occurrence.
        history.update(self.key, "אני לא שולח 298 קבצים דרך גיטהאב היום.")

    def setup_scanner(self, responder=None, deadline=.5):
        self.requests = []
        def handler(request):
            self.requests.append(json.loads(request.content))
            return responder(request) if responder else httpx.Response(200, content=completed(
                json.dumps({"changes": [proposal(self.key)]})))
        http = CloudHTTP(httpx.MockTransport(handler))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        connected(auth)
        auth.set_enabled(True)
        scanner = HistoryScanner(auth, http, deadline)
        return auth, scanner

    def test_disabled_does_not_send_or_mutate(self):
        auth, scanner = self.setup_scanner()
        auth.set_enabled(False)
        before = history.load()
        self.assertEqual(scanner.scan("gpt-6-luna").status, "disabled")
        self.assertEqual(self.requests, [])
        self.assertEqual(history.load(), before)

    def test_complete_scan_preserves_source_learns_and_undoes_exact_additions(self):
        _, scanner = self.setup_scanner()
        original = history.load()
        result = scanner.scan("gpt-6-luna")
        self.assertEqual((result.status, result.scanned, result.corrected, result.learned, result.english),
                         ("completed", 1, 1, 1, 1))
        self.assertEqual(history.load()[0]["original_text"], original[0]["text"])
        self.assertIn("GitHub", history.load()[0]["text"])
        self.assertEqual(corrections.apply("גיטהאב"), "GitHub")
        self.assertEqual(corrections.english_terms(), ["GitHub"])
        body = self.requests[0]
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertNotIn("tools", body)
        self.assertNotIn("previous_response_id", body)
        self.assertEqual(scanner.undo().corrected, 1)
        self.assertEqual(history.load(), original)
        self.assertEqual(corrections.list_corrections(), {})
        self.assertEqual(corrections.english_terms(), [])

    def test_existing_manual_rule_wins(self):
        corrections.add_correction("גיטהאב", "manual")
        _, scanner = self.setup_scanner()
        self.assertEqual(scanner.scan("gpt-6-luna").learned, 0)
        self.assertEqual(corrections.list_corrections()["גיטהאב"], "manual")
        scanner.undo()
        self.assertEqual(corrections.list_corrections()["גיטהאב"], "manual")

    def test_contextual_change_never_becomes_global_rule(self):
        _, scanner = self.setup_scanner(lambda request: httpx.Response(200, content=completed(
            json.dumps({"changes": [proposal(self.key, "היום", "מייד", "context", True)]}))))
        result = scanner.scan("gpt-6-luna")
        self.assertEqual((result.corrected, result.learned), (1, 0))

    def test_ambiguous_occurrence_in_other_sentence_prevents_global_learning(self):
        history.add("גיטהאב הוא מונח שנכתב כפי שנאמר.")
        _, scanner = self.setup_scanner()
        self.assertEqual(scanner.scan("gpt-6-luna").learned, 0)

    def test_user_edit_during_request_is_not_overwritten_or_learned(self):
        def response(request):
            history.update(self.key, "עריכה ידנית חדשה")
            return httpx.Response(200, content=completed(json.dumps({"changes": [proposal(self.key)]})))
        _, scanner = self.setup_scanner(response)
        result = scanner.scan("gpt-6-luna")
        self.assertEqual((result.corrected, result.learned, result.english, result.skipped), (0, 0, 0, 1))
        self.assertEqual(history.load()[0]["text"], "עריכה ידנית חדשה")

    def test_undo_preserves_newer_history_and_manual_dictionary_edits(self):
        _, scanner = self.setup_scanner()
        scanner.scan("gpt-6-luna")
        history.update(self.key, "שינוי מאוחר")
        corrections.add_correction("גיטהאב", "חדש")
        result = scanner.undo()
        self.assertEqual((result.corrected, result.skipped), (0, 1))
        self.assertEqual(history.load()[0]["text"], "שינוי מאוחר")
        self.assertEqual(corrections.list_corrections()["גיטהאב"], "חדש")

    def test_source_from_existing_cloud_edit_is_preserved(self):
        history.add("גיטהאב עובד.", original_text="מקור מוקדם", edit_status="edited")
        self.key = history.load()[0]["id"]
        _, scanner = self.setup_scanner()
        scanner.scan("gpt-6-luna")
        self.assertEqual(history.load()[0]["original_text"], "מקור מוקדם")

    def test_invalid_or_partial_output_never_changes_history(self):
        for content, status in ((completed('{"unexpected":[]}'), "invalid"),
                                (event("response.output_text.delta", delta="partial"), "incomplete")):
            with self.subTest(status=status):
                _, scanner = self.setup_scanner(lambda request, content=content: httpx.Response(200, content=content))
                before = history.load()
                self.assertEqual(scanner.scan("gpt-6-luna").status, status)
                self.assertEqual(history.load(), before)

    def test_timeout_discards_late_completed_result(self):
        stream = SlowStream([completed(json.dumps({"changes": [proposal(self.key)]}))], .15)
        _, scanner = self.setup_scanner(lambda request: httpx.Response(200, stream=stream), deadline=.04)
        before = history.load()
        self.assertEqual(scanner.scan("gpt-6-luna").status, "timeout")
        self.assertEqual(history.load(), before)

    def test_cancel_and_disable_during_request_discard_result(self):
        for action in ("cancel", "disable", "account"):
            with self.subTest(action=action):
                def response(request):
                    if action == "cancel":
                        scanner.cancel()
                    elif action == "disable":
                        auth.set_enabled(False)
                    else:
                        connected(auth, key="b")
                        auth.select("b")
                    return httpx.Response(200, content=completed(json.dumps({"changes": [proposal(self.key)]})))
                auth, scanner = self.setup_scanner(response)
                before = history.load()
                self.assertIn(scanner.scan("gpt-6-luna").status, {"disabled", "cancelled"})
                self.assertEqual(history.load(), before)

    def test_error_in_later_batch_does_not_apply_earlier_success(self):
        for _ in range(12):
            history.add("תמלול נוסף.")
        def response(request):
            return (httpx.Response(200, content=completed('{"changes":[]}')) if len(self.requests) == 1
                    else httpx.Response(429, json={"error": {"code": "subscription_sharing_usage_limit_exceeded"}}))
        _, scanner = self.setup_scanner(response)
        before = history.load()
        self.assertEqual(scanner.scan("gpt-6-luna").status, "quota")
        self.assertEqual(history.load(), before)

    def test_invalid_spans_numbers_negation_english_and_overlap(self):
        entries = [{"id": "a", "text": "לא שולח 298 קבצים. גיטהאב גיטהאב."}]
        bad = [proposal("a", "298", "299", "context"), proposal("a", "לא שולח", "שולח", "context"),
               proposal("a", "גיטהאב", "GitHub"), proposal("a", "קבצים", "<script>", "english"),
               proposal("other", "קבצים", "files"), proposal("a", "קבצים", "files\u2069"),
               proposal("a", "חסר", "missing")]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_changes(json.dumps({"changes": [value]}), entries)
        with self.assertRaises(ValueError):
            validate_changes(json.dumps({"changes": [proposal("a", "298 קבצים", "298 files"),
                                  proposal("a", "קבצים", "files")]}), entries)

    def test_storage_failure_cannot_report_success(self):
        _, scanner = self.setup_scanner()
        before = history.load()
        with patch.object(history, "_write", return_value=False):
            self.assertEqual(scanner.scan("gpt-6-luna").status, "storage")
        self.assertEqual(history.load(), before)

    def test_learning_failure_reports_partial_and_retains_undo(self):
        _, scanner = self.setup_scanner()
        with patch.object(corrections, "_save_corrections", return_value=False):
            self.assertEqual(scanner.scan("gpt-6-luna").status, "partial_storage")
        self.assertTrue(scanner.can_undo())
        self.assertEqual(scanner.undo().corrected, 1)

    def test_undo_survives_restart_and_backup_failure_blocks_commit(self):
        auth, scanner = self.setup_scanner()
        original = history.load()
        with patch("history_scanner.atomic_save", return_value=False):
            self.assertEqual(scanner.scan("gpt-6-luna").status, "storage")
        self.assertEqual(history.load(), original)
        scanner.scan("gpt-6-luna")
        restarted = HistoryScanner(auth, scanner.editor.http)
        self.assertTrue(restarted.can_undo())
        self.assertEqual(restarted.undo().corrected, 1)
        self.assertEqual(history.load(), original)
        self.assertEqual(corrections.list_corrections(), {})

    def test_bad_proposal_does_not_discard_valid_corrections_or_later_batches(self):
        for _ in range(12):
            history.add("תמלול נוסף.")
        def response(request):
            if len(self.requests) == 1:
                return httpx.Response(200, content=completed(json.dumps({"changes": [proposal("missing")]})))
            return httpx.Response(200, content=completed(json.dumps({"changes": [proposal(self.key),
                proposal(self.key, "298", "299", "context")]})))
        _, scanner = self.setup_scanner(response)
        updates = []
        result = scanner.scan("gpt-6-luna", detail=updates.append)
        self.assertEqual((result.status, result.scanned, result.total, result.corrected, result.rejected),
                         ("completed", 13, 13, 1, 2))
        self.assertEqual(result.reasons, {"format": 1, "protected": 1})
        self.assertEqual(result.details[0]["before"], "גיטהאב")
        self.assertTrue(result.details[0]["learned"])
        self.assertEqual([u["phase"] for u in updates].count("analyzing"), 2)
        self.assertEqual(updates[-1]["checked"], 13)
        self.assertEqual(scanner.report()["rejected"], 2)
        self.assertEqual(HistoryScanner(scanner.auth, scanner.editor.http).report()["corrected"], 1)
        scanner.undo()
        self.assertEqual(scanner.report()["status"], "undone")

    def test_overlap_rejects_both_alternatives_not_the_first_one_only(self):
        entries = [{"id": "a", "text": "לא שולח 298 קבצים דרך גיטהאב"}]
        patches = [proposal("a", "298 קבצים", "298 files", "context"),
                   proposal("a", "קבצים", "documents"), proposal("a")]
        accepted, rejected = filter_changes(json.dumps({"changes": patches}), entries)
        self.assertEqual([p["after"] for p in accepted], ["GitHub"])
        self.assertEqual(rejected, {"overlap": 2})

    def test_cancel_before_worker_starts_does_not_get_cleared(self):
        _, scanner = self.setup_scanner()
        ticket = scanner.prepare()
        scanner.cancel()
        result = scanner.scan("gpt-6-luna", ticket=ticket)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.requests, [])

    def test_no_changes_and_rejected_only_have_distinct_persistent_results(self):
        _, scanner = self.setup_scanner(lambda request: httpx.Response(200, content=completed('{"changes":[]}')))
        result = scanner.scan("gpt-6-luna")
        self.assertEqual((result.status, result.scanned, result.corrected, result.rejected), ("completed", 1, 0, 0))
        self.assertEqual(scanner.report()["details"], [])
        _, scanner = self.setup_scanner(lambda request: httpx.Response(200, content=completed(json.dumps({
            "changes": [proposal(self.key, "298", "299", "context")]}))))
        result = scanner.scan("gpt-6-luna")
        self.assertEqual((result.status, result.scanned, result.corrected, result.rejected), ("completed", 1, 0, 1))
        self.assertEqual(scanner.report()["reasons"], {"protected": 1})

    def test_report_persistence_failure_does_not_hide_committed_result(self):
        _, scanner = self.setup_scanner()
        from safe_json import atomic_save
        with patch("history_scanner.atomic_save", side_effect=lambda path, value:
                   False if path == scanner.report_path else atomic_save(path, value)):
            result = scanner.scan("gpt-6-luna")
        self.assertEqual((result.status, result.corrected, result.report_saved), ("completed", 1, False))

    def test_long_entry_progress_counts_records_not_context_windows(self):
        history.update(self.key, "a " * 6000)
        _, scanner = self.setup_scanner(lambda request: httpx.Response(200, content=completed('{"changes":[]}')))
        updates = []
        result = scanner.scan("gpt-6-luna", detail=updates.append)
        self.assertEqual((result.scanned, result.total), (1, 1))
        waiting = [u for u in updates if u["phase"] == "analyzing"]
        self.assertEqual(len(waiting), 2)
        self.assertEqual([u["checked"] for u in waiting], [0, 0])
        self.assertEqual(updates[-1]["checked"], 1)
