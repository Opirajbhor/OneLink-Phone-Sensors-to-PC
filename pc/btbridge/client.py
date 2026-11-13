"""BTBridge v1 client: one socket to the phone, reader thread, frame routing.

All network work happens on a background thread; the UI only ever reads
attributes or receives callbacks, so a stalled phone can never freeze Tk.

Callbacks fire on the reader thread — they must be cheap and thread-safe.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional

from . import protocol as P

STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTING = "connecting"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"


class PhoneClient:
    """A single connection to a phone.

    Parameters
    ----------
    on_audio : callable(bytes) -> None
        PCM16 mono 48 kHz payload of every 0x01 frame.
    on_jpeg : callable(bytes) -> None
        Raw JPEG payload of every 0x02 frame.
    on_event : callable(str, object) -> None
        Lifecycle events: ``hello``, ``stat``, ``error``, ``closed``, ``pong``.
    idle_timeout : float
        Seconds without any frame before the link is declared dead. The phone
        sends ``stat`` every 2 s, so 15 s is generous but still bounds a hang.
    """

    def __init__(self, on_audio: Optional[Callable[[bytes], None]] = None,
                 on_jpeg: Optional[Callable[[bytes], None]] = None,
                 on_event: Optional[Callable[[str, object], None]] = None,
                 idle_timeout: float = 15.0,
                 hello_timeout: float = 6.0,
                 read_timeout: float = 0.5):
        self.on_audio = on_audio
        self.on_jpeg = on_jpeg
        self.on_event = on_event
        self.idle_timeout = idle_timeout
        self.hello_timeout = hello_timeout
        self.read_timeout = read_timeout

        self.status = STATUS_DISCONNECTED
        self.host: Optional[str] = None
        self.port: Optional[int] = None
        self.via: str = ""
        self.serial: Optional[str] = None
        self.last_error: Optional[str] = None
        self.hello: dict = {}
        self.stat: dict = {}
        self.connected_at: Optional[float] = None
        self.last_rx: float = 0.0
        self.last_audio_at: float = 0.0
        self.last_jpeg_at: float = 0.0

        # counters (read by the UI for the status line / selftest asserts)
        self.audio_frames = 0
        self.audio_bytes = 0
        self.jpeg_frames = 0
        self.jpeg_bytes = 0
        self.json_frames = 0
        self.unknown_frames = 0
        self.other_frames = 0
        self.stereo_frames = 0

        self._sock: Optional[socket.socket] = None
        self._reader: Optional[threading.Thread] = None
        self._closed = threading.Event()
        self._send_lock = threading.Lock()
        self._cfg = {"mic": False, "cam": False, "camW": 640, "camH": 480, "fps": 10,
                     "facing": "back", "audioOut": False}

    # -- state --------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self.status == STATUS_CONNECTED and not self._closed.is_set()

    def name(self) -> str:
        return str(self.hello.get("name") or "phone")

    def _emit(self, kind: str, payload: object = None) -> None:
        cb = self.on_event
        if cb is None:
            return
        try:
            cb(kind, payload)
        except Exception:  # noqa: BLE001 - a bad callback must not kill the reader
            pass

    # -- connect / close ----------------------------------------------------
    def connect(self, host: str, port: int = P.DEFAULT_PORT, timeout: float = 6.0,
                via: str = "manual", serial: Optional[str] = None) -> bool:
        """Dial the phone. Blocking; call from a worker thread in the UI."""
        self.close(notify=False)
        self.status = STATUS_CONNECTING
        self.host, self.port, self.via, self.serial = host, port, via, serial
        self.last_error = None
        self.hello = {}
        self.stat = {}
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            self.status = STATUS_ERROR
            self.last_error = "cannot reach %s:%d — %s" % (host, port, exc)
            self._emit("error", self.last_error)
            return False
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.settimeout(self.read_timeout)
        except OSError:
            pass
        self._sock = sock
        self._closed.clear()
        self.status = STATUS_CONNECTED
        self.connected_at = time.time()
        self.last_rx = time.time()
        self._reader = threading.Thread(target=self._read_loop, args=(sock,),
                                        name="btbridge-reader", daemon=True)
        self._reader.start()
        self._emit("connected", {"host": host, "port": port, "via": via})
        return True

    def close(self, notify: bool = True, reason: str = "disconnected") -> None:
        was_open = self._sock is not None
        self._closed.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.5)
        self._reader = None
        if self.status != STATUS_ERROR or not was_open:
            self.status = STATUS_DISCONNECTED
        if was_open and notify:
            self._emit("closed", reason)

    def _fail(self, message: str) -> None:
        """Called from the reader thread when the link dies."""
        self.last_error = message
        self.status = STATUS_ERROR
        self.close(notify=False)
        self._emit("closed", message)

    # -- sending ------------------------------------------------------------
    def _send(self, data: bytes) -> bool:
        sock = self._sock
        if sock is None or self._closed.is_set():
            return False
        try:
            with self._send_lock:
                sock.sendall(data)
            return True
        except OSError as exc:
            self._fail("send failed: %s" % exc)
            return False

    def send_json(self, obj: dict) -> bool:
        return self._send(P.pack_json(obj))

    def send_cfg(self, mic: Optional[bool] = None, cam: Optional[bool] = None,
                 cam_w: Optional[int] = None, cam_h: Optional[int] = None,
                 fps: Optional[float] = None, facing: Optional[str] = None,
                 audio_out: Optional[bool] = None) -> bool:
        """Send a ``cfg`` frame; omitted fields are left out (protocol keeps them)."""
        cfg = dict(self._cfg)
        if mic is not None:
            cfg["mic"] = bool(mic)
        if cam is not None:
            cfg["cam"] = bool(cam)
        if cam_w is not None:
            cfg["camW"] = int(cam_w)
        if cam_h is not None:
            cfg["camH"] = int(cam_h)
        if fps is not None:
            cfg["fps"] = float(fps)
        if facing is not None:
            cfg["facing"] = "front" if str(facing).lower() == "front" else "back"
        if audio_out is not None:
            cfg["audioOut"] = bool(audio_out)
        self._cfg = cfg
        return self.send_json({"t": "cfg", "mic": cfg["mic"], "cam": cfg["cam"],
                               "camW": cfg["camW"], "camH": cfg["camH"],
                               "fps": cfg["fps"], "facing": cfg["facing"],
                               "audioOut": cfg["audioOut"]})

    def send_frame(self, ftype: int, payload: bytes) -> bool:
        """Raw frame, used for audio playout (type 0x04)."""
        return self._send(P.pack_frame(ftype, payload))

    def send_stop(self) -> bool:
        self._cfg.update({"mic": False, "cam": False, "audioOut": False})
        return self.send_json({"t": "stop"})

    def send_ping(self) -> bool:
        return self.send_json({"t": "ping"})

    # -- reader -------------------------------------------------------------
    def _handle_json(self, msg: dict) -> None:
        kind = msg.get("t")
        if kind == "hello":
            self.hello = msg
            self._emit("hello", msg)
        elif kind == "stat":
            self.stat = msg
            self._emit("stat", msg)
        elif kind == "pong":
            self._emit("pong", msg)
        elif kind == "err":
            self._emit("error", msg.get("msg") or "phone reported an error")
        else:
            self._emit("unknown_json", msg)

    def _read_loop(self, sock: socket.socket) -> None:
        reader = P.FrameReader(sock)
        reason = "phone closed the connection"
        try:
            while not self._closed.is_set():
                try:
                    ftype, payload = reader.read_frame()
                except socket.timeout:
                    # Before hello we may be talking to a stale adb forward or a
                    # socket that accepted and died — do not wait the full idle
                    # window for it.
                    limit = (self.hello_timeout if not self.hello
                             else self.idle_timeout)
                    if time.time() - self.last_rx > limit:
                        reason = ("stalled: no hello received within %.0f s"
                                  % limit if not self.hello
                                  else "stalled: nothing received for %.0f s"
                                  % self.idle_timeout)
                        break
                    continue
                except P.ConnectionClosed:
                    reason = "phone closed the connection"
                    break
                except P.ProtocolError as exc:
                    reason = "protocol error: %s" % exc
                    break
                except OSError as exc:
                    reason = "socket error: %s" % exc
                    break

                now = time.time()
                self.last_rx = now
                if ftype == P.T_PCM_MONO or ftype == P.T_PCM_STEREO:
                    self.audio_frames += 1
                    self.audio_bytes += len(payload)
                    self.last_audio_at = now
                    if self.on_audio is not None:
                        try:
                            if ftype == P.T_PCM_STEREO:
                                payload = P.downmix_stereo_to_mono(payload)
                                self.stereo_frames += 1
                            self.on_audio(payload)
                        except Exception:  # noqa: BLE001
                            pass
                elif ftype == P.T_JPEG:
                    self.jpeg_frames += 1
                    self.jpeg_bytes += len(payload)
                    self.last_jpeg_at = now
                    if self.on_jpeg is not None:
                        try:
                            self.on_jpeg(payload)
                        except Exception:  # noqa: BLE001
                            pass
                elif ftype == P.T_JSON:
                    self.json_frames += 1
                    try:
                        msg = P.parse_json(payload)
                    except P.ProtocolError as exc:
                        self._emit("error", str(exc))
                        continue
                    self._handle_json(msg)
                else:
                    # The PC must tolerate frame types it does not know.
                    self.unknown_frames += 1
        finally:
            if not self._closed.is_set():
                self.last_error = reason
                self.status = STATUS_ERROR
                self._closed.set()
                sock2, self._sock = self._sock, None
                if sock2 is not None:
                    try:
                        sock2.close()
                    except OSError:
                        pass
                self._emit("closed", reason)

    # -- presentation helpers ----------------------------------------------
    def summary(self) -> str:
        if self.status == STATUS_CONNECTED:
            bits = []
            if self.hello:
                bits.append("%s · proto %s" % (self.hello.get("name", "?"),
                                               self.hello.get("proto", "?")))
            if self.stat:
                s = self.stat
                bits.append("mic=%s cam=%s fps=%s" % (
                    s.get("mic"), s.get("cam"), s.get("fps")))
            return " · ".join(bits) or "connected"
        if self.status == STATUS_ERROR:
            return "error: %s" % (self.last_error or "?")
        return self.status
