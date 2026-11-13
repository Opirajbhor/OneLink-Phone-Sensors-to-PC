"""JPEG decode worker.

The reader thread only hands raw JPEG bytes over; decoding happens on a
separate worker so a slow frame never stalls the socket, and the newest frame
wins (older ones are dropped, exactly like the phone is supposed to do).
"""

from __future__ import annotations

import io
import threading
import time

try:
    from PIL import Image
except Exception as exc:  # noqa: BLE001 - handled by the UI as "no camera"
    Image = None
    _PIL_ERROR = str(exc)
else:
    _PIL_ERROR = ""


class CameraDecoder:
    """Decode JPEG frames off the UI thread.

    ``latest()`` returns ``(seq, PIL.Image)`` or ``(0, None)``. The UI polls it
    from ``after()``; the sequence number tells it whether anything changed.
    """

    def __init__(self, max_queue: int = 2):
        self.error = _PIL_ERROR
        self.frames_in = 0
        self.frames_decoded = 0
        self.frames_dropped = 0
        self.decode_errors = 0
        self.last_decode_ms = 0.0
        self.last_size = (0, 0)

        self._cond = threading.Condition()
        self._pending: bytes | None = None
        self._seq = 0
        self._image = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._max_queue = max(1, max_queue)

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        if Image is None:
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="jpeg-decode",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.5)
        self._thread = None

    # -- producer (reader thread) ------------------------------------------
    def push(self, jpeg: bytes) -> None:
        if not jpeg:
            return
        with self._cond:
            self.frames_in += 1
            if self._pending is not None:
                self.frames_dropped += 1
            self._pending = jpeg           # newest wins, old frame discarded
            self._cond.notify()

    # -- consumer -----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                if self._pending is None:
                    self._cond.wait(0.25)
                jpeg, self._pending = self._pending, None
            if jpeg is None or self._stop.is_set():
                continue
            t0 = time.perf_counter()
            try:
                img = Image.open(io.BytesIO(jpeg))
                img.load()
                if img.mode != "RGB":
                    img = img.convert("RGB")
            except Exception:  # noqa: BLE001 - a torn JPEG must not kill us
                self.decode_errors += 1
                continue
            ms = (time.perf_counter() - t0) * 1000.0
            with self._cond:
                self._image = img
                self._seq += 1
                self.frames_decoded += 1
                self.last_decode_ms = ms
                self.last_size = img.size

    def latest(self) -> tuple[int, object]:
        with self._cond:
            return self._seq, self._image

    def stats(self) -> dict:
        with self._cond:
            return {"in": self.frames_in, "decoded": self.frames_decoded,
                    "dropped": self.frames_dropped, "errors": self.decode_errors,
                    "last_ms": round(self.last_decode_ms, 2),
                    "size": self.last_size}


def fit_rect(img_w: int, img_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    """Largest size fitting (img_w, img_h) inside (box_w, box_h), aspect kept."""
    if img_w <= 0 or img_h <= 0 or box_w <= 0 or box_h <= 0:
        return (0, 0)
    scale = min(box_w / float(img_w), box_h / float(img_h))
    w = max(1, int(round(img_w * scale)))
    h = max(1, int(round(img_h * scale)))
    return (w, h)
