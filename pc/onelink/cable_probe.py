"""End-to-end VB-Cable check without a phone.

Plays a 1 kHz tone into the playback endpoint we would use for the phone mic
(``CABLE Input``) and records ``CABLE Output`` with winmm ``waveIn``. If the
tone comes back, the routing that Discord/Zoom will see is proven working.

Used by ``python -m onelink --probe-audio`` and by ``--selftest``.
"""

from __future__ import annotations

import array
import ctypes
import math
import sys
import time
from ctypes import wintypes

from . import audio_win as aw

WAVE_FORMAT_PCM = 1
CALLBACK_NULL = 0
MAXPNAMELEN = 32


class WAVEINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * MAXPNAMELEN),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
    ]


_winmm_in = None


def _dll():
    global _winmm_in
    if _winmm_in is not None:
        return _winmm_in
    d = ctypes.WinDLL("winmm")
    d.waveInGetNumDevs.restype = wintypes.UINT
    d.waveInGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(WAVEINCAPSW),
                                    wintypes.UINT]
    d.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.UINT,
                             ctypes.POINTER(aw.WAVEFORMATEX), ctypes.c_size_t,
                             ctypes.c_size_t, wintypes.DWORD]
    d.waveInPrepareHeader.argtypes = [ctypes.c_void_p, ctypes.POINTER(aw.WAVEHDR),
                                      wintypes.UINT]
    d.waveInAddBuffer.argtypes = [ctypes.c_void_p, ctypes.POINTER(aw.WAVEHDR),
                                  wintypes.UINT]
    d.waveInStart.argtypes = [ctypes.c_void_p]
    d.waveInStop.argtypes = [ctypes.c_void_p]
    d.waveInReset.argtypes = [ctypes.c_void_p]
    d.waveInUnprepareHeader.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(aw.WAVEHDR), wintypes.UINT]
    d.waveInClose.argtypes = [ctypes.c_void_p]
    _winmm_in = d
    return d


def list_capture_devices() -> list[tuple[int, str, int]]:
    """[(index, name, channels)] of waveIn recording endpoints."""
    d = _dll()
    out = []
    for i in range(int(d.waveInGetNumDevs())):
        caps = WAVEINCAPSW()
        if d.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append((i, caps.szPname.strip(), int(caps.wChannels)))
    return out


def find_cable_output(devices=None):
    devices = list_capture_devices() if devices is None else devices
    for i, name, ch in devices:
        low = name.lower()
        if "cable output" in low or ("vb-audio" in low and "output" in low):
            return (i, name, ch)
    return None


def _rms(chunk: bytes) -> float:
    if len(chunk) < 2:
        return 0.0
    s = array.array("h")
    s.frombytes(chunk[: len(chunk) & ~1])
    if not s:
        return 0.0
    return (sum(x * x for x in s) / len(s)) ** 0.5 / 32768.0


def _record_start(dev_id: int, seconds: float, channels: int = 1):
    d = _dll()
    fmt = aw.WAVEFORMATEX(WAVE_FORMAT_PCM, channels, aw.AUDIO_RATE,
                          aw.AUDIO_RATE * channels * 2, channels * 2, 16, 0)
    hwi = ctypes.c_void_p()
    for ch in (channels, 2):
        fmt = aw.WAVEFORMATEX(WAVE_FORMAT_PCM, ch, aw.AUDIO_RATE,
                              aw.AUDIO_RATE * ch * 2, ch * 2, 16, 0)
        hwi = ctypes.c_void_p()
        rc = d.waveInOpen(ctypes.byref(hwi), dev_id, ctypes.byref(fmt), 0, 0,
                          CALLBACK_NULL)
        if rc == 0:
            channels = ch
            break
    else:
        return None, "waveInOpen failed: %s" % aw.error_text(rc)
    size = int(aw.AUDIO_RATE * channels * 2 * seconds)
    buf = ctypes.create_string_buffer(size)
    hdr = aw.WAVEHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
    hdr.dwBufferLength = size
    rc = d.waveInPrepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
    if rc:
        d.waveInClose(hwi)
        return None, "waveInPrepareHeader failed: %s" % aw.error_text(rc)
    rc = d.waveInAddBuffer(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
    if rc:
        d.waveInClose(hwi)
        return None, "waveInAddBuffer failed: %s" % aw.error_text(rc)
    rc = d.waveInStart(hwi)
    if rc:
        d.waveInClose(hwi)
        return None, "waveInStart failed: %s" % aw.error_text(rc)
    return (hwi, buf, hdr), None


def _record_finish(rec) -> bytes:
    d = _dll()
    hwi, buf, hdr = rec
    d.waveInStop(hwi)
    d.waveInReset(hwi)
    d.waveInUnprepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
    data = bytes(buf[: hdr.dwBytesRecorded])
    d.waveInClose(hwi)
    return data


def loopback_test(seconds: float = 0.7, amplitude: int = 9000,
                  freq: float = 1000.0) -> dict:
    """Play a tone into the cable and record it back.

    Returns a dict: ok, reason, rms, recorded_bytes, playback, capture.
    """
    result = {"ok": False, "reason": "", "rms": 0.0, "recorded_bytes": 0,
              "playback": None, "capture": None, "expected_rms": 0.0}
    if not sys.platform.startswith("win"):
        result["reason"] = "not Windows"
        return result

    caps = list_capture_devices()
    cap = find_cable_output(caps)
    if cap is None:
        result["reason"] = ("no 'CABLE Output' recording endpoint "
                            "(capture devices: %s)"
                            % ", ".join(n for _i, n, _c in caps) or "none")
        return result
    result["capture"] = cap[1]

    devices = aw.list_devices()
    sink, tried = aw.open_cable_sink(devices)
    result["playback"] = sink.name
    if not sink.ok:
        result["reason"] = ("could not open any CABLE playback endpoint: %s"
                            % "; ".join("%s -> %s" % (d.name, e) for d, e in tried)
                            or sink.error or "unknown")
        sink.close()
        return result

    rec, err = _record_start(cap[0], seconds + 0.35)
    if err:
        sink.close()
        result["reason"] = err
        return result

    n = 480
    phase = 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        block = array.array("h", [
            int(amplitude * math.sin(2 * math.pi * freq * (phase + i) / aw.AUDIO_RATE))
            for i in range(n)])
        sink.write(block.tobytes())
        phase += n
        time.sleep(n / float(aw.AUDIO_RATE) * 0.98)
    time.sleep(0.25)

    data = _record_finish(rec)
    waited = 0.0
    while waited < 1.0:
        peak = sink.read_peak()
        if peak == 0.0:
            break
        time.sleep(0.02)
        waited += 0.02
    sink.close()

    lvl = _rms(data)
    result["rms"] = round(lvl, 4)
    result["expected_rms"] = round(amplitude / 32768.0 / math.sqrt(2), 4)
    result["recorded_bytes"] = len(data)
    if len(data) == 0:
        result["reason"] = "CABLE Output returned no samples"
        return result
    if lvl < 0.02:
        result["reason"] = "CABLE Output was silent (rms %.4f)" % lvl
        return result
    result["ok"] = True
    result["reason"] = "tone played on %r and recorded at %.4f rms" % (
        sink.name, lvl)
    return result
