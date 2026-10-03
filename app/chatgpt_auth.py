"""Official ChatGPT public-client OAuth. Credentials belong to this Windows user.

Importing/constructing this module performs no network requests. UI callbacks
run sign-in/catalog operations off the GUI thread. Only explicit activation
allows background refresh. Tokens and dictated text are never logged.
"""
import asyncio
import base64
import ctypes
from ctypes import wintypes
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

import jwt
from external_browser import open_external_browser

AUTH = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
REQUIRED_SCOPES = {"resource.invoke", "chatgpt.tokens.use.direct"}
DEFAULT_MODEL = "gpt-6-luna"


class AuthError(Exception):
    """Safe, app-defined code only; never surface raw server/token exceptions."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data, decrypt=False):
    if os.name != "nt":
        raise AuthError("storage")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _Blob(len(data), buffer)
    target = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(_Blob)]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        operation = crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR,
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(_Blob)]
        # CRYPTPROTECT_UI_FORBIDDEN; deliberately no LOCAL_MACHINE flag.
        args = (ctypes.byref(source), "MyWhisper ChatGPT", None, None, None,
                1, ctypes.byref(target))
    operation.restype = wintypes.BOOL
    if not operation(*args):
        raise AuthError("storage")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


class CredentialStore:
    def __init__(self, path=None, protect=dpapi):
        base = os.environ.get("LOCALAPPDATA")
        self.path = Path(path) if path else (
            Path(base) / "MatanDigital" / "MyWhisper" / "chatgpt.dpapi" if base else None)
        self.protect = protect

    def load(self):
        if self.path is None or not self.path.exists():
            return {"accounts": {}, "active": ""}
        try:
            result = json.loads(self.protect(self.path.read_bytes(), decrypt=True))
            if not isinstance(result, dict) or not isinstance(result.get("accounts"), dict):
                raise ValueError()
            if not isinstance(result.get("active", ""), str) or not isinstance(result.get("host", ""), str):
                raise ValueError()
            if not isinstance(result.get("pending_client", ""), str):
                raise ValueError()
            for key, account in result["accounts"].items():
                if not isinstance(key, str) or not isinstance(account, dict):
                    raise ValueError()
                if not all(isinstance(account.get(k, ""), str) for k in
                           ("client_id", "subject", "email", "id_token", "access_token", "refresh_token")):
                    raise ValueError()
                if not isinstance(account.get("scopes", []), list) or not all(
                        isinstance(scope, str) for scope in account.get("scopes", [])):
                    raise ValueError()
                expires = account.get("expires_at", 0)
                if type(expires) not in (int, float) or not math.isfinite(expires):
                    raise ValueError()
                models = account.get("models", [])
                if not isinstance(models, list) or not all(isinstance(m, dict)
                    and isinstance(m.get("slug"), str) and isinstance(m.get("display_name", ""), str) for m in models):
                    raise ValueError()
            return result
        except Exception:
            raise AuthError("storage") from None

    def save(self, data):
        if self.path is None:
            raise AuthError("storage")
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            encrypted = self.protect(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as f:
                f.write(encrypted)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.path)
        except Exception:
            raise AuthError("storage") from None
        finally:
            temporary.unlink(missing_ok=True)


class ChatGPTAuth:
    def __init__(self, http, store=None):
        self.http = http
        self.store = store or CredentialStore()
        self.lock = threading.RLock()
        self.enabled = False
        self.generation = 0
        self._refresh = None
        self._signin_cancel = threading.Event()
        self._signin_lock = threading.Lock()
        self.error = ""
        self.signing_in = False
        self.sign_in_error = ""
        try:
            self.data = self.store.load()
        except AuthError:
            self.data = {"accounts": {}, "active": ""}
            self.error = "storage"

    def _active(self):
        return self.data["accounts"].get(self.data.get("active"), {})

    def status(self):
        with self.lock:
            account = self._active()
            return {"active": self.data.get("active", ""), "enabled": self.enabled,
                    "connected": bool(account.get("refresh_token")),
                    "eligible": REQUIRED_SCOPES <= set(account.get("scopes", [])),
                    "email": account.get("email", ""), "error": self.error,
                    "signing_in": self.signing_in, "sign_in_error": self.sign_in_error,
                    "models": list(account.get("models", [])),
                    "accounts": [{"key": key, "label": (a.get("email") or "ChatGPT")
                                  + " · " + key[-6:]} for key, a in self.data["accounts"].items()]}

    def set_enabled(self, enabled):
        with self.lock:
            self.enabled = enabled is True
            self.generation += 1
            if self._refresh is not None:
                self._refresh.cancel()
                self._refresh = None

    def access(self):
        with self.lock:
            a = self._active()
            if (not self.enabled or not REQUIRED_SCOPES <= set(a.get("scopes", []))
                    or a.get("expires_at", 0) <= time.time() + 2):
                return None
            return a.get("access_token")

    def select(self, key):
        with self.lock:
            if key not in self.data["accounts"]:
                raise AuthError("account")
            self.set_enabled(False)
            self._signin_cancel.set()
            self.data["active"] = key
            self.store.save(self.data)
            self.error = ""
            self.sign_in_error = ""

    async def _json(self, method, url, **kwargs):
        response = await self.http.client.request(method, url, **kwargs)
        if response.status_code != 200:
            try:
                code = response.json().get("error", "")
            except Exception:
                code = ""
            raise AuthError("authorization" if response.status_code in (401, 403)
                            or code in ("invalid_grant", "invalid_client", "invalid_refresh_token",
                                        "token_expired", "refresh_token_expired", "refresh_token_invalidated",
                                        "refresh_token_reused") else "connection")
        return response.json()

    async def _validate_identity(self, token, client_id, nonce):
        # Trusted issuer/JWKS fixed from the official discovery document.
        keys = await self._json("GET", AUTH + "/.well-known/jwks.json")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise ValueError()
            matching = [k for k in keys.get("keys", []) if k.get("kid") == header.get("kid")]
            if len(matching) != 1:
                raise ValueError()
            key = jwt.PyJWK.from_dict(matching[0], algorithm="RS256").key
            identity = jwt.decode(token, key, algorithms=["RS256"], audience=client_id,
                                  issuer=AUTH, options={"require": ["exp", "iss", "aud", "sub", "nonce"]})
            if not secrets.compare_digest(identity["nonce"], nonce) or not identity["sub"]:
                raise ValueError()
            return identity
        except Exception:
            raise AuthError("identity") from None

    @staticmethod
    def _tokens(body):
        try:
            if body.get("token_type", "").lower() != "bearer":
                raise ValueError()
            if not all(isinstance(body.get(k), str) and body[k] for k in
                       ("access_token", "refresh_token", "scope")):
                raise ValueError()
            expires = float(body["expires_in"])
            if not 0 < expires <= 86400:
                raise ValueError()
            return {"access_token": body["access_token"], "refresh_token": body["refresh_token"],
                    "scopes": body["scope"].split(), "expires_at": time.time() + expires,
                    "earliest_refresh_at": body.get("earliest_refresh_at", 0)}
        except (ValueError, TypeError, KeyError):
            raise AuthError("authorization") from None

    def sign_in(self, returning=False, browser=open_external_browser, timeout=180):
        if not self._signin_lock.acquire(blocking=False):
            raise AuthError("busy")
        with self.lock:
            self.signing_in = True
            self.sign_in_error = ""
        try:
            self._sign_in(returning, browser, timeout)
        except AuthError as error:
            with self.lock:
                self.sign_in_error = str(error)
            raise
        except Exception:
            with self.lock:
                self.sign_in_error = "connection"
            raise
        finally:
            with self.lock:
                self.signing_in = False
            self._signin_lock.release()
        return self.status()

    def _sign_in(self, returning, browser, timeout):
        self._signin_cancel.clear()
        with self.lock:
            if self.error == "storage":
                raise AuthError("storage")  # Never overwrite an unreadable credential file.
            if not self.data.get("host"):
                self.data["host"] = "urn:uuid:" + str(uuid.uuid4())
                self.store.save(self.data)
            old = dict(self._active()) if returning else {}
            host = self.data["host"]
            pending_client = self.data.get("pending_client", "") if not returning else ""
        client_id = old.get("client_id") or pending_client or "dynamic_agent_client"
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        answer = {}

        class Callback(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(1)

            def log_message(self, *_):
                pass  # URLs contain authorization codes; never log HTTP requests.

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                valid = (parsed.path == "/auth/callback" and all(len(v) == 1 for v in query.values())
                         and secrets.compare_digest(query.get("state", [""])[0], state))
                if valid:
                    answer.update({k: v[0] for k, v in query.items()})
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(("אפשר לחזור ל-MyWhisper. החיבור אינו מפעיל עריכה בענן."
                                  if valid else "Invalid authorization callback.").encode("utf-8"))

        with HTTPServer(("127.0.0.1", 0), Callback) as server:
            server.timeout = 0.25
            redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
            params = {"client_id": client_id, "ext_agent_host_id": host, "response_type": "code",
                      "redirect_uri": redirect, "scope": SCOPES, "resource": RESOURCE,
                      "state": state, "nonce": nonce, "code_challenge_method": "S256",
                      "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
            if old:
                if old.get("id_token"):
                    params["id_token_hint"] = old["id_token"]
                if old.get("email"):
                    params["login_hint"] = old["email"]
            elif client_id == "dynamic_agent_client":
                params["agent_name_hint"] = "MyWhisper"
            if old and not REQUIRED_SCOPES <= set(old.get("scopes", [])):
                params["prompt"] = "consent"  # Explicit reauthorization after a declined grant.
            if not browser(AUTH + "/api/accounts/authorize?" + urlencode(params)):
                raise AuthError("browser")
            deadline = time.monotonic() + timeout
            while not answer and time.monotonic() < deadline and not self._signin_cancel.is_set():
                server.handle_request()
        if self._signin_cancel.is_set():
            raise AuthError("cancelled")
        if not answer:
            raise AuthError("timeout")
        if answer.get("error"):
            raise AuthError("permission")
        issued = answer.get("client_id", client_id if client_id != "dynamic_agent_client" else "")
        if not issued or issued == "dynamic_agent_client" or (client_id != "dynamic_agent_client" and issued != client_id):
            raise AuthError("identity")
        if not answer.get("code"):
            raise AuthError("authorization")
        if not old:
            with self.lock:
                # Retain the issued registration even if exchange/verification
                # fails. Retrying uses it, rather than creating another client.
                self.data["pending_client"] = issued
                self.store.save(self.data)
        body = self.http.call(self._json("POST", AUTH + "/api/accounts/oauth/token", data={
            "grant_type": "authorization_code", "client_id": issued, "code": answer["code"],
            "code_verifier": verifier, "redirect_uri": redirect, "resource": RESOURCE}))
        identity = self.http.call(self._validate_identity(body.get("id_token", ""), issued, nonce))
        if old and identity["sub"] != old.get("subject"):
            raise AuthError("identity")
        account = {"client_id": issued, "subject": identity["sub"],
                   "email": identity.get("email") if isinstance(identity.get("email"), str) else "",
                   "id_token": body["id_token"],
                   **self._tokens(body)}
        key = hashlib.sha256((issued + "\0" + identity["sub"]).encode()).hexdigest()
        with self.lock:
            if self._signin_cancel.is_set():
                raise AuthError("cancelled")
            self.set_enabled(False)
            self.data["accounts"][key] = account
            self.data["active"] = key
            self.data.pop("pending_client", None)
            self.store.save(self.data)
            self.error = "" if REQUIRED_SCOPES <= set(account["scopes"]) else "permission"
        if not self.error:
            self.catalog()
        return self.status()

    def catalog(self):
        with self.lock:
            key = self.data.get("active")
            account = dict(self._active())
        if not REQUIRED_SCOPES <= set(account.get("scopes", [])):
            raise AuthError("permission")
        if account.get("expires_at", 0) <= time.time():
            raise AuthError("authorization")
        body = self.http.call(self._json("GET", RESOURCE + "/models", headers={
            "Authorization": "Bearer " + account["access_token"]}))
        models = [{"slug": m["slug"], "display_name": m.get("display_name")
                   if isinstance(m.get("display_name"), str) else m["slug"]}
                  for m in body.get("models", []) if m.get("visibility") == "list"
                  and isinstance(m.get("slug"), str)]
        with self.lock:
            if self.data.get("active") == key:
                self._active()["models"] = models
                self.store.save(self.data)
        return models

    def schedule_refresh(self):
        with self.lock:
            a = self._active()
            earliest = a.get("earliest_refresh_at", 0)
            try:
                if isinstance(earliest, str):
                    from datetime import datetime
                    earliest = datetime.fromisoformat(earliest.replace("Z", "+00:00")).timestamp()
                earliest = float(earliest)
            except (TypeError, ValueError):
                earliest = 0
            if (not self.enabled or not a.get("refresh_token") or self.error
                    or a.get("expires_at", 0) > time.time() + 120 or earliest > time.time()
                    or (self._refresh is not None and not self._refresh.done())):
                return
            self._refresh = self.http.submit(self._renew(dict(a), self.data.get("active"), self.generation))

    async def _renew(self, account, key, generation):
        try:
            with self.lock:
                if not self.enabled or generation != self.generation:
                    return
            body = await self._json("POST", AUTH + "/api/accounts/oauth/token", data={
                "grant_type": "refresh_token", "client_id": account["client_id"],
                "refresh_token": account["refresh_token"], "resource": RESOURCE})
            replacement = self._tokens(body)
            with self.lock:
                if self.enabled and generation == self.generation and key == self.data.get("active"):
                    self._active().update(replacement)
                    self.store.save(self.data)
        except asyncio.CancelledError:
            raise
        except AuthError as error:
            with self.lock:
                if generation == self.generation:
                    if str(error) == "authorization":
                        self.error = "authorization"
                        for name in ("access_token", "refresh_token", "id_token", "scopes", "expires_at", "models"):
                            self._active().pop(name, None)
                        self.store.save(self.data)
                    elif str(error) == "storage":
                        self.error = "storage"
        except Exception:
            # A transient connection failure may recover on the next enabled poll.
            pass

    def disconnect(self):
        with self.lock:
            self.set_enabled(False)
            self._signin_cancel.set()
            account = dict(self._active())
            for name in ("access_token", "refresh_token", "id_token", "scopes", "expires_at", "models"):
                self._active().pop(name, None)
            self.store.save(self.data)
            self.error = ""
            self.sign_in_error = ""
        async def revoke():
            metadata = await self._json("GET", AUTH + "/.well-known/openid-configuration")
            endpoint = metadata.get("revocation_endpoint")
            if endpoint != AUTH + "/api/accounts/oauth/revoke":
                raise AuthError("authorization")
            async with asyncio.timeout(6):
                for attempt in range(2):
                    try:
                        response = await self.http.client.post(endpoint, timeout=2, data={
                            "token": account["refresh_token"], "token_type_hint": "refresh_token",
                            "client_id": account["client_id"]})
                        if response.status_code == 200:
                            return True
                        if response.status_code < 500:
                            return False
                    except Exception:
                        pass
                    if attempt == 0:
                        await asyncio.sleep(.4)
                return False
        if not account.get("refresh_token"):
            return True
        try:
            return self.http.call(revoke())
        except Exception:
            return False

    def close(self):
        self.set_enabled(False)
        self._signin_cancel.set()
