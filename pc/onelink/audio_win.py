"""Windows audio output via winmm ``waveOut`` (ctypes only, no extra packages).

The mic stream from the phone is PCM16 LE mono 48 kHz. We push it into a
playback endpoint belonging to VB-Audio Virtual Cable, so that ``CABLE Output``
(a recording device) can be selected in Discord / Zoom / OBS as the "phone mic".

Two sinks with a common interface:

``WaveOutSink``  real device via waveOutOpen/waveOutWrite, N buffers rotated
                 with WHDR_DONE polling (double/triple buffering).
``NullSink``     no device: still measures level and counts bytes, used when
                 the virtual cable is missing so the rest of the UI keeps
                 working and the level bar stays live.
"""

from __future__ import annotations

import array
import ctypes
import sys
import threading
import time
from ctypes import wintypes

from .protocol import AUDIO_CHANNELS, AUDIO_RATE, AUDIO_SAMPLE_BYTES

# --- winmm constants -------------------------------------------------------
WAVE_FORMAT_PCM = 1
WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_NULL = 0x00000000
WAVE_FORMAT_DIRECT = 0x0008
WHDR_DONE = 0x00000001
WHDR_PREPARED = 0x00000002
MMSYSERR_NOERROR = 0
MAXPNAMELEN = 32

# Device-name ranking for the VB-Audio virtual cable. The user's box exposes
# "CABLE In 16ch (VB-Audio Virtual Cable)" and "CABLE Input (VB-Audio Virtual
# Cable)" — winmm truncates szPname to 31 chars, so match on substrings only.
CABLE_PREFERRED = ("onelink speaker 16", "cable in 16", "onelink speaker",
                   "cable input", "cable in", "onelink", "cable",
                   "vb-audio", "vb audio")


class WAVEFORMATEX(ctypes.Structure):
    # mmreg.h packs these to 1 byte; sizeof(WAVEFORMATEX) == 18.
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),      # DWORD_PTR
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),    # DWORD_PTR
    ]


class WAVEOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * MAXPNAMELEN),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


class AudioDevice:
    __slots__ = ("index", "name", "channels")

    def __init__(self, index: int, name: str, channels: int):
        self.index = index
        self.name = name
        self.channels = channels

    def __repr__(self) -> str:
        return "AudioDevice(%d, %r, %dch)" % (self.index, self.name, self.channels)


_winmm = None
_winmm_error = None


def winmm():
    """Load winmm once and declare the prototypes we use."""
    global _winmm, _winmm_error
    if _winmm is not None:
        return _winmm
    if _winmm_error is not None:
        raise RuntimeError(_winmm_error)
    if not sys.platform.startswith("win"):
        _winmm_error = "winmm audio is only available on Windows"
        raise RuntimeError(_winmm_error)
    dll = ctypes.WinDLL("winmm")
    u32, u64 = ctypes.c_uint, ctypes.c_size_t
    dll.waveOutGetNumDevs.restype = wintypes.UINT
    dll.waveOutGetNumDevs.argtypes = []
    dll.waveOutGetDevCapsW.restype = wintypes.UINT
    dll.waveOutGetDevCapsW.argtypes = [u64, ctypes.POINTER(WAVEOUTCAPSW), wintypes.UINT]
    dll.waveOutOpen.restype = wintypes.UINT
    dll.waveOutOpen.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), wintypes.UINT,
        ctypes.POINTER(WAVEFORMATEX), u64, u64, wintypes.DWORD]
    dll.waveOutPrepareHeader.restype = wintypes.UINT
    dll.waveOutPrepareHeader.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    dll.waveOutUnprepareHeader.restype = wintypes.UINT
    dll.waveOutUnprepareHeader.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    dll.waveOutWrite.restype = wintypes.UINT
    dll.waveOutWrite.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    dll.waveOutReset.restype = wintypes.UINT
    dll.waveOutReset.argtypes = [ctypes.c_void_p]
    dll.waveOutClose.restype = wintypes.UINT
    dll.waveOutClose.argtypes = [ctypes.c_void_p]
    _winmm = dll
    return dll


