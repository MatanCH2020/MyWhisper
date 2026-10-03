"""OAuth, isolation, opt-in, bounded streaming and delivery without live accounts."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import ANY, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from PySide6.QtCore import QObject
import config
import history
import main
from cloud_http import CloudHTTP
from chatgpt_auth import AUTH, RESOURCE, REQUIRED_SCOPES, AuthError, ChatGPTAuth, CredentialStore, dpapi
from text_editor import EditResult, TextEditor


class MemoryStore:
    def __init__(self):
        self.data = {"accounts": {}, "active": ""}

    def load(self):
        return self.data

    def save(self, data):
        self.data = data


def connected(auth, key="a", token="account-a"):
    auth.data["accounts"][key] = {"email": key + "@example.test", "client_id": "oaiapp_" + key,
        "subject": key, "scopes": list(REQUIRED_SCOPES), "access_token": token,
        "refresh_token": "refresh-" + key, "expires_at": time.time() + 3600,
        "models": [{"slug": "gpt-6-luna", "display_name": "GPT-6 Luna"}]}
    auth.data["active"] = key


def event(kind, **fields):
    return ("data: " + json.dumps({"type": kind, **fields}) + "\n\n").encode()


def completed(text):
    return event("response.completed", response={"status": "completed", "output": [
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": text}]}]})


class SlowStream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay):
        self.chunks, self.delay = chunks, delay
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


class EditorTests(unittest.TestCase):
    def setup_editor(self, response, deadline=.3):
        self.requests = []
        async def handler(request):
            self.requests.append(request)
            return response
        http = CloudHTTP(httpx.MockTransport(handler))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        connected(auth)
        editor = TextEditor(auth, http, deadline)
        return auth, editor

    def test_disabled_never_sends_or_refreshes_even_with_credentials(self):
        auth, editor = self.setup_editor(httpx.Response(200, content=completed("clean")))
        auth._active()["expires_at"] = 0
        auth.schedule_refresh()
        self.assertEqual(editor.edit("original", "gpt-6-luna").status, "disabled")
        self.assertEqual(self.requests, [])

    def test_only_completed_text_is_accepted_and_payload_is_text_only(self):
        auth, editor = self.setup_editor(httpx.Response(200, content=
            event("response.output_text.delta", delta="partial") + completed("clean")))
        auth.set_enabled(True)
        result = editor.edit("original", "gpt-6-luna")
        self.assertEqual((result.text, result.status), ("clean", "edited"))
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        body = json.loads(request.content)
        self.assertEqual(str(request.url), RESOURCE + "/responses")
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        self.assertEqual(body["reasoning"], {"effort": "none"})
        self.assertEqual(body["input"], [{"role": "user", "content": "original"}])
        self.assertNotIn("tools", body)
        self.assertNotIn("previous_response_id", body)

    def test_interrupted_stream_discards_all_deltas(self):
        auth, editor = self.setup_editor(httpx.Response(200, content=event("response.output_text.delta", delta="partial")))
        auth.set_enabled(True)
        result = editor.edit("original", "gpt-6-luna")
        self.assertEqual((result.text, result.status), ("original", "incomplete"))

    def test_usage_failure_after_deltas_keeps_local_text(self):
        auth, editor = self.setup_editor(httpx.Response(200, content=
            event("response.output_text.delta", delta="partial") +
            event("response.failed", response={"error": {"code": "subscription_sharing_usage_limit_exceeded"}})))
        auth.set_enabled(True)
        result = editor.edit("original", "gpt-6-luna")
        self.assertEqual((result.text, result.status), ("original", "quota"))

    def test_total_deadline_even_when_server_drips_chunks_and_late_result_is_ignored(self):
        stream = SlowStream([event("response.output_text.delta", delta="x")] * 8 + [completed("late")], .025)
        auth, editor = self.setup_editor(httpx.Response(200, stream=stream), deadline=.09)
        auth.set_enabled(True)
        start = time.monotonic()
        result = editor.edit("original", "gpt-6-luna")
        self.assertLess(time.monotonic() - start, .18)
        self.assertEqual((result.text, result.status), ("original", "timeout"))
        time.sleep(.08)
        self.assertTrue(stream.closed)
        self.assertEqual(result.text, "original")

    def test_disable_cancels_inflight_edit(self):
        auth, editor = self.setup_editor(httpx.Response(200, stream=SlowStream([completed("late")], .25)))
        auth.set_enabled(True)
        results = []
        worker = threading.Thread(target=lambda: results.append(editor.edit("original", "gpt-6-luna")))
        worker.start()
        time.sleep(.025)
        auth.set_enabled(False)
        editor.cancel()
        worker.join(.15)
        self.assertFalse(worker.is_alive())
        self.assertEqual((results[0].text, results[0].status), ("original", "disabled"))

    def test_http_auth_quota_and_server_fail_open(self):
        for code, status in ((401, "authorization"), (403, "authorization"), (429, "quota"), (500, "connection")):
            with self.subTest(code=code):
                auth, editor = self.setup_editor(httpx.Response(code))
                auth.set_enabled(True)
                result = editor.edit("original", "gpt-6-luna")
                self.assertEqual((result.text, result.status), ("original", status))

    def test_missing_or_unlisted_model_does_not_send(self):
        auth, editor = self.setup_editor(httpx.Response(200))
        auth.set_enabled(True)
        self.assertEqual(editor.edit("original", "unknown").status, "authorization")
        self.assertEqual(self.requests, [])

    def test_switch_isolates_bearer_and_requires_reactivation(self):
        auth, editor = self.setup_editor(httpx.Response(200, content=completed("clean")))
        connected(auth, "b", "account-b")
        auth.select("a")
        self.assertEqual(editor.edit("original", "gpt-6-luna").status, "disabled")
        auth.set_enabled(True)
        editor.edit("original", "gpt-6-luna")
        self.assertEqual(self.requests[-1].headers["Authorization"], "Bearer account-a")
        auth.select("b")
        auth.set_enabled(True)
        editor.edit("original", "gpt-6-luna")
        self.assertEqual(self.requests[-1].headers["Authorization"], "Bearer account-b")


class OAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.private.public_key()))
        cls.jwk.update(kid="test-key", use="sig", alg="RS256")

    def setup_auth(self, identity_changes=None, callback_changes=None, scopes=None):
        self.callback_pages = []
        self.callback_done = threading.Event()
        self.params, self.calls = {}, []
        async def handler(request):
            self.calls.append(request)
            path = request.url.path
            if path.endswith("jwks.json"):
                return httpx.Response(200, json={"keys": [self.jwk]})
            if path.endswith("/models"):
                return httpx.Response(200, json={"models": [
                    {"slug": "hidden", "visibility": "hide"},
                    {"slug": "gpt-6-luna", "display_name": "Luna", "visibility": "list"}]})
            if path.endswith("/token"):
                identity = {"iss": AUTH, "aud": "oaiapp_test", "exp": time.time() + 300,
                            "sub": "person-a", "nonce": self.params["nonce"]}
                identity.update(identity_changes or {})
                return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh",
                    "id_token": jwt.encode(identity, self.private, algorithm="RS256", headers={"kid": "test-key"}),
                    "token_type": "Bearer", "expires_in": 3600,
                    "scope": scopes if scopes is not None else " ".join(REQUIRED_SCOPES)})
            raise AssertionError(str(request.url))
        http = CloudHTTP(httpx.MockTransport(handler))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        def browser(url):
            self.assertTrue(auth.status()["signing_in"])
            self.params.update({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})
            query = {"state": self.params["state"], "client_id": "oaiapp_test", "code": "code"}
            query.update(callback_changes or {})
            def callback():
                try:
                    with urlopen(self.params["redirect_uri"] + "?" + urlencode(query), timeout=2) as response:
                        self.callback_pages.append((response.status, response.read().decode("utf-8")))
                except HTTPError as error:
                    self.callback_pages.append((error.code, error.read().decode("utf-8")))
                except Exception:
                    pass
                finally:
                    self.callback_done.set()
            threading.Thread(target=callback, daemon=True).start()
            return True
        return auth, browser

    def test_pkce_signin_validates_and_does_not_activate(self):
        auth, browser = self.setup_auth()
        status = auth.sign_in(browser=browser, timeout=1)
        self.assertTrue(status["connected"])
        self.assertFalse(status["enabled"])
        self.assertFalse(status["signing_in"])
        self.assertEqual(status["sign_in_error"], "")
        self.assertTrue(status["eligible"])
        self.assertEqual(status["models"][0]["slug"], "gpt-6-luna")
        self.assertTrue(self.callback_done.wait(1))
        self.assertEqual(self.callback_pages[0][0], 200)
        self.assertIn("החשבון מחובר ל-MyWhisper", self.callback_pages[0][1])
        self.assertNotIn("code=", self.callback_pages[0][1])
        self.assertEqual(self.params["client_id"], "dynamic_agent_client")
        self.assertEqual(self.params["agent_name_hint"], "MyWhisper")
        self.assertEqual(self.params["code_challenge_method"], "S256")
        exchange = parse_qs(self.calls[0].content.decode())
        import base64, hashlib
        challenge = base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"][0].encode()).digest()).decode().rstrip("=")
        self.assertEqual(challenge, self.params["code_challenge"])
        self.assertEqual(exchange["client_id"], ["oaiapp_test"])
        self.assertEqual(exchange["redirect_uri"], [self.params["redirect_uri"]])
        self.assertNotIn("client_secret", exchange)

    def test_nonce_issuer_audience_expiry_and_subject_are_checked(self):
        for changes in ({"nonce": "wrong"}, {"iss": "https://evil.test"}, {"aud": "other"},
                        {"exp": time.time() - 10}, {"sub": ""}):
            with self.subTest(changes=changes):
                auth, browser = self.setup_auth(identity_changes=changes)
                with self.assertRaises(AuthError):
                    auth.sign_in(browser=browser, timeout=1)
                self.assertEqual(auth.status()["accounts"], [])
                self.assertTrue(self.callback_done.wait(1))
                self.assertEqual(self.callback_pages[0][0], 400)
                self.assertIn("ההתחברות לא הושלמה", self.callback_pages[0][1])

    def test_invalid_state_never_exchanges_code(self):
        auth, browser = self.setup_auth(callback_changes={"state": "wrong"})
        with self.assertRaises(AuthError):
            auth.sign_in(browser=browser, timeout=.06)
        self.assertEqual(self.calls, [])
        self.assertFalse(auth.status()["signing_in"])
        self.assertEqual(auth.status()["sign_in_error"], "timeout")

    def test_failed_new_login_keeps_existing_connection_and_retry_clears_failure(self):
        auth, browser = self.setup_auth()
        connected(auth)
        with self.assertRaises(AuthError):
            auth.sign_in(browser=lambda _: False)
        self.assertTrue(auth.status()["connected"])
        self.assertEqual(auth.status()["sign_in_error"], "browser")
        auth.sign_in(browser=browser, timeout=1)
        self.assertEqual(auth.status()["sign_in_error"], "")

    def test_invalid_signature_is_rejected(self):
        auth, _ = self.setup_auth()
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode({"iss": AUTH, "aud": "oaiapp_test", "exp": time.time() + 60,
            "sub": "person-a", "nonce": "nonce"}, private, algorithm="RS256", headers={"kid": "test-key"})
        with self.assertRaises(AuthError):
            auth.http.call(auth._validate_identity(token, "oaiapp_test", "nonce"))

    def test_small_clock_skew_is_tolerated_but_wrong_nonce_is_not(self):
        auth, browser = self.setup_auth(identity_changes={"iat": time.time() + 2})
        self.assertTrue(auth.sign_in(browser=browser, timeout=1)["connected"])
        auth, browser = self.setup_auth(identity_changes={"iat": time.time() + 2, "nonce": "wrong"})
        with self.assertRaises(AuthError):
            auth.sign_in(browser=browser, timeout=1)

    def test_identity_diagnostics_never_log_token_or_private_claims(self):
        auth, browser = self.setup_auth(identity_changes={"email": "private@example.test", "nonce": "wrong"})
        with self.assertLogs("chatgpt_auth", level="INFO") as captured, self.assertRaises(AuthError):
            auth.sign_in(browser=browser, timeout=1)
        messages = " ".join(captured.output)
        self.assertIn("nonce_or_subject", messages)
        self.assertNotIn("private@example.test", messages)
        self.assertNotIn("person-a", messages)
        self.assertNotIn(self.params["nonce"], messages)

    def test_failed_credential_save_never_reports_connected(self):
        auth, browser = self.setup_auth()
        save = auth.store.save
        def fail_account_save(data):
            if data["accounts"]:
                raise AuthError("storage")
            save(data)
        auth.store.save = fail_account_save
        with self.assertRaises(AuthError):
            auth.sign_in(browser=browser, timeout=1)
        self.assertFalse(auth.status()["connected"])
        self.assertTrue(self.callback_done.wait(1))
        self.assertIn("לא ניתן לשמור", self.callback_pages[0][1])

    def test_returning_identity_cannot_replace_selected_account(self):
        auth, browser = self.setup_auth(identity_changes={"sub": "other-person"})
        connected(auth, "saved")
        auth._active().update(client_id="oaiapp_test", subject="person-a")
        with self.assertRaises(AuthError):
            auth.sign_in(returning=True, browser=browser, timeout=1)
        self.assertEqual(auth.data["active"], "saved")
        self.assertEqual(auth._active()["access_token"], "account-a")

    def test_returning_client_id_mismatch_never_exchanges(self):
        auth, browser = self.setup_auth(callback_changes={"client_id": "different"})
        connected(auth)
        auth._active()["client_id"] = "oaiapp_test"
        with self.assertRaises(AuthError):
            auth.sign_in(returning=True, browser=browser, timeout=1)
        self.assertEqual(self.calls, [])

    def test_oauth_denial_never_exchanges_code(self):
        auth, browser = self.setup_auth(callback_changes={"error": "access_denied"})
        with self.assertRaises(AuthError):
            auth.sign_in(browser=browser, timeout=1)
        self.assertEqual(self.calls, [])

    def test_identity_only_grant_does_not_fetch_models_or_enable(self):
        auth, browser = self.setup_auth(scopes="openid profile email offline_access")
        status = auth.sign_in(browser=browser, timeout=1)
        self.assertFalse(status["eligible"])
        self.assertFalse(status["enabled"])
        self.assertEqual(len(self.calls), 2)

    def test_returning_registration_reuses_host_client_and_hints(self):
        auth, browser = self.setup_auth()
        auth.sign_in(browser=browser, timeout=1)
        host = self.params["ext_agent_host_id"]
        self.params.clear()
        auth.sign_in(returning=True, browser=browser, timeout=1)
        self.assertEqual(self.params["client_id"], "oaiapp_test")
        self.assertEqual(self.params["ext_agent_host_id"], host)
        self.assertIn("id_token_hint", self.params)
        self.assertNotIn("agent_name_hint", self.params)
        self.assertEqual(len(auth.status()["accounts"]), 1)


class StorageAndRefreshTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_dpapi_roundtrip_is_not_plaintext(self):
        secret = b'{"access_token":"not-a-real-token"}'
        encrypted = dpapi(secret)
        self.assertNotIn(secret, encrypted)
        self.assertEqual(dpapi(encrypted, decrypt=True), secret)
        with tempfile.TemporaryDirectory() as folder:
            store = CredentialStore(Path(folder) / "test.dpapi")
            store.save({"accounts": {"test": {"access_token": "not-a-real-token"}}})
            self.assertNotIn(b"not-a-real-token", store.path.read_bytes())
            self.assertEqual(store.load()["accounts"]["test"]["access_token"], "not-a-real-token")

    def test_corrupt_store_blocks_replacement(self):
        store = MemoryStore()
        store.load = Mock(side_effect=AuthError("storage"))
        http = CloudHTTP()
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, store)
        with self.assertRaises(AuthError):
            auth.sign_in(browser=Mock())

    def test_refresh_serialized_rotates_atomically_and_only_while_enabled(self):
        calls = []
        async def handler(request):
            calls.append(request)
            await asyncio.sleep(.025)
            return httpx.Response(200, json={"token_type": "Bearer", "expires_in": 3600,
                "scope": " ".join(REQUIRED_SCOPES), "access_token": "new-access", "refresh_token": "new-refresh"})
        http = CloudHTTP(httpx.MockTransport(handler))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        connected(auth)
        auth._active()["expires_at"] = 0
        auth.schedule_refresh()
        self.assertEqual(calls, [])
        auth.set_enabled(True)
        for _ in range(5):
            auth.schedule_refresh()
        auth._refresh.result(1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(auth.access(), "new-access")
        self.assertEqual(auth._active()["refresh_token"], "new-refresh")
        form = parse_qs(calls[0].content.decode())
        self.assertEqual(form["client_id"], ["oaiapp_a"])
        self.assertNotIn("scope", form)
        auth.set_enabled(False)
        auth._active()["expires_at"] = 0
        auth.schedule_refresh()
        self.assertEqual(len(calls), 1)

    def test_disconnect_clears_only_selected_account_and_reports_failed_revocation(self):
        http = CloudHTTP(httpx.MockTransport(lambda request: httpx.Response(500)))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        connected(auth, "b", "b-token")
        connected(auth, "a", "a-token")
        auth.set_enabled(True)
        self.assertFalse(auth.disconnect())
        self.assertFalse(auth.enabled)
        self.assertNotIn("access_token", auth.data["accounts"]["a"])
        self.assertEqual(auth.data["accounts"]["a"]["client_id"], "oaiapp_a")
        self.assertEqual(auth.data["accounts"]["b"]["access_token"], "b-token")

    def test_refresh_terminal_error_clears_tokens_keeps_registration(self):
        http = CloudHTTP(httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "invalid_grant"})))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        connected(auth)
        auth._active()["expires_at"] = 0
        auth.set_enabled(True)
        auth.schedule_refresh()
        auth._refresh.result(1)
        self.assertEqual(auth.error, "authorization")
        self.assertIsNone(auth.access())
        self.assertNotIn("refresh_token", auth._active())
        self.assertEqual(auth._active()["client_id"], "oaiapp_a")

    def test_refresh_network_failure_preserves_tokens_and_allows_later_retry(self):
        http = CloudHTTP(httpx.MockTransport(lambda request: httpx.Response(503)))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        connected(auth)
        auth._active()["expires_at"] = 0
        auth.set_enabled(True)
        auth.schedule_refresh()
        auth._refresh.result(1)
        self.assertEqual(auth.error, "")
        self.assertEqual(auth._active()["refresh_token"], "refresh-a")

    def test_revocation_uses_discovery_and_selected_registration(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path.endswith("openid-configuration"):
                return httpx.Response(200, json={"revocation_endpoint": AUTH + "/api/accounts/oauth/revoke"})
            return httpx.Response(200)
        http = CloudHTTP(httpx.MockTransport(handler))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        connected(auth)
        self.assertTrue(auth.disconnect())
        self.assertEqual(parse_qs(calls[1].content.decode()), {
            "token": ["refresh-a"], "token_type_hint": ["refresh_token"], "client_id": ["oaiapp_a"]})

    def test_disabled_cancels_refresh_and_discards_late_replacement(self):
        calls = []
        async def handler(request):
            calls.append(request)
            await asyncio.sleep(.3)
            return httpx.Response(200, json={"token_type": "Bearer", "expires_in": 3600,
                "scope": " ".join(REQUIRED_SCOPES), "access_token": "late", "refresh_token": "late"})
        http = CloudHTTP(httpx.MockTransport(handler))
        self.addCleanup(http.close)
        auth = ChatGPTAuth(http, MemoryStore())
        connected(auth)
        auth._active()["expires_at"] = 0
        auth.set_enabled(True)
        auth.schedule_refresh()
        time.sleep(.02)
        auth.set_enabled(False)
        time.sleep(.04)
        self.assertEqual(auth._active()["refresh_token"], "refresh-a")
        self.assertIsNone(auth._refresh)


class CloudSettingsTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication
        from ui import AppUI, MainWindow
        self.qapp = QApplication.instance() or QApplication([])
        self.ui = AppUI(dict(config.DEFAULTS), lambda: 0, lambda cfg: None, lambda: [],
                        lambda: None, lambda cue: None, lambda cue, path: False)
        self.ui.chatgpt_action = Mock(return_value={"enabled": False, "accounts": [], "models": []})
        self.win = MainWindow(self.ui, self.ui.p)
        self.addCleanup(self.win.deleteLater)
        self.addCleanup(self.ui._overlay.deleteLater)

    def test_construct_and_refresh_settings_perform_no_auth_actions(self):
        self.win._refresh_cloud()
        self.assertFalse(self.win._cloud_switch.isChecked())
        self.ui.chatgpt_action.assert_not_called()

    def test_connection_and_editing_states_are_independent_and_controls_are_gated(self):
        base = {"accounts": [], "models": [{"slug": "luna", "display_name": "Luna"}], "enabled": False}
        cases = [
            ({}, "לא מחובר", "כבויה", False),
            ({"connected": True, "eligible": True, "email": "owner@example.test", "model": "luna"}, "מחובר ל־ChatGPT", "כבויה", True),
            ({"connected": True, "eligible": True, "enabled": True, "model": "luna"}, "מחובר ל־ChatGPT", "פעילה", True),
            ({"connected": True, "eligible": True}, "מחובר ל־ChatGPT", "בחר מודל", False),
            ({"connected": True, "eligible": True, "model": "luna", "models": []}, "מחובר ל־ChatGPT", "בחר מודל", False),
            ({"connected": True, "eligible": False, "active": "a"}, "מחובר ל־ChatGPT", "אינו זכאי", False),
            ({"connected": False, "active": "a", "error": "authorization"}, "נדרשת התחברות מחדש", "כבויה", False),
            ({"signing_in": True}, "ממתין לאישור", "כבויה", False),
        ]
        for changes, connection, editing, ready in cases:
            with self.subTest(changes=changes):
                self.win._refresh_cloud({**base, **changes})
                self.assertIn(connection, self.win._cloud_connection.text())
                self.assertIn(editing, self.win._cloud_status.text())
                self.assertEqual(self.win._cloud_switch.isEnabled(), ready)
        self.ui.chatgpt_action.assert_not_called()

    def test_login_failure_stays_visible_after_polling_without_hiding_existing_account(self):
        for connected_now in (False, True):
            status = {"connected": connected_now, "sign_in_error": "timeout", "email": "owner@example.test"}
            self.ui.chatgpt_status = lambda: status
            for _ in range(3):
                self.win._refresh_cloud()
                self.assertIn("האישור לא התקבל בזמן", self.win._cloud_connection_detail.text())
                self.assertIn("מחובר ל־ChatGPT" if connected_now else "לא מחובר", self.win._cloud_connection.text())
            if connected_now:
                self.assertIn("owner@example.test", self.win._cloud_connection_detail.text())
        self.ui.chatgpt_action.assert_not_called()

    def test_cancel_activation_does_not_opt_in(self):
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "exec"), patch.object(QMessageBox, "clickedButton", return_value=None):
            self.win._cloud_toggle(True)
        self.ui.chatgpt_action.assert_called_once_with("enable", False)

    def test_selecting_model_does_not_enable_cloud(self):
        self.win._cloud_model.addItem("Demo", "demo-model")
        self.win._cloud_model.setCurrentIndex(1)
        self.ui.chatgpt_action.assert_called_once_with("model", "demo-model")

    def test_original_action_uses_saved_source_without_cloud(self):
        from PySide6.QtWidgets import QDialog
        self.ui.get_history = lambda: [{"id": "one", "text": "edited", "original_text": "local"}]
        with patch.object(QDialog, "exec") as show:
            self.win.show_original("one")
            show.assert_called_once()
        self.ui.chatgpt_action.assert_not_called()

    def test_new_upgrade_invalid_config_default_off_and_explicit_setting_persists(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "CONFIG_PATH", Path(folder) / "config.json"):
            self.assertIs(config.load_config()["chatgpt_enabled"], False)
            for old in ({"theme": "light"}, {"llm_polish": True}, {"chatgpt_enabled": "true"}, {"chatgpt_enabled": 1}):
                config.CONFIG_PATH.write_text(json.dumps(old))
                self.assertIs(config.load_config()["chatgpt_enabled"], False)
            config.save_config({"chatgpt_enabled": True, "chatgpt_model": "gpt-6-luna"})
            self.assertIs(config.load_config()["chatgpt_enabled"], True)

    def test_selected_browser_survives_login_and_settings_save(self):
        app = main.Mywishper.__new__(main.Mywishper)
        QObject.__init__(app)
        app.config = self.ui.config
        app.chatgpt = Mock()
        status = {"eligible": False, "models": [], "enabled": False}
        app.chatgpt.status.return_value = status
        app.chatgpt.data = {}
        app._chatgpt_status = Mock(return_value=status)
        app._set_chatgpt_enabled = Mock(return_value=False)
        app._apply_sound_config = Mock()
        app.tray = Mock()
        app._clip_picker = None
        self.ui.chatgpt_action = app._chatgpt_action
        self.ui.chatgpt_browsers = lambda: [{"slug": "system", "name": "System"},
                                          {"slug": "chrome", "name": "Chrome"}]
        # Recreate settings just as a theme change or app restart would.
        from ui import MainWindow
        win = MainWindow(self.ui, self.ui.p)
        self.addCleanup(win.deleteLater)
        url = "https://auth.openai.com/api/accounts/authorize?state=synthetic"
        app.chatgpt.sign_in.side_effect = lambda **kw: kw["browser"](url)
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(config, "CONFIG_PATH", Path(folder) / "config.json"), \
             patch.object(main, "browser_choices", self.ui.chatgpt_browsers), \
             patch.object(main, "open_external_browser", return_value=True) as launch:
            win._cloud_browser.setCurrentIndex(win._cloud_browser.findData("chrome"))
            self.assertEqual(config.load_config()["chatgpt_browser"], "chrome")
            app._chatgpt_action("login")
            launch.assert_called_once_with(url, "chrome")
            app._on_settings_change(self.ui.config)
            restored = config.load_config()
            self.assertEqual(restored["chatgpt_browser"], "chrome")
            self.assertIs(restored["chatgpt_enabled"], False)


class PipelineTests(unittest.TestCase):
    def run_pipeline(self, edited, destination=True, enabled=True):
        app = main.Mywishper.__new__(main.Mywishper)
        QObject.__init__(app)
        app.config = {"chatgpt_enabled": enabled, "chatgpt_model": "gpt-6-luna", "bidi_isolate": False}
        app.transcriber, app.ui, app.tray, app.text_editor = (Mock() for _ in range(4))
        app.transcriber.transcribe.return_value = "raw"
        app.text_editor.edit.return_value = edited
        app.clipwatch = None
        with patch.object(main.corrections, "bias_terms", return_value=""), \
             patch.object(main.corrections, "english_terms", return_value=[]), \
             patch.object(main.corrections, "apply", return_value="local") as correction, \
             patch.object(main.history, "add") as saved, \
             patch.object(main, "paste_text") as paste, \
             patch.object(main, "same_destination", return_value=destination):
            app._worker([0], 123)
        return app, saved, paste

    def test_pipeline_edits_corrected_text_once_stores_source_and_pastes_once(self):
        app, saved, paste = self.run_pipeline(EditResult("clean", "edited", 45, "gpt-6-luna"))
        app.text_editor.edit.assert_called_once_with("local", "gpt-6-luna")
        saved.assert_called_once_with("clean", original_text="local", edit_status="edited", edit_ms=45, edit_model="gpt-6-luna")
        paste.assert_called_once_with("clean", True, .5, destination_check=ANY)

    def test_fallback_pastes_local_once_and_has_original(self):
        app, saved, paste = self.run_pipeline(EditResult("local", "timeout", 2000, "gpt-6-luna"))
        self.assertEqual(paste.call_count, 1)
        self.assertEqual(paste.call_args.args[0], "local")
        self.assertEqual(saved.call_args.kwargs["original_text"], "local")

    def test_changed_focus_saves_and_notifies_without_paste(self):
        app, saved, paste = self.run_pipeline(EditResult("clean", "edited"), destination=False)
        saved.assert_called_once()
        paste.assert_not_called()
        app.tray.notify.assert_called_once()

    def test_disabled_does_not_call_editor_and_history_remains_compatible(self):
        app, saved, paste = self.run_pipeline(EditResult("cloud", "edited"), enabled=False)
        app.text_editor.edit.assert_not_called()
        saved.assert_called_once_with("local")
        paste.assert_called_once()

    def test_history_old_entries_and_new_metadata_survive_update_delete_undo(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(history, "HISTORY_PATH", Path(folder) / "history.json"):
            history.HISTORY_PATH.write_text('[{"text":"old","time":"2020-01-01"}]')
            history.add("clean", original_text="local", edit_status="edited", edit_ms=10)
            records = history.load()
            self.assertEqual(records[1]["text"], "old")
            key = records[0]["id"]
            history.update(key, "changed")
            entry, index = history.delete(key)
            history.restore(entry, index)
            self.assertEqual(history.load()[0]["original_text"], "local")
