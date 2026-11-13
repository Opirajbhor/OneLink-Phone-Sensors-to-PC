"""OneLink wire protocol v1: framing, frame types, constants.

Every frame is ``type (1 byte) | length (u32 BE) | payload``.
See PROTOCOL.md at the repository root.
"""

from __future__ import annotations

import array
import json
import struct
import sys

# --- frame types -----------------------------------------------------------
T_PCM_MONO = 0x01     # PCM16 LE, mono, 48 kHz  (phone -> PC)
T_JPEG = 0x02         # one JPEG image          (phone -> PC)
T_JSON = 0x03         # UTF-8 JSON control message
T_PCM_STEREO = 0x04   # PCM16 LE, stereo, 48 kHz (v0.2, phone plays it)

TYPE_NAMES = {
    T_PCM_MONO: "pcm",
    T_JPEG: "jpeg",
    T_JSON: "json",
    T_PCM_STEREO: "pcm-stereo",
}

# --- audio format ----------------------------------------------------------
AUDIO_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_BYTES = 2
FRAME_SAMPLES = 960                       # 20 ms at 48 kHz
FRAME_BYTES = FRAME_SAMPLES * AUDIO_SAMPLE_BYTES   # 1920

# sanity ceiling for a single frame (a phone never sends anything this big)
MAX_PAYLOAD = 32 * 1024 * 1024

PROTO_VERSION = 1
DEFAULT_PORT = 47110
SERVICE_TYPE = "_onelink._tcp.local."

HEADER = struct.Struct(">BI")             # type, big-endian u32 length


class ProtocolError(Exception):
    """The peer sent something that is not a valid v1 frame."""


class ConnectionClosed(Exception):
    """The peer closed the socket (cleanly or not)."""


def pack_frame(ftype: int, payload: bytes) -> bytes:
    return HEADER.pack(ftype, len(payload)) + payload


def pack_json(obj: dict) -> bytes:
    return pack_frame(
        T_JSON, json.dumps(obj, separators=(",", ":")).encode("utf-8")
    )


def parse_json(payload: bytes) -> dict:
    try:
        obj = json.loads(payload.decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - tolerate junk from the phone
        raise ProtocolError("bad JSON control frame: %s" % exc) from exc
    if not isinstance(obj, dict):
        raise ProtocolError("control frame is not a JSON object")
    return obj


def looks_like_hello(msg: dict) -> bool:
    return msg.get("t") == "hello"


def downmix_stereo_to_mono(payload: bytes) -> bytes:
    """Average L/R pairs of PCM16 LE stereo into PCM16 LE mono.

    v1 does not send type 0x04 from the phone, but we must tolerate any frame
    type: playing interleaved stereo into a mono sink would be noise.
    """
    if len(payload) < 4:
        return b""
    n = len(payload) & ~3
    src = array.array("h")
    src.frombytes(payload[:n])
    if sys.byteorder == "big":
        src.byteswap()
    out = array.array("h", bytes(2 * (n // 4)))
    for i in range(n // 4):
        out[i] = (src[2 * i] + src[2 * i + 1]) // 2
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


class FrameReader:
    """Incremental frame parser over a (possibly timeout-y) socket.

    Buffers partial data internally so a ``socket.timeout`` mid-frame loses
    nothing: the next call resumes exactly where it stopped. That is what lets
    the client keep a short read timeout for stall detection without corrupting
    the stream.
    """

    def __init__(self, sock, max_payload: int = MAX_PAYLOAD):
        self.sock = sock
        self.max_payload = max_payload
        self._buf = bytearray()

    def read_frame(self) -> tuple[int, bytes]:
        while True:
            if len(self._buf) >= HEADER.size:
                ftype, length = HEADER.unpack_from(self._buf, 0)
                if length > self.max_payload:
                    raise ProtocolError(
                        "frame too large: type=0x%02X length=%d" % (ftype, length)
                    )
                total = HEADER.size + length
                if len(self._buf) >= total:
                    payload = bytes(self._buf[HEADER.size:total])
                    del self._buf[:total]
                    return ftype, payload
                want = total - len(self._buf)
            else:
                want = HEADER.size - len(self._buf)

            chunk = self.sock.recv(max(want, 65536))
            if not chunk:
                raise ConnectionClosed("peer closed the connection")
            self._buf += chunk
