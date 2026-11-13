"""Windows clipboard: watch, apply, remember — without feedback loops.

Loop safety, in order of dependence:

1. **Own-write suppression.** Anything we put on the clipboard is recorded by
   hash; when the watcher later sees that exact content it stops, so a remote
   clip never bounces back.
2. **Content hashing.** Every event is compared by SHA-1 of the text, not by
   object identity — the clipboard has no identity.
3. **Echo ids.** Each outbound clip carries a uuid; the phone echoes it back on
   its own read, and we drop any clip whose id we have already seen.
4. **Debounce.** Rapid copies (a browser can fire 3-4 in a second) are coalesced
   so we send one frame per burst.
5. **One-way at a time.** While applying a remote clip the watcher is paused,
   because Windows also bumps the sequence number for our own write.

History is kept locally (newest first, de-duplicated) and persisted to
``%APPDATA%\\OneLink\\clipboard.json`` so it survives a restart.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
import time
import uuid
from collections import deque
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
HISTORY_MAX = 50
POLL_MS = 350
DEBOUNCE_S = 0.25

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def read_text() -> str | None:
    """Current clipboard text, or None (empty / non-text / busy)."""
    for _ in range(6):
        if user32.OpenClipboard(None):
            try:
                if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    return None
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return None
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return None
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)          # another app holds it; retry briefly
    return None


def write_text(text: str) -> bool:
    """Replace the clipboard with `text`. Returns False if it could not open."""
    if user32.OpenClipboard(None) == 0:
        return False
    try:
        user32.EmptyClipboard()
        data = (text + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


class ClipboardWatcher:
    """Polls the clipboard sequence number; reports only real, non-echo changes."""

    def __init__(self, on_copy, app_dir: str | None = None):
        self.on_copy = on_copy                    # callable(text, hash)
        self.history: deque[dict] = deque(maxlen=HISTORY_MAX)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._suppress: set[str] = set()          # hashes we wrote ourselves
        self._seen_ids: deque[str] = deque(maxlen=200)
        self._last_seq = 0
        self._pending: tuple[str, float] | None = None
        self._lock = threading.Lock()
        self.enabled = True
        self.app_dir = app_dir or os.path.join(
            os.environ.get("APPDATA") or os.path.expanduser("~"), "OneLink")
        self._load_history()

    # --------------------------------------------------------------- history
    def _path(self) -> str:
        return os.path.join(self.app_dir, "clipboard.json")

    def _load_history(self):
        try:
            with open(self._path(), "r", encoding="utf-8") as fh:
                items = json.load(fh)
            for it in items[-HISTORY_MAX:]:
                if isinstance(it, dict) and it.get("text"):
                    self.history.append(it)
        except (OSError, ValueError):
            pass

    def _save_history(self):
        try:
            os.makedirs(self.app_dir, exist_ok=True)
            tmp = self._path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(list(self.history), fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path())
        except OSError:
            pass

    def add_history(self, text: str, origin: str) -> dict:
        h = text_hash(text)
        with self._lock:
            for item in list(self.history):
                if item.get("hash") == h:
                    self.history.remove(item)
            entry = {"text": text[:4000], "hash": h, "origin": origin,
                     "ts": int(time.time() * 1000)}
            self.history.appendleft(entry)
            self._save_history()
        return entry

    def items(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return list(self.history)[:limit]

    # ------------------------------------------------------------------ apply
    def apply_remote(self, text: str, clip_id: str | None = None) -> bool:
        """Put a phone clip on the PC clipboard without echoing it back."""
        h = text_hash(text)
        self._suppress.add(h)
        if len(self._suppress) > 32:
            self._suppress = set(list(self._suppress)[-16:])
        if clip_id:
            self._seen_ids.append(clip_id)
        ok = write_text(text)
        self._last_seq = user32.GetClipboardSequenceNumber()
        self.add_history(text, "phone")
        return ok

    def seen(self, clip_id: str) -> bool:
        return clip_id in self._seen_ids

    # ------------------------------------------------------------------- loop
    def start(self):
        if self._thread is not None:
            return
        self._last_seq = user32.GetClipboardSequenceNumber()
        current = read_text()
        if current:
            self.add_history(current, "pc")
            self._suppress.add(text_hash(current))
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="clipboard", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=1.0)

    def _run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(POLL_MS / 1000.0)

    def _tick(self):
        seq = user32.GetClipboardSequenceNumber()
        if seq == self._last_seq:
            self._flush_pending()
            return
        self._last_seq = seq
        text = read_text()
        if not text:
            return
        h = text_hash(text)
        if h in self._suppress:          # our own write coming back
            self._suppress.discard(h)
            return
        if not self.enabled:
            self.add_history(text, "pc")
            return
        self._pending = (text, time.time())

    def _flush_pending(self):
        if self._pending is None:
            return
        text, when = self._pending
        if time.time() - when < DEBOUNCE_S:
            return
        self._pending = None
        h = text_hash(text)
        self._suppress.add(h)
        if len(self._suppress) > 32:
            self._suppress = set(list(self._suppress)[-16:])
        self.add_history(text, "pc")
        try:
            self.on_copy(text, h)
        except Exception:  # noqa: BLE001
            pass


def new_clip_id() -> str:
    return uuid.uuid4().hex[:12]
