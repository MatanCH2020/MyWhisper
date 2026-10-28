"""Explicit, previewed support submissions. Never reads user files or raw logs."""
import json
import math
import platform
import re
import ssl
import urllib.error
import urllib.request
import uuid

from version import __version__

# Public write-only endpoint, supplied by the deployed Studio project.
# No admin, sync, service-role or account credential belongs in this module.
REPORT_URL = "https://uxfqowzuvfwqituknxji.supabase.co/functions/v1/submit-report"
CATEGORIES = {
    "transcription": "תמלול", "microphone": "מיקרופון", "paste": "הדבקה",
    "clipboard": "העתקות", "interface": "ממשק", "update": "עדכון", "other": "אחר",
}
MODELS = {
    "ivrit-ai/whisper-large-v3-ct2", "ivrit-ai/whisper-large-v3-turbo-ct2",
    "mlx-community/ivrit-ai-whisper-large-v3-mlx",
}
NUMBERS = {"capture_seconds": 86400, "speech_seconds": 86400,
           "decode_seconds": 86400, "rms": 1, "peak": 1}


class ReportError(Exception):
    """A user-safe message; never include network exceptions or request bodies."""


def clean_diagnostics(values):
    """Construct an allowlisted object instead of attempting to redact a log."""
    if not isinstance(values, dict):
        values = {}
    result = {}
    for key in ("app_version", "os_release", "python_version", "qt_version"):
        value = values.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._+-]{1,60}", value):
            result[key] = value
    choices = {"os": {"Windows", "Darwin"}, "architecture": {"x86_64", "arm64", "AMD64"},
               "backend": {"faster-whisper", "mlx"},
               "device": {"cuda", "cpu", "metal", "auto"}, "model": MODELS}
    for key, allowed in choices.items():
        value = values.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    for key, upper in NUMBERS.items():
        value = values.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= upper:
            result[key] = round(value, 6)
    if type(values.get("silent")) is bool:
        result["silent"] = values["silent"]
    return result


def diagnostics(config, transcriber):
    """Snapshot only existing numerical diagnostics. Does not load a model."""
    from PySide6 import __version__ as qt_version
    last = getattr(transcriber, "last_diagnostics", {})
    if not isinstance(last, dict):
        last = {}
    return clean_diagnostics({
        "app_version": __version__, "os": platform.system(), "os_release": platform.release(),
        "architecture": platform.machine(), "python_version": platform.python_version(),
        "qt_version": qt_version, "backend": config.get("backend"),
        "device": getattr(transcriber, "device", None) or config.get("device"),
        "model": config.get("model"), "capture_seconds": last.get("duration"),
        "speech_seconds": last.get("speech_seconds"), "decode_seconds": last.get("elapsed"),
        "rms": last.get("rms"), "peak": last.get("peak"), "silent": last.get("silent"),
    })


def make_report(category, description, contact, technical, request_id=None):
    if not isinstance(description, str) or not isinstance(contact, str):
        raise ReportError("תיאור התקלה ופרטי הקשר חייבים להיות טקסט.")
    description, contact = description.strip(), contact.strip()
    if category not in CATEGORIES or not 10 <= len(description) <= 6000 or len(contact) > 254:
        raise ReportError("יש לתאר את התקלה ב־10–6,000 תווים; פרטי קשר עד 254 תווים.")
    technical = clean_diagnostics(technical)
    if not {"app_version", "os"} <= technical.keys():
        raise ReportError("לא ניתן להכין את פרטי הגרסה ומערכת ההפעלה לדיווח.")
    request_id = request_id or str(uuid.uuid4())
    try:
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ReportError("מזהה הדיווח אינו תקין.") from None
    result = {"schema_version": 1, "request_id": request_id, "category": category,
              "description": description, "contact": contact, "diagnostics": technical}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 32768:
        raise ReportError("הדיווח גדול מדי. יש לקצר את התיאור.")
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def submit_report(payload, *, endpoint=None, opener=None):
    """One bounded POST on the caller's worker. A timeout is never a receipt."""
    endpoint = REPORT_URL if endpoint is None else endpoint
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        raise ReportError("שירות הדיווח עדיין אינו זמין. הטופס נשמר פתוח; אפשר לנסות בהמשך.")
    # Rebuild, dropping nothing silently: callers may only submit the reviewed contract.
    if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "request_id", "category", "description", "contact", "diagnostics"}:
        raise ReportError("מבנה הדיווח אינו תקין.")
    checked = make_report(payload["category"], payload["description"], payload["contact"],
                          payload["diagnostics"], payload["request_id"])
    if checked != payload or type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ReportError("מבנה הדיווח אינו תקין.")
    data = json.dumps(checked, ensure_ascii=False, allow_nan=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
               "User-Agent": "MyWhisper-Support/1"}
    request = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
    try:
        if opener is None:
            import certifi
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirect(),
                urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
        with opener.open(request, timeout=10) as response:
            if response.status not in (200, 201):
                raise ReportError("לא התקבל אישור שמירה. אפשר לנסות שוב עם אותו דיווח.")
            raw = response.read(16385)
            if len(raw) > 16384:
                raise ValueError()
            receipt = json.loads(raw)
            reference = receipt.get("report_id") if isinstance(receipt, dict) else None
            if (not isinstance(receipt, dict) or receipt.get("ok") is not True
                    or not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,80}", reference)):
                raise ValueError()
            return reference
    except ReportError:
        raise
    except urllib.error.HTTPError as error:
        message = {429: "נשלחו דיווחים רבים בזמן קצר. יש להמתין ולנסות שוב.",
                   409: "הדיווח השתנה מאז ניסיון השליחה. יש לפתוח דיווח חדש.",
                   413: "הדיווח גדול מדי. יש לקצר את התיאור."}.get(error.code)
        raise ReportError(message or "השירות לא אישר שמירה. אפשר לנסות שוב; הטופס נשאר פתוח.") from None
    except Exception:
        raise ReportError("לא התקבל אישור שמירה מהשרת. בדוק חיבור ונסה שוב; הניסיון החוזר לא ייצור כפילות.") from None
