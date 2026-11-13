# OneLink

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
- Camera → PC preview window (JPEG over TCP), with **rear/front switching** and a **rotate**
  control (0/90/180/270, applied instantly on the PC side).
- **PC audio → phone**: speaker toggle captures the PC's output (WASAPI loopback) and plays it on
  the phone, so it can reach the phone's speaker, or Bluetooth headphones paired to the phone.
- Discovery: mDNS `_onelink._tcp.`, plus `adb devices` and manual `host:port`.
- Transport: **adb forward** (fast, wired) or plain **Wi-Fi/LAN** (no adb, no developer options on
  the PC side; the phone app does the capture itself).

Verified against a real phone (Redmi 12, Android 15): hello advertises `audioOut`, `facing`
switches cameras, camera frames arrive, 0x04 audio is accepted with no errors.

## Not there yet

- No encryption. LAN/loopback only.
- Camera is a preview window, not a virtual webcam device (OBS Virtual Camera can take it later).

## Build the phone app

Needs JDK 17 and an Android SDK with `platforms;android-35`, `build-tools;35.0.0`.

```bash
export JAVA_HOME=<jdk-17>
export ANDROID_HOME=<android-sdk>
cd android && ./gradlew assembleDebug
# apk: android/app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell pm grant com.onelink android.permission.RECORD_AUDIO
adb shell pm grant com.onelink android.permission.CAMERA
```

`android/local.properties` holds `sdk.dir` and must never be committed.

## Run the PC app

Run the installer (`release\OneLink-Setup-1.6.exe`) — per-user, no admin prompt — and OneLink
opens its own window: a device list on the left, the live camera/mic/speaker/clipboard state on
the right, and a **Settings** tab holding every switch (microphone, camera, speaker, clipboard
sync), the camera resolution/fps, audio routing and the clipboard history. Closing the window
hides it to the tray; the tray menu mirrors the switches and has Quit. `--tray` starts with no
window at all.

From source:

```bat
pc\run.bat                 :: tray
pc\run.bat --window        :: the WebView window instead
pc\run.bat --serve         :: headless, prints the local URL
pc\run.bat --selftest      :: 15 checks incl. a VB-Cable loopback, no phone needed
```

Requires Python 3.11+ with `tkinter`, `pillow`, `zeroconf`, `pystray`; VB-Cable for the virtual
microphone.

## Release artifacts

`release/` holds the built binaries and is gitignored (rebuild them, don't commit them):

| File | What it is |
|---|---|
| `release/OneLink-Setup-1.6.exe` | the installer — per-user, optional tray autostart |
| `release/app/OneLink/` | the packaged app folder the installer wraps |
| `release/OneLink-v1.3.apk` | the phone app, debug-signed |

Rebuild everything:

```bat
set JAVA_HOME=C:\Users\Opi\BTBridge\toolchain\jdk\jdk-17.0.21+9
set ANDROID_HOME=C:\Users\Opi\BTBridge\toolchain\sdk
cd android && gradlew.bat assembleDebug
copy app\build\outputs\apk\debug\app-debug.apk ..\release\OneLink-v1.3.apk

python -m PyInstaller --noconfirm --onedir --windowed --name OneLink --paths pc ^
  --add-data "pc\onelink\webui;onelink\webui" --icon pc\onelink\webui\icon.ico ^
  --distpath release\app --hidden-import pystray --hidden-import zeroconf ^
  --hidden-import soundcard --hidden-import numpy entry.py

"C:\Users\Opi\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

## Design notes

- The phone captures its own media, so **no adb is required** — adb is only a faster transport
  when a cable is plugged in.
- The phone holds a partial wakelock while serving; without it the OS suspends the tunnel.
- Audio is raw PCM16 48 kHz mono (no codec, no added latency, ~96 KB/s), camera is JPEG frames.
- Everything is deliberately tiny: no third-party Android libraries, no native code.

## Why the audio devices are not named "OneLink"

Renaming a Windows audio endpoint means writing its friendly name in
`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\{Render,Capture}\<guid>\Properties`.
Those keys are owned by SYSTEM and grant Administrators read-only, so the write is
refused even from an elevated shell (`Requested registry access is not allowed`),
and taking ownership (`SeTakeOwnershipPrivilege` enabled, `WRITE_OWNER` open) is
refused too. Only the audio service may touch them.

So the rename has to happen through Windows itself:
Settings → System → Sound → All sound devices → pick the device → Rename.
OneLink has a button for that page (Settings → Audio → "Rename it in Windows").

What OneLink does instead: it finds the virtual cable by *any* of its names —
"OneLink Speaker / OneLink Microphone" (if you renamed it), "CABLE Input /
CABLE Output", or the VB-Audio adapter name — so a rename never breaks routing.
