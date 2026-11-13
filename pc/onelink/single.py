"""One OneLink per machine.

A named kernel mutex is the gate: the second process sees it already exists,
brings the first window to the front (or exits quietly when the first one is
running tray-only) and returns before any socket, tray icon or watcher starts.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

MUTEX = "Global\\OneLink.SingleInstance"
ERROR_ALREADY_EXISTS = 183
WINDOW_TITLE = "OneLink"

_handle = None


def _focus_existing() -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True


def acquire() -> bool:
    """True when this process owns OneLink; False when another copy does."""
    global _handle
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, MUTEX)
        if not handle:
            return True                  # cannot tell: do not block the app
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            _focus_existing()
            return False
        _handle = handle                 # keep it for the process lifetime
        return True
    except Exception:                    # noqa: BLE001
        return True
