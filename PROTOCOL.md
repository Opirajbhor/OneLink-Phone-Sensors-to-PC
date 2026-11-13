# OneLink — wire protocol v1

Two parts: an Android app that captures and serves, a Windows app that discovers, connects,
and uses the streams. No adb required; adb is an optional faster path.

## Transport

- The phone listens on **TCP port 47110** bound to `0.0.0.0`.
- Connection paths, in the order the PC should try them:
  1. **adb forward** (USB cable, fastest): `adb forward tcp:47110 tcp:47110`, then dial `127.0.0.1:47110`.
  2. **USB tethering**: phone tethers, PC gets `192.168.42.x`, dial the phone's tether IP.
  3. **Wi-Fi LAN**: dial the phone's IP, discovered by mDNS (below).
- One client at a time. A new connection replaces the previous one (close the old socket).
- No encryption in v1. LAN/loopback only, and the phone must refuse connections when the
  service is off.

## Discovery

mDNS/DNS-SD service: `_onelink._tcp.` (local), port 47110.
TXT records: `name=<device model>`, `proto=1`.

## Framing

Every frame in either direction is:

```
+--------+------------------+-----------------+
| type   | length (BE u32)   | payload         |
| 1 byte | 4 bytes           | length bytes    |
+--------+------------------+-----------------+
```

| type | meaning |
|---|---|
| 0x01 | PCM audio, 16-bit signed LE, mono, 48000 Hz |
| 0x02 | one JPEG image |
| 0x03 | UTF-8 JSON control message |
| 0x04 | PCM audio, 16-bit signed LE, stereo, 48000 Hz (phone plays it) — v0.2 |

## Handshake

Immediately after accept, the phone sends one type-0x03 JSON line:

```json
{"t":"hello","proto":1,"name":"Redmi 7","mic":true,"cam":true,"audioOut":false}
```

`name` is the phone model (e.g. `ro.product.model`).

## PC -> phone (type 0x03)

```json
{"t":"cfg","mic":true,"cam":true,"camW":640,"camH":480,"fps":10,"facing":"back","audioOut":false}
{"t":"ping"}
{"t":"stop"}
```

- `cfg` selects which streams are running and the camera geometry/frame rate. Missing fields keep
  their current value. Defaults: `mic=false, cam=false, camW=640, camH=480, fps=10,
  facing="back", audioOut=false`.
- `facing` is `"back"` or `"front"`; changing it reopens the camera.
- `audioOut` asks the phone to play the PCM frames the PC sends (type 0x04).
- `stop` stops all capture but keeps the socket open (so the tile stays "on" and the phone idles).

## Phone -> PC (type 0x03)

```json
{"t":"stat","mic":true,"cam":true,"fps":9.7,"camW":640,"camH":480,"facing":"back","audioOut":true}
{"t":"pong"}
{"t":"err","msg":"camera in use"}
{"t":"clip","text":"hello","hash":"3a7bd3e2360a3d29","id":"9f2c1ab34de5","origin":"phone","ts":1763000000000}
```

`stat` is sent every 2 s and on any state change.

## Clipboard (type 0x03, either direction)

```json
{"t":"clip","text":"...","hash":"<16 hex>","id":"<12 hex>","origin":"pc|phone","ts":<ms>}
```

Loop rules both sides must implement:

1. `hash` is the first 16 hex chars of SHA-1 over the UTF-8 text. Before applying an
   incoming clip, record its hash as "last applied"; when the local clipboard listener
   then fires with that same hash, skip it.
2. Remember recent `id`s; a clip whose id was already seen is dropped.
3. Debounce local copies (~250 ms) so a burst of copies becomes one message.
4. Text is capped at 4000 chars per clip.

History: newest first, de-duplicated by hash, 50 entries. PC persists it in
`%APPDATA%\OneLink\clipboard.json`; the phone keeps it in SharedPreferences
(`onelink_clip`). Clicking a PC history entry puts it back on the clipboard
(`copy_to_clip`).

## Media

- **Mic**: continuous 0x01 frames of PCM16 mono 48 kHz. 20 ms per frame (960 samples, 1920 bytes)
  is the target. The phone must not resample and must not apply AGC/NS.
- **Camera**: 0x02 frames, one JPEG each, at the negotiated size and rate. The phone must drop
  frames rather than queue them if the socket cannot keep up.
- The PC must tolerate any frame size and any interleaving of types.

## Phone app behaviour

- One screen: a big on/off switch, the current status (`Ready · Wi-Fi`, `Streaming · mic+cam`),
  the device name, and the listen address.
- A Quick Settings tile that mirrors and toggles the same state. The tile state must always
  reflect the service state, never a local guess.
- While serving: hold a partial wakelock (the phone must not suspend mid-stream) and run a
  foreground service with a persistent notification.
- Microphone and camera are captured **by the app** (runtime permissions asked on first start).
- Stopping the service must stop capture, release the wakelock, stop mDNS, and close the socket.
- Broadcast receiver for automation: action `com.onelink.TOGGLE` flips the state,
  `com.onelink.START` and `com.onelink.STOP` set it.

## Windows app behaviour

- Auto-discovers phones: mDNS browse of `_onelink._tcp.`, plus `adb devices -l` when adb is
  available, plus manual `host:port`. The list shows the device name from TXT/`ro.product.model`.
- One window: device list, Connect/Disconnect, checkboxes for **Mic** and **Camera**, a live mic
  level, and a camera preview.
- Mic: play the PCM into the Windows playback device whose name contains `CABLE Input`, so
  `CABLE Output` becomes a normal recording device for Discord/Zoom. If VB-Cable is missing, say
  so in the UI instead of failing silently.
- Camera: decode JPEG and show it in the window (no virtual camera driver in v1).
- Never block the UI thread; a stalled phone must not freeze the window.
