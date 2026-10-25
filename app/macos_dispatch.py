"""Marshal Cocoa/Accessibility operations to the Qt main thread."""
import threading
from PySide6.QtCore import QObject, Signal, Qt, QThread
from PySide6.QtWidgets import QApplication


class Dispatcher(QObject):
    requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.requested.connect(self._run, Qt.QueuedConnection)

    def _run(self, request):
        if request["cancelled"].is_set():
            request["done"].set()
            return
        try:
            request["result"] = request["fn"]()
        except Exception as exc:
            request["error"] = exc
        finally:
            request["done"].set()


_dispatcher = None


def initialize():
    global _dispatcher
    _dispatcher = Dispatcher()


def on_main(fn):
    app = QApplication.instance()
    if app is None or _dispatcher is None:
        raise RuntimeError("Native dispatcher is not initialized")
    if QThread.currentThread() == app.thread():
        return fn()
    req = {"fn": fn, "done": threading.Event(), "cancelled": threading.Event()}
    _dispatcher.requested.emit(req)
    if not req["done"].wait(5):
        req["cancelled"].set()
        raise TimeoutError("Native operation timed out")
    if "error" in req:
        raise req["error"]
    return req.get("result")
