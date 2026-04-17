"""
Storage helpers - file-level locking + safe JSON read/write.

Used across modules that share JSON files (tickets, conversations, messages)
so that the Flask webhook thread and the Telegram listener thread don't
corrupt each other's writes.
"""

import json
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# One lock per filesystem path, shared across threads in this process.
_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _registry_lock:
        lock = _locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _locks[path] = lock
        return lock


@contextmanager
def file_lock(path: str):
    """Context manager that serialises access to a given file path."""
    lock = _lock_for(path)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def load_json(path: str, default: Any = None):
    """Load JSON with graceful fallback on missing/corrupt files."""
    if default is None:
        default = []
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        logger.exception("Failed to read %s - using default", path)
        return default


def save_json(path: str, data: Any) -> None:
    """Atomically write JSON by writing to a temp file then renaming."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        logger.exception("Failed to write %s", path)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