def error_text(code: int) -> str:
    """Human text for an MMRESULT via waveOutGetErrorTextW."""
    try:
        dll = winmm()
        dll.waveOutGetErrorTextW.restype = wintypes.UINT
        dll.waveOutGetErrorTextW.argtypes = [
            wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        buf = ctypes.create_unicode_buffer(256)
        if dll.waveOutGetErrorTextW(code, buf, 256) == 0:
            return "%d (%s)" % (code, buf.value)
    except Exception:  # noqa: BLE001
        pass
    return "MMRESULT %d" % code


def list_devices() -> list[AudioDevice]:
    """Enumerate waveOut playback endpoints (waveOutGetNumDevs + GetDevCapsW)."""
    dll = winmm()
    out: list[AudioDevice] = []
    for i in range(int(dll.waveOutGetNumDevs())):
        caps = WAVEOUTCAPSW()
        rc = dll.waveOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps))
        if rc != MMSYSERR_NOERROR:
            continue
        out.append(AudioDevice(i, caps.szPname.strip(), int(caps.wChannels)))
    return out


def rank_cable(devices: list[AudioDevice]) -> list[AudioDevice]:
    """VB-Audio cable candidates, best first.

    Preference order: "CABLE In 16ch" > "CABLE Input" > any "CABLE" > "VB-Audio".
    Devices that match none of those are excluded (we never hijack speakers).
    """
    scored = []
    for dev in devices:
        low = dev.name.lower()
        rank = None
        for pos, needle in enumerate(CABLE_PREFERRED):
            if needle in low:
                rank = pos
                break
        if rank is not None:
            scored.append((rank, dev.index, dev))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [dev for _, _, dev in scored]


def find_cable(devices: list[AudioDevice] | None = None) -> AudioDevice | None:
    cands = rank_cable(devices if devices is not None else list_devices())
    return cands[0] if cands else None


def _pcm_peak(chunk: bytes) -> float:
    """Linear peak amplitude (0..1) and RMS of a PCM16 LE chunk -> (peak, rms)."""
    if len(chunk) < 2:
        return 0.0, 0.0
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) & ~1])
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    if not n:
        return 0.0, 0.0
    peak = 0
    acc = 0
    for s in samples:
        if s < 0:
            s = -s
        if s > peak:
            peak = s
        acc += s * s
    return peak / 32768.0, (acc / n) ** 0.5 / 32768.0


