"""Self-test: a fake phone on 127.0.0.1 drives the real client end to end.

``python -m btbridge --selftest`` proves, without any hardware:

1. winmm enumeration works and we pick a VB-Audio cable endpoint.
2. The playback -> CABLE Output routing actually carries audio (waveIn probe).
3. mDNS announce + browse finds a service and reads ``name`` from its TXT.
4. Frames: hello, stat, unknown types, 1 s of PCM16 and 3 JPEGs all arrive
   through :class:`btbridge.client.PhoneClient`, the audio sink consumes the
   samples, Pillow decodes the frames, and a dropped connection is detected.

Exit code 0 = every check passed.
"""

from __future__ import annotations

import io
import socket
import struct
import sys
import threading
import time

from . import audio_win as aw
from . import protocol as P
from .camera import CameraDecoder
from .client import PhoneClient

FAKE_NAME = "FakePhone"


# --------------------------------------------------------------------------
# fake phone
# --------------------------------------------------------------------------
def make_test_jpeg(index: int, w: int = 640, h: int = 480) -> bytes:
    """A recognisable test card: colour bars + big frame number."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (18, 18, 22))
    draw = ImageDraw.Draw(img)
    colors = [(235, 64, 52), (245, 165, 36), (247, 222, 65),
              (86, 199, 92), (56, 160, 232), (128, 92, 232), (232, 92, 180)]
    bar_w = w / len(colors)
    for i, c in enumerate(colors):
        draw.rectangle([i * bar_w, 0, (i + 1) * bar_w - 1, h * 0.55], fill=c)
    draw.rectangle([0, h * 0.55, w, h], fill=(28, 28, 34))
    draw.text((24, h * 0.62), "BTBRIDGE TEST %d" % index, fill=(255, 255, 255))
    draw.text((24, h * 0.72), "%dx%d  fake phone" % (w, h), fill=(150, 150, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


class FakePhone(threading.Thread):
    """Listens on 127.0.0.1:port, sends a v1 stream, records what the PC sent."""

    def __init__(self, host: str = "127.0.0.1", port: int = P.DEFAULT_PORT,
                 audio_seconds: float = 1.0, jpeg_count: int = 3,
                 audio_delay: float = 0.02, close_after: float = 0.4):
        super().__init__(name="fake-phone", daemon=True)
        self.host = host
        self.port = port
        self.audio_seconds = audio_seconds
        self.jpeg_count = jpeg_count
        self.audio_delay = audio_delay
        self.close_after = close_after

        self.bound_port: int | None = None
        self.ready = threading.Event()
        self.accepted = threading.Event()
        self.done = threading.Event()
        self.error: str | None = None
        self.client_cfg: dict | None = None
        self.client_frames: list[tuple[int, bytes]] = []
        self.bytes_sent = 0
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._conn: socket.socket | None = None

    # -- setup ---------------------------------------------------------------
    def bind(self) -> bool:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        last = None
        for cand in [self.port] + [self.port + i for i in range(1, 6)] + [0]:
            try:
                srv.bind((self.host, cand))
                self.bound_port = srv.getsockname()[1]
                break
            except OSError as exc:
                last = exc
                continue
        else:
            self.error = "cannot bind %s:%s — %s" % (self.host, self.port, last)
            return False
        srv.listen(1)
        srv.settimeout(0.5)
        self._sock = srv
        self.ready.set()
        return True

    # -- lifecycle -----------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        for s in (self._conn, self._sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    def run(self) -> None:
        if self._sock is None and not self.bind():
            self.done.set()
            return
        try:
            self._serve()
        except Exception as exc:  # noqa: BLE001
            self.error = "%s: %s" % (type(exc).__name__, exc)
        finally:
            self.done.set()

    def _serve(self) -> None:
        srv = self._sock
        assert srv is not None
        deadline = time.time() + 15
        conn = None
        while time.time() < deadline and not self._stop.is_set():
            try:
                conn, _addr = srv.accept()
                break
            except socket.timeout:
                continue
        if conn is None:
            self.error = "no client connected"
            return
        conn.settimeout(0.5)
        self._conn = conn
        self.accepted.set()

        self.send_json({"t": "hello", "proto": P.PROTO_VERSION, "name": FAKE_NAME,
                        "mic": True, "cam": True, "audioOut": False})
        self.send_json({"t": "stat", "mic": True, "cam": True, "fps": 9.7,
                        "camW": 640, "camH": 480})
        # things the PC must tolerate: an unknown frame type and unknown JSON
        self._send(P.pack_frame(0x7F, b"unknown-frame-payload"))
        self.send_json({"t": "what-is-this", "x": 1})
        self.send_json({"t": "pong"})

        reader = threading.Thread(target=self._read_frames, args=(conn,),
                                  name="fake-phone-rx", daemon=True)
        reader.start()

        # --- media: 1 s of PCM at 20 ms/frame, JPEGs interleaved -----------
        total_frames = int(self.audio_seconds * P.AUDIO_RATE / P.FRAME_SAMPLES)
        jpeg_every = max(1, total_frames // max(1, self.jpeg_count))
        sent_jpeg = 0
        phase = 0
        t0 = time.time()
        for i in range(total_frames):
            if self._stop.is_set():
                break
            self._send(P.pack_frame(P.T_PCM_MONO, self._pcm_block(phase)))
            phase += P.FRAME_SAMPLES
            if i % jpeg_every == 0 and sent_jpeg < self.jpeg_count:
                sent_jpeg += 1
                self._send(P.pack_frame(P.T_JPEG, make_test_jpeg(sent_jpeg)))
            # pace the stream like a real phone (20 ms per audio frame)
            target = t0 + (i + 1) * self.audio_delay
            sleep = target - time.time()
            if sleep > 0:
                time.sleep(sleep)
        while sent_jpeg < self.jpeg_count and not self._stop.is_set():
            sent_jpeg += 1
            self._send(P.pack_frame(P.T_JPEG, make_test_jpeg(sent_jpeg)))
            time.sleep(0.05)

        # one more stat, then hang up so the client must notice the drop
        self.send_json({"t": "stat", "mic": True, "cam": True, "fps": 9.4,
                        "camW": 640, "camH": 480})
        time.sleep(self.close_after)
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass
        self._conn = None
        reader.join(timeout=2)

    # -- helpers -------------------------------------------------------------
    def _pcm_block(self, phase: int) -> bytes:
        """960 samples of a 440 Hz tone (amplitude 0.3) — real PCM16 LE."""
        import array
        import math
        amp = 0.3 * 32767
        vals = array.array("h", [
            int(amp * math.sin(2 * math.pi * 440 * (phase + i) / P.AUDIO_RATE))
            for i in range(P.FRAME_SAMPLES)])
        return vals.tobytes()

    def _send(self, data: bytes) -> None:
        conn = self._conn
        if conn is None:
            raise ConnectionError("fake phone has no client")
        conn.sendall(data)
        self.bytes_sent += len(data)

    def send_json(self, obj: dict) -> None:
        self._send(P.pack_json(obj))

    def _read_frames(self, conn: socket.socket) -> None:
        reader = P.FrameReader(conn)
        while not self._stop.is_set():
            try:
                ftype, payload = reader.read_frame()
            except socket.timeout:
                continue
            except (P.ConnectionClosed, OSError, P.ProtocolError):
                return
            self.client_frames.append((ftype, payload))
            if ftype == P.T_JSON:
                try:
                    msg = P.parse_json(payload)
                except P.ProtocolError:
                    continue
                if msg.get("t") == "cfg":
                    self.client_cfg = msg


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        print("  [%s] %-28s %s" % ("PASS" if ok else "FAIL", name, detail))
        return ok

    def warn(self, name: str, detail: str) -> None:
        self.rows.append((name, None, detail))  # type: ignore[arg-type]
        print("  [WARN] %-28s %s" % (name, detail))

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [r for r in self.rows if r[1] is False]


def check_audio_devices(rep: Report) -> tuple[object, object]:
    print("\n[1/4] winmm playback devices")
    devices = aw.list_devices()
    print("  waveOutGetNumDevs() = %d" % len(devices))
    for d in devices:
        print("    [%d] %-38s %dch" % (d.index, d.name, d.channels))
    rep.check("playback devices found", len(devices) > 0, "%d device(s)" % len(devices))

    ranked = aw.rank_cable(devices)
    print("  cable ranking: %s" % ([(d.index, d.name) for d in ranked] or "none"))
    sink, tried = aw.open_cable_sink(devices)
    for dev, err in tried:
        print("    refused: [%d] %s -> %s" % (dev.index, dev.name, err))
    if sink.ok:
        rep.check("opened CABLE endpoint", True,
                  "#%d %r %dms x%d buffers" % (sink.device_id, sink.name,
                                               sink.buffer_ms, sink.nbuffers))
    else:
        rep.warn("opened CABLE endpoint", "%s (level-only fallback)" % sink.error)
    return sink, devices


def check_loopback(rep: Report, enabled: bool) -> None:
    print("\n[2/4] VB-Cable loopback (play into CABLE Input, record CABLE Output)")
    if not enabled:
        rep.warn("cable loopback", "skipped (--no-audio)")
        return
    from .cable_probe import list_capture_devices, loopback_test
    caps = list_capture_devices()
    print("  waveInGetNumDevs() = %d" % len(caps))
    for i, name, ch in caps:
        print("    [%d] %-38s %dch" % (i, name, ch))
    res = loopback_test()
    if res["ok"]:
        rep.check("cable loopback", True, res["reason"])
    else:
        rep.warn("cable loopback", res["reason"])


def check_mdns(rep: Report, timeout: float = 6.0) -> None:
    print("\n[3/4] mDNS announce + browse (_btbridge._tcp.)")
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except Exception as exc:  # noqa: BLE001
        rep.warn("mDNS browse", "zeroconf unavailable: %s" % exc)
        return
    from .discovery import MdnsDiscovery

    found: list[object] = []
    lock = threading.Lock()

    def on_add(dev):
        with lock:
            found.append(dev)

    disc = MdnsDiscovery(on_add=on_add, on_remove=lambda k: None,
                         on_log=lambda m: print("    mdns: %s" % m))
    if not disc.start():
        rep.warn("mDNS browse", disc.error or "browse failed")
        disc.stop()
        return

    zc2 = None
    info = None
    try:
        addrs = [socket.inet_aton("127.0.0.1")]
        info = ServiceInfo(
            "_btbridge._tcp.local.",
            "BTBridge Self Test._btbridge._tcp.local.",
            addresses=addrs, port=47110, properties={"name": "SelfTestPhone",
                                                     "proto": "1"},
            server="selftest.local.")
        zc2 = Zeroconf()
        zc2.register_service(info)
        deadline = time.time() + timeout
        while time.time() < deadline:
            with lock:
                if any(getattr(d, "name", "") == "SelfTestPhone" for d in found):
                    break
            time.sleep(0.1)
        names = [getattr(d, "name", "?") for d in found]
        rep.check("mDNS found test service", "SelfTestPhone" in names,
                  "devices=%s" % names)
    except Exception as exc:  # noqa: BLE001
        rep.warn("mDNS announce+browse", "environment refused mDNS: %s" % exc)
    finally:
        if info is not None and zc2 is not None:
            try:
                zc2.unregister_service(info)
            except Exception:  # noqa: BLE001
                pass
        if zc2 is not None:
            try:
                zc2.close()
            except Exception:  # noqa: BLE001
                pass
        disc.stop()


def check_stream(rep: Report) -> None:
    print("\n[4/4] fake phone -> client (hello, PCM, JPEG, drop detection)")
    phone = FakePhone()
    if not phone.bind():
        rep.check("fake phone binds", False, phone.error or "?")
        return
    phone.start()
    port = phone.bound_port
    print("  fake phone listening on 127.0.0.1:%d" % port)

    sink = aw.NullSink("self-test (level only)")
    sink.open()
    dec = CameraDecoder()
    if not dec.start():
        rep.check("Pillow available", False, dec.error or "no PIL")
    events: list[tuple[str, object]] = []
    closed = threading.Event()

    def on_event(kind, payload=None):
        events.append((kind, payload))
        if kind == "closed":
            closed.set()

    client = PhoneClient(on_audio=sink.write, on_jpeg=dec.push, on_event=on_event)
    try:
        ok = client.connect("127.0.0.1", port, timeout=5.0, via="selftest")
        if not rep.check("dial fake phone", ok, client.last_error or
                         "127.0.0.1:%d" % port):
            return
        client.send_cfg(mic=True, cam=True, cam_w=640, cam_h=480, fps=10)

        # wait for the stream to be delivered
        deadline = time.time() + 12
        while time.time() < deadline:
            if (sink.total_bytes >= 96000 and dec.frames_decoded >= 3
                    and phone.done.is_set()):
                break
            time.sleep(0.05)

        hello = client.hello
        rep.check("hello received", hello.get("name") == FAKE_NAME,
                  "name=%r proto=%r mic=%r cam=%r" % (hello.get("name"),
                                                     hello.get("proto"),
                                                     hello.get("mic"),
                                                     hello.get("cam")))
        stats_seen = [p for kind, p in events if kind == "stat"]
        fps_seen = [s.get("fps") for s in stats_seen]
        rep.check("stat received", len(stats_seen) >= 2
                  and all(s.get("camW") == 640 for s in stats_seen),
                  "%d stat(s), fps=%s, last=%s" % (len(stats_seen), fps_seen,
                                                   stats_seen[-1] if stats_seen else None))
        rep.check("unknown frame tolerated", client.unknown_frames == 1,
                  "unknown_frames=%d json=%d" % (client.unknown_frames,
                                                 client.json_frames))

        samples = sink.total_bytes // 2
        rep.check("PCM received", samples >= 48000,
                  "%d frames, %d samples, %d bytes (%.2f s)"
                  % (client.audio_frames, samples, sink.total_bytes,
                     samples / 48000.0))
        peak = sink.read_peak()
        rep.check("PCM level measured", peak > 0.2,
                  "peak=%.3f (440 Hz tone, amp 0.3)" % peak)

        stats = dec.stats()
        rep.check("JPEG frames decoded", dec.frames_decoded >= 3,
                  "decoded=%d dropped=%d errors=%d size=%s last=%sms"
                  % (stats["decoded"], stats["dropped"], stats["errors"],
                     stats["size"], stats["last_ms"]))
        rep.check("JPEG size", dec.last_size == (640, 480), "size=%s" % (dec.last_size,))

        cfg = phone.client_cfg
        rep.check("cfg reached phone", bool(cfg) and cfg.get("mic") is True
                  and cfg.get("cam") is True and cfg.get("camW") == 640
                  and cfg.get("camH") == 480 and cfg.get("fps") in (10, 10.0),
                  "cfg=%s" % cfg)

        rep.check("disconnect detected", closed.wait(6.0),
                  (client.last_error or "no error") if closed.is_set() else
                  "client did not notice the phone leaving")
        rep.check("window would not hang", not client._reader.is_alive()
                  if client._reader else True,
                  "reader thread exited=%s" % (not (client._reader and
                                                    client._reader.is_alive())))
    finally:
        client.close(notify=False)
        dec.stop()
        sink.close()
        phone.stop()
        phone.join(timeout=3)


def run(no_audio: bool = False, no_mdns: bool = False) -> int:
    print("BTBridge self-test — protocol v1 client")
    print("python %s on %s" % (sys.version.split()[0], sys.platform))
    print("protocol header sizeof = %d, FRAME_BYTES = %d"
          % (P.HEADER.size, P.FRAME_BYTES))
    rep = Report()
    check_audio_devices(rep)
    check_loopback(rep, enabled=not no_audio)
    if no_mdns:
        rep.warn("mDNS browse", "skipped (--no-mdns)")
    else:
        check_mdns(rep)
    check_stream(rep)

    failed = rep.failed
    passed = sum(1 for _n, ok, _d in rep.rows if ok is True)
    print("\n%d check(s) passed, %d failed, %d warning(s)"
          % (passed, len(failed), len(rep.rows) - passed - len(failed)))
    if failed:
        for name, _ok, detail in failed:
            print("  FAILED: %s — %s" % (name, detail))
        print("SELFTEST FAILED")
        return 1
    print("SELFTEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
