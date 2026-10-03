"""Launch an installed browser executable directly, bypassing embedded URL handlers.

Do not inspect browser profiles, cookies, passwords or authentication storage.
The browser itself chooses its usual profile/session. URLs are never logged.
"""
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

_BROWSERS = {"chrome": ("Google Chrome", "chrome.exe"), "edge": ("Microsoft Edge", "msedge.exe"),
             "firefox": ("Mozilla Firefox", "firefox.exe"), "brave": ("Brave", "brave.exe"),
             "opera": ("Opera", "opera.exe"), "vivaldi": ("Vivaldi", "vivaldi.exe")}


def _registry_value(root, key, name=""):
    import winreg
    try:
        with winreg.OpenKey(root, key) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except OSError:
        return ""


def installed_browsers():
    if os.name != "nt":
        return {}
    import winreg
    found = {}
    for slug, (label, executable) in _BROWSERS.items():
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            value = _registry_value(root, "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + executable)
            path = Path(os.path.expandvars(value.strip('"'))) if isinstance(value, str) and value else None
            if path and path.is_file() and path.name.lower() == executable:
                found[slug] = {"name": label, "path": str(path)}
                break
    # Some installations have no App Paths registration. Only fixed vendor
    # paths are considered; never accept an arbitrary executable from config.
    relatives = {"chrome": "Google/Chrome/Application/chrome.exe",
                 "edge": "Microsoft/Edge/Application/msedge.exe",
                 "firefox": "Mozilla Firefox/firefox.exe",
                 "brave": "BraveSoftware/Brave-Browser/Application/brave.exe",
                 "vivaldi": "Vivaldi/Application/vivaldi.exe"}
    for slug, relative in relatives.items():
        if slug in found:
            continue
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            path = Path(base) / relative if base else None
            if path and path.is_file():
                found[slug] = {"name": _BROWSERS[slug][0], "path": str(path)}
                break
    return found


def _default_browser(browsers):
    import winreg
    prog_id = _registry_value(winreg.HKEY_CURRENT_USER,
        "Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\https\\UserChoice", "ProgId")
    if not isinstance(prog_id, str) or not prog_id:
        return ""
    command = _registry_value(winreg.HKEY_CLASSES_ROOT, prog_id + "\\shell\\open\\command")
    if not isinstance(command, str):
        return ""
    match = re.match(r'^\s*"?(.+?\.exe)"?(?:\s|$)', os.path.expandvars(command), re.IGNORECASE)
    if match:
        executable = Path(match.group(1)).name.lower()
        return next((slug for slug in browsers if _BROWSERS[slug][1] == executable), "")
    return ""


def browser_choices():
    return [{"slug": "system", "name": "דפדפן ברירת המחדל — חיצוני"}] + [
        {"slug": slug, "name": value["name"]} for slug, value in installed_browsers().items()]


def open_external_browser(url, preference="system"):
    parsed = urlsplit(url)
    if not (parsed.scheme == "https" and
            ((parsed.netloc == "auth.openai.com" and parsed.path == "/api/accounts/authorize")
             or (parsed.netloc == "chatgpt.com" and parsed.path == "/settings/usage"))):
        return False
    browsers = installed_browsers()
    if preference == "system":
        preference = _default_browser(browsers) if os.name == "nt" else ""
        if not preference:
            preference = next(iter(browsers), "")
    selected = browsers.get(preference)
    if selected is None:
        return False
    try:
        # Argument array, no shell and no generic URL opener/BROWSER env var.
        subprocess.Popen([selected["path"], url], shell=False,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except OSError:
        return False
