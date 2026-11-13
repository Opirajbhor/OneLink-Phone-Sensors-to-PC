"""Web UI host: a pywebview window over the existing BTBridge machinery.

The Python side owns the state; JavaScript polls ``Bridge.state()`` every
120 ms and calls small command methods. No callbacks cross into the page, so a
slow phone or a dead socket can never wedge the renderer.

Camera frames are JPEG bytes straight off the socket — the browser decodes
them, so Pillow is not needed here.
"""

from __future__ import annotations

import base64
import os
import threading
import time
import traceback
from collections import deque

from . import __version__, audio_out, audio_win, discovery
from .client import PhoneClient
from .protocol import DEFAULT_PORT

WEBUI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")
INDEX = os.path.join(WEBUI, "index.html")

CAM_SIZES = ("1280x720", "640x480", "960x540", "320x240")


class Bridge:
    """Everything the page can call. Must stay cheap and thread-safe."""

    def __init__(self, initial_host: str | None = None):
        self._lock = threading.RLock()
        self._ui_version = __version__

        self.devices: dict[str, discovery.Device] = {}
        self.sel: str | None = None
        self.conn_state = "idle"          # idle | connecting | connected | error
        self.conn_name = ""
        self.conn_detail = ""
        self.mic_enabled = True
        self.cam_enabled = True
        self.spk_enabled = False
        self.size = "640x480"
        self.fps = 10.0
        self.facing = "back"
        self.sender: audio_out.LoopbackSender | None = None

        self.level = 0.0
        self._frame: bytes | None = None
        self._frame_seq = 0
        self._frame_sent = 0

        self.log: deque[str] = deque(maxlen=300)
        self._client = PhoneClient(on_audio=self._on_audio, on_jpeg=self._on_jpeg,
                                   on_event=self._on_event)
        self.sink = None
        self._sink_tried: list = []
        self.mdns: discovery.MdnsDiscovery | None = None
        self.adb_path = discovery.find_adb()
        self._forwarded_serial: str | None = None
        self._closing = False

        self._log("BTBridge %s — protocol v1" % self._ui_version)
        adb_label = "not found (USB discovery off)"
        if self.adb_path:
            adb_label = "%s (%s)" % (os.path.basename(self.adb_path), "found")
        self._log("adb: %s" % adb_label)
        self._open_sink()
        self._start_mdns()
        threading.Thread(target=self._scan_adb, name="adb-scan", daemon=True).start()
        if initial_host:
            self.add_manual(initial_host)

    # ------------------------------------------------------------- logging
    def _log(self, msg: str):
        with self._lock:
            self.log.append(time.strftime("%H:%M:%S ") + msg)

    # ------------------------------------------------------------ devices
    def _start_mdns(self):
        self.mdns = discovery.MdnsDiscovery(
            on_add=self._dev_found,
            on_remove=self._dev_gone,
            on_log=self._log)
        if not self.mdns.start():
            self._log("mDNS unavailable: %s — use adb or a manual address"
                      % (self.mdns.error or "?"))

    def _dev_found(self, dev: discovery.Device):
        with self._lock:
            self.devices[dev.key] = dev
            if self.sel is None:
                self.sel = dev.key
            self._log("found %s (%s)" % (dev.name, dev.detail or dev.source))

    def _dev_gone(self, key: str):
        with self._lock:
            if key in self.devices:
                self._log("gone: %s" % self.devices[key].name)
                self.devices.pop(key, None)
            if self.sel == key:
                self.sel = next(iter(self.devices), None)

    def _scan_adb(self):
        try:
            devs, msg = discovery.adb_devices(self.adb_path)
        except Exception:  # noqa: BLE001
            self._log("adb scan failed: " + traceback.format_exc(limit=1))
            return
        with self._lock:
            for dev in devs:
                self.devices[dev.key] = dev
            self._log(msg)
            if self.sel is None and devs:
                self.sel = devs[0].key

    def refresh(self) -> bool:
        self._log("rescanning (adb; mDNS is continuous)")
        threading.Thread(target=self._scan_adb, name="adb-scan", daemon=True).start()
        return True

    def add_manual(self, text: str) -> dict:
        host, port = self._parse_hostport(str(text))
        if not host:
            return {"ok": False, "error": "bad address"}
        dev = discovery.Device(key="manual:%s:%d" % (host, port),
                               name="%s:%d" % (host, port), source="manual",
                               host=host, port=port, detail="manual")
        with self._lock:
            self.devices[dev.key] = dev
            self.sel = dev.key
        self._log("manual entry: %s:%d" % (host, port))
        return {"ok": True, "key": dev.key}

    def select(self, key: str) -> bool:
        with self._lock:
            if key in self.devices:
                self.sel = key
                return True
        return False

    @staticmethod
    def _parse_hostport(text: str) -> tuple[str, int]:
        text = (text or "").strip()
        if not text:
            return "", DEFAULT_PORT
        if text.startswith("["):
            host, _, rest = text[1:].partition("]")
            port = rest.lstrip(":")
            return host, int(port) if port.isdigit() else DEFAULT_PORT
        if text.count(":") == 1:
            host, _, port = text.partition(":")
            if port.isdigit():
                return host.strip(), int(port)
        return text, DEFAULT_PORT

    # ------------------------------------------------------------ connect
    def connect(self, key: str = "") -> dict:
        if self._client.is_connected or self.conn_state == "connecting":
            return {"ok": False, "error": "already connected"}
        with self._lock:
            dev = self.devices.get(key or self.sel or "")
        if dev is None:
            return {"ok": False, "error": "pick a phone first"}
        self.conn_state = "connecting"
        self.conn_name = dev.name
        self.conn_detail = dev.detail or dev.source
        self._log("connecting to %s (%s)…" % (dev.name, dev.detail or dev.source))
        threading.Thread(target=self._do_connect, args=(dev,), name="connect",
                         daemon=True).start()
        return {"ok": True}

    def _do_connect(self, dev: discovery.Device):
        try:
            if dev.source == "adb" and getattr(dev, "serial", ""):
                ok, msg = discovery.adb_forward(self.adb_path, dev.serial)
                self._log(msg)
                if ok:
                    self._forwarded_serial = dev.serial
                    dev = discovery.Device(**{**dev.__dict__, "host": "127.0.0.1"})
            self._open_sink()
            if not self._client.connect(dev.host, dev.port, via=dev.source,
                                        serial=getattr(dev, "serial", None)):
                self.conn_state = "error"
                self.conn_detail = self._client.last_error or "connect failed"
                self._log("connect failed: %s" % self.conn_detail)
                return
            self.conn_state = "connected"
            self.conn_name = dev.name
            self.conn_detail = dev.detail or dev.source
            self._log("connected to %s via %s" % (dev.name, dev.source))
            self._push_cfg()
            self._sync_sender()
        except Exception:  # noqa: BLE001
            self.conn_state = "error"
            self.conn_detail = "internal error"
            self._log("connect crashed:\n" + traceback.format_exc())

    def disconnect(self) -> bool:
        self._log("disconnecting")
        self._client.close(notify=False)
        self._close_sink()
        if self.sender is not None:
            self.sender.stop()
            self.sender = None
        self.conn_state = "idle"
        self.conn_name = ""
        self.conn_detail = ""
        serial, self._forwarded_serial = self._forwarded_serial, None
        if serial and self.adb_path:
            threading.Thread(target=discovery.adb_remove_forward,
                             args=(self.adb_path, serial), daemon=True).start()
        return True

    def set_cfg(self, mic: bool, cam: bool, size: str, fps: float,
                facing: str = "back", audio_out_on: bool = False) -> bool:
        with self._lock:
            self.mic_enabled = bool(mic)
            self.cam_enabled = bool(cam)
            self.spk_enabled = bool(audio_out_on)
            if size in CAM_SIZES:
                self.size = size
            self.facing = "front" if str(facing).lower() == "front" else "back"
            try:
                self.fps = max(1.0, min(30.0, float(fps)))
            except (TypeError, ValueError):
                pass
        self._sync_sender()
        self._push_cfg()
        return True

    def _sync_sender(self):
        """Start/stop the PC->phone loopback capture to match the toggle."""
        want = self.spk_enabled and self._client.is_connected
        if want and self.sender is None:
            s = audio_out.LoopbackSender(
                on_frame=lambda pcm: self._client.send_frame(0x04, pcm))
            if s.start():
                self.sender = s
                self._log("speaker: capturing %r → phone" % s.device_name)
            else:
                self._log("speaker: %s" % (s.error or "cannot capture"))
        elif not want and self.sender is not None:
            self.sender.stop()
            self.sender = None
            self._log("speaker: stopped")

    def _push_cfg(self):
        if not self._client.is_connected:
            return
        w, h = self.size.split("x")
        self._client.send_cfg(mic=self.mic_enabled, cam=self.cam_enabled,
                              cam_w=int(w), cam_h=int(h), fps=self.fps,
                              facing=self.facing, audio_out=self.spk_enabled)

    # -------------------------------------------------------------- audio
    def _open_sink(self):
        self._close_sink()
        try:
            sink, tried = audio_win.open_cable_sink()
        except Exception:  # noqa: BLE001
            self._log("audio: device open failed\n" + traceback.format_exc(limit=1))
            return
        self.sink, self._sink_tried = sink, tried
        if sink is not None and sink.ok:
            self._log("audio: %r (#%s, %s ms x %s buffers)"
                      % (sink.name, getattr(sink, "device_id", "?"),
                         getattr(sink, "buffer_ms", "?"),
                         getattr(sink, "nbuffers", "?")))
        else:
            self._log("audio: %s — install VB-Audio Virtual Cable"
                      % (getattr(sink, "error", "no device")))

    def _close_sink(self):
        sink, self.sink = self.sink, None
        if sink is not None:
            try:
                sink.close()
            except Exception:  # noqa: BLE001
                pass

    def _on_audio(self, payload: bytes):
        sink = self.sink
        if sink is not None and self.mic_enabled:
            try:
                sink.write(payload)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- camera
    def _on_jpeg(self, jpeg: bytes):
        with self._lock:
            self._frame = jpeg
            self._frame_seq += 1

    def _on_event(self, kind: str, payload=None):
        if kind == "hello":
            self._log("hello: %s" % (payload or {}))
            self._push_cfg()
        elif kind == "stat":
            pass
        elif kind == "error":
            self._log("phone error: %s" % payload)
        elif kind == "closed":
            self.conn_state = "idle"
            self._close_sink()
            if self.sender is not None:
                self.sender.stop()
                self.sender = None
            self._log("closed: %s" % payload)

    # -------------------------------------------------------------- state
    def state(self) -> dict:
        sink = self.sink
        peak = 0.0
        if sink is not None and self.mic_enabled:
            try:
                peak = sink.read_peak()
            except Exception:  # noqa: BLE001
                peak = 0.0
        self.level = max(peak, self.level * 0.75)
        if self.level < 0.0015:
            self.level = 0.0

        frame = None
        with self._lock:
            if self._frame_seq != self._frame_sent and self._frame:
                frame = "data:image/jpeg;base64," + base64.b64encode(self._frame).decode("ascii")
                self._frame_sent = self._frame_seq
            frame_seq = self._frame_seq
            devices = [{"key": d.key, "name": d.name, "source": d.source,
                        "addr": "%s:%d" % (d.host, d.port),
                        "detail": d.detail or ""} for d in self.devices.values()]
            log = list(self.log)

        c = self._client
        duration = max(0.001, time.time() - (c.connected_at or time.time()))
        running = c.is_connected and (self.mic_enabled or self.cam_enabled)
        return {
            "version": self._ui_version,
            "conn": {"state": self.conn_state, "name": self.conn_name,
                     "detail": self.conn_detail},
            "devices": devices,
            "sel": self.sel,
            "mic": {"enabled": self.mic_enabled, "level": self.level,
                    "db": (20 * __import__("math").log10(self.level)) if self.level > 0.0005 else None,
                    "sink": getattr(sink, "name", "") if sink is not None else "",
                    "sink_ok": bool(getattr(sink, "ok", False))},
            "cam": {"enabled": self.cam_enabled, "seq": frame_seq, "frame": frame,
                    "size": self.size, "fps": self.fps, "facing": self.facing},
            "spk": {"enabled": self.spk_enabled,
                    "level": (self.sender.level if self.sender is not None else 0.0),
                    "ok": bool(self.sender is not None and self.sender.ok),
                    "device": (self.sender.device_name if self.sender is not None else ""),
                    "error": (self.sender.error if self.sender is not None else None)},
            "stats": {"running": running,
                      "rx_kbs": (c.audio_bytes + c.jpeg_bytes) / 1024.0 / duration,
                      "vfps": c.jpeg_frames / duration if c.jpeg_frames else 0.0,
                      "afps": c.audio_frames / duration if c.audio_frames else 0.0},
            "log": log,
        }

    # ------------------------------------------------------------ teardown
    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        try:
            if self._client.is_connected:
                self._client.send_stop()
        except Exception:  # noqa: BLE001
            pass
        self._client.close(notify=False)
        self._close_sink()
        if self.sender is not None:
            self.sender.stop()
            self.sender = None
        if self.mdns is not None:
            try:
                self.mdns.stop()
            except Exception:  # noqa: BLE001
                pass
        if self._forwarded_serial and self.adb_path:
            try:
                discovery.adb_remove_forward(self.adb_path, self._forwarded_serial)
            except Exception:  # noqa: BLE001
                pass


def run_gui(initial_host: str | None = None) -> int:
    """Open the pywebview window. Falls back with a clear message if missing."""
    try:
        import webview
    except Exception as exc:  # noqa: BLE001
        print("pywebview is not available (%s)." % exc)
        print("Install it with:  python -m pip install pywebview")
        print("Or run the classic Tk UI:  python -m btbridge --tk")
        return 2

    bridge = Bridge(initial_host)
    window = webview.create_window(
        "BTBridge", INDEX, js_api=bridge, width=1140, height=772,
        min_size=(980, 680), background_color="#f4f6fa",
        text_select=True,
    )
    try:
        webview.start(debug=bool(os.environ.get("BTBRIDGE_DEBUG")))
    finally:
        bridge.shutdown()
        del window
    return 0
