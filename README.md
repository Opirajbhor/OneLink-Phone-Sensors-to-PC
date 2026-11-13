# BTBridge

Use an Android phone's **microphone** and **camera** on a Windows PC, and (planned) send the
PC's audio back to the phone. Nothing to root, no virtual-Bluetooth driver, no cloud.

Two halves:

| Dir | What it is |
|---|---|
| `android/` | The phone app (Kotlin, framework only, no AndroidX). One switch, a Quick Settings tile, a foreground service that captures mic + camera and serves them over TCP. |
| `pc/` | The Windows app (Python + Tk). Finds phones by mDNS or adb, connects, plays the phone mic into VB-Cable and previews the camera. |
| `PROTOCOL.md` | The wire protocol both sides speak. Read this first. |

## What works today

- Phone mic → PC, played into **VB-Cable**, so Discord/Zoom/Teams see it as a normal microphone
  (`CABLE Output`). Verified with a real loopback test (tone out `CABLE Input`, recorded back on
  `CABLE Output`).
- Camera → PC preview window (JPEG over TCP).
- Discovery: mDNS `_btbridge._tcp.`, plus `adb devices` and manual `host:port`.
- Transport: **adb forward** (fast, wired) or plain **Wi-Fi/LAN** (no adb, no developer options on
  the PC side; the phone app does the capture itself).

## Not there yet

- PC audio → phone (the AudioRelay direction) — protocol type `0x04` is reserved, playback not
  implemented.
- Real webcam device in Zoom/Discord: v1 shows a preview window only.
- No encryption. LAN/loopback only.

## Build the phone app

Needs JDK 17 and an Android SDK with `platforms;android-35`, `build-tools;35.0.0`.

```bash
export JAVA_HOME=<jdk-17>
export ANDROID_HOME=<android-sdk>
cd android && ./gradlew assembleDebug
# apk: android/app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell pm grant com.btbridge android.permission.RECORD_AUDIO
adb shell pm grant com.btbridge android.permission.CAMERA
```

`android/local.properties` holds `sdk.dir` and must never be committed.

## Run the PC app

```bat
pc\run.bat            :: window
pc\run.bat --selftest :: 15 checks incl. a VB-Cable loopback, no phone needed
```

Requires Python 3.11+ with `tkinter`, `pillow`, `zeroconf`; VB-Cable for the virtual microphone.
`run.bat` finds a suitable interpreter automatically.

## Design notes

- The phone captures its own media, so **no adb is required** — adb is only a faster transport
  when a cable is plugged in.
- The phone holds a partial wakelock while serving; without it the OS suspends the tunnel.
- Audio is raw PCM16 48 kHz mono (no codec, no added latency, ~96 KB/s), camera is JPEG frames.
- Everything is deliberately tiny: no third-party Android libraries, no native code.