class _BaseSink:
    """Interface shared by the real device and the level-only sink."""

    name = "none"
    ok = False
    error = None
    is_null = True

    def open(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def write(self, data: bytes) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def read_peak(self) -> float:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError


class NullSink(_BaseSink):
    """No device: measures level only, so the UI stays useful without VB-Cable."""

    is_null = True

    def __init__(self, reason: str = "no virtual cable device"):
        self.error = reason
        self.ok = False
        self.name = "(no output device)"
        self.total_bytes = 0
        self.total_frames = 0
        self._peak = 0.0
        self._lock = threading.Lock()

    def open(self) -> bool:
        return True

    def write(self, data: bytes) -> None:
        if not data:
            return
        peak, _rms = _pcm_peak(data)
        with self._lock:
            self.total_bytes += len(data)
            self.total_frames += 1
            if peak > self._peak:
                self._peak = peak

    def read_peak(self) -> float:
        with self._lock:
            v = self._peak
            self._peak = 0.0
        return v

    def close(self) -> None:
        pass

    def stats(self) -> dict:
        return {"device": None, "bytes": self.total_bytes,
                "frames": self.total_frames, "null": True}


class WaveOutSink(_BaseSink):
    """Rotating waveOut buffers feeding one playback endpoint.

    ``buffers`` headers of ``buffer_ms`` each are prepared once and reused;
    the feeder thread fills a free one as soon as the driver marks it
    WHDR_DONE. That is plain double/triple buffering without callbacks, so it
    works from any thread and cannot deadlock the Tk loop.
    """

    is_null = False

    def __init__(self, device_id: int, device_name: str = "",
                 rate: int = AUDIO_RATE, channels: int = AUDIO_CHANNELS,
                 buffer_ms: int = 30, buffers: int = 3):
        self.device_id = device_id
        self.name = device_name or ("device #%d" % device_id)
        self.rate = rate
        self.channels = channels
        self.buffer_ms = buffer_ms
        self.nbuffers = max(2, buffers)
        self.ok = False
        self.error = None

        self._bufsize = int(rate * channels * AUDIO_SAMPLE_BYTES * buffer_ms / 1000)
        self._bufsize -= self._bufsize % (channels * AUDIO_SAMPLE_BYTES)
        self._hwo = ctypes.c_void_p()
        self._hdrs: list[WAVEHDR] = []
        self._bufs: list[ctypes.Array] = []
        self._inflight: set[int] = set()
        self._data = bytearray()
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak = 0.0
        self._lock = threading.Lock()
        self._written = 0
        self._underruns = 0
        self._open_flags = CALLBACK_NULL | WAVE_FORMAT_DIRECT
        self._max_buffer = rate * channels * AUDIO_SAMPLE_BYTES * 2  # 2 s cap

    # -- lifecycle ----------------------------------------------------------
    def open(self) -> bool:
        dll = winmm()
        fmt = WAVEFORMATEX(
            WAVE_FORMAT_PCM, self.channels, self.rate,
            self.rate * self.channels * AUDIO_SAMPLE_BYTES,
            self.channels * AUDIO_SAMPLE_BYTES, 16, 0)
        hwo = ctypes.c_void_p()
        rc = dll.waveOutOpen(ctypes.byref(hwo), self.device_id,
                             ctypes.byref(fmt), 0, 0, self._open_flags)
        if rc != MMSYSERR_NOERROR:
            # Some endpoints refuse WAVE_FORMAT_DIRECT for mono/48k; allowing
            # the ACM converter to help is still fine for the cable.
            rc2 = dll.waveOutOpen(ctypes.byref(hwo), self.device_id,
                                  ctypes.byref(fmt), 0, 0, CALLBACK_NULL)
            if rc2 != MMSYSERR_NOERROR:
                self.error = "waveOutOpen(%r) failed: %s" % (self.name, error_text(rc))
                return False
            self._open_flags = CALLBACK_NULL
        self._hwo = hwo

        for _ in range(self.nbuffers):
            buf = ctypes.create_string_buffer(self._bufsize)
            hdr = WAVEHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = self._bufsize
            hdr.dwUser = 0
            rc = dll.waveOutPrepareHeader(hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if rc != MMSYSERR_NOERROR:
                self.error = "waveOutPrepareHeader failed: %s" % error_text(rc)
                self._release()
                return False
            self._bufs.append(buf)
            self._hdrs.append(hdr)

        self._stop.clear()
        self._thread = threading.Thread(target=self._feeder, name="waveout-feeder",
                                        daemon=True)
        self._thread.start()
        self.ok = True
        return True

    def _release(self) -> None:
        dll = winmm()
        if not self._hwo:
            return
        try:
            dll.waveOutReset(self._hwo)
        except Exception:  # noqa: BLE001
            pass
        for hdr in self._hdrs:
            try:
                dll.waveOutUnprepareHeader(self._hwo, ctypes.byref(hdr),
                                           ctypes.sizeof(hdr))
            except Exception:  # noqa: BLE001
                pass
        self._hdrs.clear()
        self._bufs.clear()
        try:
            dll.waveOutClose(self._hwo)
        except Exception:  # noqa: BLE001
            pass
        self._hwo = ctypes.c_void_p()

    def close(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.5)
        self._thread = None
        self._release()
        self.ok = False

    # -- producer side ------------------------------------------------------
    def write(self, data: bytes) -> None:
        if not data or not self.ok:
            return
        with self._cv:
            self._data += data
            over = len(self._data) - self._max_buffer
            if over > 0:                     # keep latency bounded, drop oldest
                del self._data[:over]
            self._cv.notify()

    # -- feeder -------------------------------------------------------------
    def _free_index(self) -> int:
        for i in range(len(self._hdrs)):
            if i not in self._inflight:
                return i
            if self._hdrs[i].dwFlags & WHDR_DONE:
                self._inflight.discard(i)
                return i
        return -1

    def _feeder(self) -> None:
        dll = winmm()
        min_chunk = self.channels * AUDIO_SAMPLE_BYTES   # >= 1 sample frame
        while not self._stop.is_set():
            idx = self._free_index()
            if idx < 0:
                time.sleep(0.004)
                continue
            with self._cv:
                if len(self._data) < min_chunk:
                    self._cv.wait(0.04)
            if self._stop.is_set():
                break
            with self._cv:
                if not self._data:
                    self._underruns += 1
                    continue
                n = min(self._bufsize, len(self._data))
                n -= n % min_chunk
                if n <= 0:
                    continue
                chunk = bytes(self._data[:n])
                del self._data[:n]
            peak, _rms = _pcm_peak(chunk)
            if peak:
                with self._lock:
                    if peak > self._peak:
                        self._peak = peak
            hdr = self._hdrs[idx]
            ctypes.memmove(self._bufs[idx], chunk, len(chunk))
            hdr.dwBufferLength = len(chunk)
            hdr.dwFlags &= ~WHDR_DONE
            rc = dll.waveOutWrite(self._hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if rc != MMSYSERR_NOERROR:
                self.error = "waveOutWrite failed: %s" % error_text(rc)
                time.sleep(0.01)
                continue
            self._inflight.add(idx)
            self._written += len(chunk)

    # -- metering / stats ---------------------------------------------------
    def read_peak(self) -> float:
        with self._lock:
            v = self._peak
            self._peak = 0.0
        return v

    def stats(self) -> dict:
        with self._cv:
            queued = len(self._data)
        return {"device": self.name, "device_id": self.device_id,
                "buffer_ms": self.buffer_ms, "buffers": len(self._hdrs),
                "written": self._written, "queued": queued,
                "underruns": self._underruns, "null": False}


_last_good_device: int | None = None


def open_cable_sink(devices: list[AudioDevice] | None = None,
                    buffer_ms: int = 30, buffers: int = 3):
    """Open the best VB-Audio cable endpoint.

    Candidates are tried best-first; an endpoint that opened last time goes to
    the front (VB-Cable's 16-channel input often cannot be allocated through
    waveOut at all, so we should not retry it on every connect).
    Returns ``(sink, tried)`` where ``tried`` lists ``(device, error)`` for the
    endpoints that refused to open. Falls back to a level-only ``NullSink``.
    """
    global _last_good_device
    devices = list_devices() if devices is None else devices
    ranked = rank_cable(devices)
    if _last_good_device is not None:
        ranked.sort(key=lambda d: 0 if d.index == _last_good_device else 1)
    tried = []
    for dev in ranked:
        sink = WaveOutSink(dev.index, dev.name, buffer_ms=buffer_ms, buffers=buffers)
        if sink.open():
            _last_good_device = dev.index
            return sink, tried
        tried.append((dev, sink.error))
    reason = ("no playback device matching 'CABLE'/'VB-Audio' found"
              if not tried else "virtual cable devices refused to open")
    return NullSink(reason), tried


def describe_devices() -> str:
    devs = list_devices()
    if not devs:
        return "waveOutGetNumDevs() = 0 (no playback devices)"
    lines = ["waveOut playback devices: %d" % len(devs)]
    for d in devs:
        lines.append('  [%d] %-40s %dch' % (d.index, d.name, d.channels))
    pick = find_cable(devs)
    lines.append("picked: %s" % (("#%d %r" % (pick.index, pick.name)) if pick else "none"))
    return "\n".join(lines)
