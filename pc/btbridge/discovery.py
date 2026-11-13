"""Phone discovery: mDNS (zeroconf), adb over USB, and manual host:port.

All three feed the same ``Device`` list. Everything here is blocking I/O and
is meant to be driven from a worker thread; results are handed to the UI
through callbacks that only touch thread-safe state.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .protocol import DEFAULT_PORT, SERVICE_TYPE

# adb is shipped by the Android SDK; use the known toolchain copy if present,
# otherwise whatever is on PATH.
ADB_CANDIDATES = (
    os.environ.get("BTBRIDGE_ADB"),
    r"C:/Users/Opi/AppData/Local/Temp/pt/platform-tools/adb.exe",
    r"C:\Users\Opi\AppData\Local\Temp\pt\platform-tools\adb.exe",
    "adb",
)

CREATE_NO_WINDOW = 0x08000000


@dataclass
class Device:
    """One phone as discovered from any source."""

    key: str
    name: str
    source: str            # "mDNS" | "adb" | "manual"
    host: str
    port: int = DEFAULT_PORT
    serial: Optional[str] = None
    detail: str = ""
    seen: float = field(default_factory=time.time)

    def label(self) -> str:
        return "%s  (%s%s)" % (self.name, self.source,
                               ", " + self.detail if self.detail else "")


# --------------------------------------------------------------------------
# adb
# --------------------------------------------------------------------------
def find_adb() -> Optional[str]:
    for cand in ADB_CANDIDATES:
        if not cand:
            continue
        if os.path.sep in cand or cand.lower().endswith(".exe"):
            if os.path.isfile(cand):
                return cand
        else:
            hit = shutil.which(cand)
            if hit:
                return hit
    return None


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Run a console program with no flashing window."""
    kw = {}
    if sys.platform.startswith("win"):
        kw["creationflags"] = CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=timeout, **kw)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %.0fs: %s" % (timeout, " ".join(cmd))


def adb_devices(adb: Optional[str] = None,
                with_model: bool = True) -> tuple[list[Device], str]:
    """``adb devices -l`` plus ``adb shell getprop ro.product.model``.

    Returns (devices, human-readable log line).
    """
    adb = adb or find_adb()
    if not adb:
        return [], "adb: not found (USB path unavailable)"
    rc, out, err = _run([adb, "devices", "-l"], timeout=15)
    if rc != 0:
        return [], "adb devices failed (rc=%d): %s" % (rc, (err or out).strip()[:200])
    serials: list[tuple[str, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 2:
            continue
        serials.append((parts[0], parts[1]))

    usable = [(s, st) for s, st in serials if st == "device"]
    devs: list[Device] = []
    for serial, _state in usable:
        model = ""
        if with_model:
            rc2, out2, _err2 = _run([adb, "-s", serial, "shell",
                                     "getprop ro.product.model"], timeout=8)
            if rc2 == 0:
                model = out2.strip().splitlines()[-1].strip() if out2.strip() else ""
        name = model or serial
        devs.append(Device(key="adb:%s" % serial, name=name, source="adb",
                           host="127.0.0.1", port=DEFAULT_PORT, serial=serial,
                           detail=serial))
    skipped = [s for s, st in serials if st != "device"]
    msg = "adb: %d device(s)%s" % (len(devs),
                                   ", %d not usable (%s)" % (len(skipped), ", ".join(skipped))
                                   if skipped else "")
    return devs, msg


def adb_forward(adb: Optional[str], serial: Optional[str],
                port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """``adb forward tcp:<port> tcp:<port>`` so 127.0.0.1:<port> reaches the phone."""
    adb = adb or find_adb()
    if not adb:
        return False, "adb not found"
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += ["forward", "tcp:%d" % port, "tcp:%d" % port]
    rc, out, err = _run(cmd, timeout=15)
    if rc != 0:
        return False, "adb forward failed: %s" % ((err or out).strip()[:300] or "rc=%d" % rc)
    return True, "adb forward tcp:%d -> tcp:%d%s" % (port, port,
                                                     " (%s)" % serial if serial else "")


def adb_remove_forward(adb: Optional[str], serial: Optional[str],
                       port: int = DEFAULT_PORT) -> None:
    adb = adb or find_adb()
    if not adb:
        return
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += ["forward", "--remove", "tcp:%d" % port]
    _run(cmd, timeout=8)


# --------------------------------------------------------------------------
# mDNS
# --------------------------------------------------------------------------
class MdnsDiscovery:
    """Browse ``_btbridge._tcp.`` with zeroconf.

    ``on_add(Device)`` / ``on_remove(key)`` fire on zeroconf threads.
    """

    def __init__(self, on_add: Callable[[Device], None],
                 on_remove: Callable[[str], None],
                 on_log: Optional[Callable[[str], None]] = None,
                 service_type: str = SERVICE_TYPE):
        self.on_add = on_add
        self.on_remove = on_remove
        self.on_log = on_log or (lambda _m: None)
        self.service_type = service_type
        self.error: Optional[str] = None
        self._zc = None
        self._browser = None
        self._lock = threading.Lock()

    # zeroconf handler hooks -------------------------------------------------
    def _add(self, zc, service_type, name):
        try:
            info = zc.get_service_info(service_type, name, timeout=2500)
        except Exception as exc:  # noqa: BLE001
            self.on_log("mDNS resolve failed for %s: %s" % (name, exc))
            return
        if info is None:
            return
        props = {}
        for k, v in (info.properties or {}).items():
            kk = k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)
            vv = v.decode("utf-8", "replace") if isinstance(v, bytes) else (
                "" if v is None else str(v))
            props[kk.lower()] = vv
        addrs = []
        try:
            addrs = info.parsed_addresses()
        except Exception:  # noqa: BLE001
            pass
        host = ""
        for a in addrs:
            if ":" in a:            # skip IPv6 for the v1 dial path
                continue
            host = a
            break
        if not host and addrs:
            host = addrs[0]
        if not host:
            self.on_log("mDNS %s has no address" % name)
            return
        dev_name = props.get("name") or name.split(".")[0]
        self.on_add(Device(key="mdns:%s" % name, name=dev_name, source="mDNS",
                           host=host, port=int(info.port or DEFAULT_PORT),
                           detail="%s:%s" % (host, info.port)))

    def _remove(self, zc, service_type, name):
        self.on_remove("mdns:%s" % name)

    # lifecycle -------------------------------------------------------------
    def start(self) -> bool:
        try:
            from zeroconf import ServiceBrowser, Zeroconf
        except Exception as exc:  # noqa: BLE001
            self.error = "zeroconf unavailable: %s" % exc
            self.on_log(self.error)
            return False

        class _Listener:
            def __init__(self, outer):
                self.outer = outer

            def add_service(self, zc, service_type, name):
                self.outer._add(zc, service_type, name)

            def update_service(self, zc, service_type, name):
                self.outer._add(zc, service_type, name)

            def remove_service(self, zc, service_type, name):
                self.outer._remove(zc, service_type, name)

        try:
            with self._lock:
                self._zc = Zeroconf()
                self._browser = ServiceBrowser(self._zc, self.service_type,
                                               _Listener(self))
        except Exception as exc:  # noqa: BLE001
            self.error = "mDNS browse failed: %s" % exc
            self.on_log(self.error)
            self._zc = None
            return False
        self.on_log("mDNS browsing %s" % self.service_type)
        return True

    def stop(self) -> None:
        with self._lock:
            zc, self._zc = self._zc, None
            self._browser = None
        if zc is not None:
            try:
                zc.close()
            except Exception:  # noqa: BLE001
                pass
