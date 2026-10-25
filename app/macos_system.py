"""macOS integration. No dictated text, window titles or clipboard values are logged."""
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import threading

import ApplicationServices as AX
import AppKit
import CoreFoundation as CF
from PySide6.QtCore import QTimer

from macos_dispatch import on_main
from platform_support import BUNDLE_ID, data_dir


def ax_value(element, name):
    error, value = AX.AXUIElementCopyAttributeValue(element, name, None)
    return value if error == 0 else None


def accessibility_allowed():
    return bool(AX.AXIsProcessTrusted())


def request_accessibility():
    AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})


def microphone_status():
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
    return int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))


def request_microphone():
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
    AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda allowed: None)


def open_privacy(pane):
    allowed = {"Microphone", "Accessibility", "ListenEvent", "Pasteboard"}
    if pane not in allowed:
        return False
    return bool(AppKit.NSWorkspace.sharedWorkspace().openURL_(
        AppKit.NSURL.URLWithString_("x-apple.systempreferences:com.apple.preference.security?Privacy_" + pane)))


@dataclass(eq=False)
class Destination:
    pid: int
    window: object
    element: object

    def matches(self, other):
        return (isinstance(other, Destination) and self.pid == other.pid
                and bool(CF.CFEqual(self.window, other.window))
                and bool(CF.CFEqual(self.element, other.element)))


def _foreground():
    if not accessibility_allowed():
        return None
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None or app.processIdentifier() == os.getpid():
        return None
    pid = int(app.processIdentifier())
    element = AX.AXUIElementCreateApplication(pid)
    window = ax_value(element, AX.kAXFocusedWindowAttribute)
    field = ax_value(element, AX.kAXFocusedUIElementAttribute)
    if window is None or field is None:
        return None
    if ax_value(field, AX.kAXSubroleAttribute) == "AXSecureTextField":
        return None
    return Destination(pid, window, field)


def foreground_window():
    try:
        return on_main(_foreground)
    except Exception:
        return None


def same_destination(destination):
    current = foreground_window()
    return bool(isinstance(destination, Destination) and current and destination.matches(current))


class DestinationGuard:
    """Observe every focus transition, including an away-and-back transition."""
    def __init__(self):
        self.window = foreground_window()
        self.changed = threading.Event()
        self._observer = self._workspace_token = self._application = None
        self._callback = None
        self._ready = False

    def start(self):
        try:
            if not self.window:
                raise RuntimeError("No accessible destination")
            self._application = AX.AXUIElementCreateApplication(self.window.pid)
            self._callback = lambda *args: self.changed.set()
            error, self._observer = AX.AXObserverCreate(self.window.pid, self._callback, None)
            if error:
                raise RuntimeError("Cannot observe destination")
            for notification in (AX.kAXFocusedWindowChangedNotification,
                                 AX.kAXFocusedUIElementChangedNotification):
                if AX.AXObserverAddNotification(self._observer, self._application, notification, None):
                    raise RuntimeError("Cannot observe focus changes")
            CF.CFRunLoopAddSource(CF.CFRunLoopGetMain(), AX.AXObserverGetRunLoopSource(self._observer),
                                  CF.kCFRunLoopCommonModes)
            center = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()
            self._workspace_token = center.addObserverForName_object_queue_usingBlock_(
                AppKit.NSWorkspaceDidActivateApplicationNotification, None, None,
                lambda notification: self.changed.set())
            self._ready = same_destination(self.window)
        except Exception:
            self.changed.set()
        return self

    def unchanged(self):
        return self._ready and not self.changed.is_set() and same_destination(self.window)

    def close(self):
        self._ready = False
        if self._observer is not None:
            CF.CFRunLoopRemoveSource(CF.CFRunLoopGetMain(), AX.AXObserverGetRunLoopSource(self._observer),
                                     CF.kCFRunLoopCommonModes)
        if self._workspace_token is not None:
            AppKit.NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self._workspace_token)
        self._observer = self._workspace_token = self._application = self._callback = None


def _fullscreen():
    if not accessibility_allowed():
        return False
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if not app or app.processIdentifier() == os.getpid():
        return False
    window = ax_value(AX.AXUIElementCreateApplication(app.processIdentifier()), AX.kAXFocusedWindowAttribute)
    if window is None:
        return False
    if ax_value(window, "AXFullScreen"):
        return True
    pos, size = ax_value(window, AX.kAXPositionAttribute), ax_value(window, AX.kAXSizeAttribute)
    if pos is None or size is None:
        return False
    okp, point = AX.AXValueGetValue(pos, AX.kAXValueCGPointType, None)
    oks, extent = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
    if not okp or not oks:
        return False
    screens = AppKit.NSScreen.screens()
    top = screens[0].frame().size.height
    for screen in screens:
        frame = screen.frame()
        x, y = frame.origin.x, top - frame.origin.y - frame.size.height
        if (point.x <= x + 2 and point.y <= y + 2
                and point.x + extent.width >= x + frame.size.width - 2
                and point.y + extent.height >= y + frame.size.height - 2):
            return True
    return False


def foreground_is_fullscreen():
    try:
        return bool(on_main(_fullscreen))
    except Exception:
        return False


_instance_file = None


def acquire_single_instance():
    global _instance_file
    import fcntl
    _instance_file = (data_dir() / "instance.lock").open("a")
    try:
        fcntl.flock(_instance_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        _instance_file.close()
        _instance_file = None
        return False


def _launch_agent_path():
    return Path.home() / "Library/LaunchAgents" / (BUNDLE_ID + ".plist")


def login_enabled():
    return _launch_agent_path().is_file()


def set_login_enabled(enabled):
    """Opt-in per-user LaunchAgent. No administrator access or privileged helper."""
    path = _launch_agent_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return True
    if not getattr(sys, "frozen", False):
        return False
    executable = Path(sys.executable).resolve()
    if executable.parts[:2] == ("/", "Volumes") or ".app/Contents/MacOS/" not in str(executable):
        return False
    payload = {"Label": BUNDLE_ID, "ProgramArguments": [str(executable), "--startup"],
               "RunAtLoad": True, "ProcessType": "Interactive"}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(plistlib.dumps(payload))
    temporary.chmod(0o600)
    temporary.replace(path)
    return True
