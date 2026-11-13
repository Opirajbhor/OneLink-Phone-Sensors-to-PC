"""PC -> phone audio: WASAPI loopback capture, 20 ms PCM16 stereo frames.

Uses the ``soundcard`` package (WASAPI loopback, no driver of our own). The
captured audio is what the PC is currently playing, converted to the phone's
wire format (type 0x04: PCM16 LE, stereo, 48 kHz).

Nothing here touches the UI thread; ``level`` is a plain float read by the
poller.
"""

from __future__ import annotations

import threading
import time

SAMPLE_RATE = 48000
BLOCK = 960          # 20 ms
CHANNELS = 2

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None

try:
    import soundcard as sc
except Exception:  # noqa: BLE001
    sc = None


def loopback_devices() -> list[str]:
    """Names of speakers whose loopback we can capture."""
    if sc is None:
        return []
    try:
        return [s.name for s in sc.all_speakers()]
    except Exception:  # noqa: BLE001
        return []


def _pick_speaker(preferred: str | None):
    """Default speaker first; skip VB-Cable (that is our own plumbing)."""
    if sc is None:
        return None
    candidates = []
    try:
        candidates.append(sc.default_speaker())
    except Exception:  # noqa: BLE001
        pass
    try:
        candidates.extend(sc.all_speakers())
    except Exception:  # noqa: BLE001
        return None
    if preferred:
        for s in candidates:
            if s.name == preferred:
                candidates.insert(0, s)
                break
    for s in candidates:
        if s is None:
            continue
        low = s.name.lower()
        if "cable" in low or "vb-audio" in low:
            continue          # capturing our own virtual cable would loop
        return s
    return candidates[0] if candidates else None


class LoopbackSender:
    """Captures the PC's output and pushes int16 frames to ``on_frame``."""

    def __init__(self, on_frame, speaker_name: str | None = None):
        self.on_frame = on_frame
        self.speaker_name = speaker_name
        self.error: str | None = None
        self.level = 0.0
        self.sent_bytes = 0
        self.sent_frames = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.device_name = ""

    @property
    def ok(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self.error

    def start(self) -> bool:
        if self._thread is not None:
            return True
        if sc is None or np is None:
            self.error = "soundcard/numpy not installed (pip install soundcard)"
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="loopback", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=1.5)
        self.level = 0.0

    # ------------------------------------------------------------------ loop
    def _run(self):
        try:
            speaker = _pick_speaker(self.speaker_name)
            if speaker is None:
                self.error = "no speaker to capture"
                return
            self.device_name = speaker.name
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            # A little drift is fine: we only forward, the phone buffers.
            with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS,
                              blocksize=BLOCK) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=BLOCK)
                    if data is None or len(data) == 0:
                        continue
                    self.level = float(abs(data).max())
                    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                    self.sent_bytes += len(pcm)
                    self.sent_frames += 1
                    try:
                        self.on_frame(pcm)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc) or exc.__class__.__name__
        finally:
            self.level = 0.0

    def stats(self) -> dict:
        return {"ok": self.ok, "level": self.level, "device": self.device_name,
                "error": self.error, "kbps": 0.0}
