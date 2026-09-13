"""Recoverable JSON persistence with explicit results and UI diagnostics."""
import copy
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid

log = logging.getLogger('safe_json')
_lock = threading.RLock()
_blocked = set()
_backed_up = {}
_pending = []
_handler = None


def set_error_handler(handler):
    """Handlers must be thread-safe; deliver startup diagnostics too."""
    global _handler
    with _lock:
        _handler = handler
        pending = list(_pending)
        _pending.clear()
    for message in pending:
        handler(message)


def report_error(path, detail='לא ניתן לשמור את השינוי'):
    message = f'{detail}: {Path(path).name}'
    log.warning('%s', message)
    with _lock:
        handler = _handler
        if handler is None:
            if message not in _pending:
                _pending.append(message)
            return
    handler(message)


def read_json(path, default, valid=lambda data: True):
    """Preserve malformed data before recovery; unreadable files block writes."""
    path = Path(path).absolute()
    with _lock:
        try:
            with path.open(encoding='utf-8-sig') as f:
                data = json.load(f)
            if not valid(data):
                raise ValueError('Invalid data structure')
            _blocked.discard(path)
            return data
        except FileNotFoundError:
            _blocked.discard(path)
        except (ValueError, UnicodeError):
            try:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                if _backed_up.get(path) != signature:
                    backup = path.with_name(path.name + f'.corrupt-{uuid.uuid4().hex}.bak')
                    shutil.copy2(path, backup)
                    _backed_up[path] = signature
                    report_error(path, 'נמצא קובץ פגום; עותק גיבוי נשמר לצדו')
                _blocked.discard(path)
            except OSError:
                _blocked.add(path)
                report_error(path, 'לא ניתן לגבות קובץ פגום; השמירה נחסמה להגנת הנתונים')
        except OSError:
            _blocked.add(path)
            report_error(path, 'לא ניתן לקרוא את הקובץ; השמירה נחסמה להגנת הנתונים')
        return copy.deepcopy(default)


def atomic_save(path, data, ensure_ascii=False, indent=2) -> bool:
    """True only after a flushed temporary file replaces the target."""
    target = Path(path).absolute()
    tmp = None
    with _lock:
        if target in _blocked:
            report_error(target)
            return False
        try:
            fd, tmp = tempfile.mkstemp(suffix='.tmp', dir=target.parent)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
            return True
        except (OSError, ValueError, TypeError):
            report_error(target)
            return False
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
                except OSError:
                    log.warning('Could not remove temporary file for %s', target.name)
