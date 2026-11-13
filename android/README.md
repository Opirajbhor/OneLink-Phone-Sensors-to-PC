# OneLink — Android (phone side), protocol v1

Kotlin, framework APIs only (no AndroidX, no third-party runtime libraries).
minSdk 26, targetSdk 35, applicationId `com.onelink`.

## Build

```
export JAVA_HOME="C:/Users/Opi/OneLink/toolchain/jdk/jdk-17.0.21+9"
export ANDROID_HOME="C:/Users/Opi/OneLink/toolchain/sdk"
cd C:/Users/Opi/OneLink/android
./gradlew assembleDebug        # -> app/build/outputs/apk/debug/app-debug.apk
./gradlew --stop               # shut the daemon down when done
```

## What it does

- `OneLinkService` (foreground, persistent notification, PARTIAL_WAKE_LOCK while serving)
  owns the TCP server on `0.0.0.0:47110`, mDNS (`_onelink._tcp.`, TXT `name`/`proto=1`),
  mic capture and camera capture.
- Frames: `type(1) | length(BE u32) | payload`. 0x01 PCM16 mono 48 kHz, 0x02 JPEG,
  0x03 JSON control, 0x04 PCM16 stereo (inbound, answered with `{"t":"err"}` — v0.2 feature).
- Handshake `hello`, then `cfg` / `ping` / `stop`; phone answers `stat` (every 2 s and on
  change), `pong`, `err`.
- Mic: `AudioRecord(MIC, 48000, mono, PCM16)`, 960-sample (1920-byte) frames, no resampling,
  no AGC/NS requested.
- Camera: Camera2 + `ImageReader(JPEG)`, nearest negotiated size, request loop at the
  requested fps; frames are dropped (`ReentrantLock.tryLock`) instead of queued when the
  socket is slow. Camera busy -> `{"t":"err","msg":"camera in use"}`.
- One client at a time: a new connection closes the previous socket.
- `MainActivity`: big on/off switch, status, device model, listen address; asks for
  RECORD_AUDIO + CAMERA (+ POST_NOTIFICATIONS on 33+) on first start.
- `OneLinkTileService`: Quick Settings tile, state read from the running service on every
  refresh and on the `com.onelink.STATE` broadcast.
- `OneLinkReceiver`: `com.onelink.TOGGLE` / `START` / `STOP` automation actions
  (e.g. `adb shell am broadcast -a com.onelink.TOGGLE -n com.onelink/.OneLinkReceiver`).
