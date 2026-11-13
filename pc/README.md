# BTBridge — PC client (protocol v1)

Windows desktop app that connects to the BTBridge Android phone, plays the phone
microphone into **VB-Cable** (so Discord / Zoom / OBS can use `CABLE Output` as a
normal recording device) and shows the phone camera in a window.

One window, no drivers, no extra services. Protocol spec: [`../PROTOCOL.md`](../PROTOCOL.md).

```
BTBridge/pc/
├─ run.bat                 launcher (finds a suitable Python, forwards args)
├─ README.md               this file
└─ btbridge/
   ├─ __main__.py          python -m btbridge  (+ diagnostic subcommands)
   ├─ protocol.py          frame codec: type|len(BE u32)|payload, resumable reader
   ├─ discovery.py         mDNS (_btbridge._tcp.) + adb devices -l + manual host
   ├─ client.py            PhoneClient: socket, reader thread, cfg/stop/ping
   ├─ audio_win.py         winmm waveOut via ctypes (double/triple buffering)
   ├─ cable_probe.py       plays a tone into CABLE Input, records CABLE Output
   ├─ camera.py            JPEG decode worker (newest frame wins)
   ├─ selftest.py          fake phone + all checks (--selftest)
   └─ ui.py                Tk single window
```

## 1. Install

Requires **Python 3.11+ 64-bit** with tkinter, plus two packages:

```bat
python -m pip install pillow zeroconf
```

VB-Cable must be installed (https://vb-audio.com/Cable/). Playback endpoints
appear as `CABLE Input` / `CABLE In 16ch (VB-Audio Virtual Cable)`; the recording
side is `CABLE Output (VB-Audio Virtual Cable)`.

## 2. Run

```bat
run.bat
```

`run.bat` probes `python`, then `py -3`, then the usual install folders, and
starts the first one that can `import tkinter, PIL, zeroconf`. Arguments are
forwarded: `run.bat --selftest`, `run.bat --list-devices`.

Useful subcommands:

| command | what it does |
|---|---|
| `python -m btbridge` | the GUI |
| `python -m btbridge --selftest` | full headless self-test against a fake phone |
| `python -m btbridge --list-devices` | winmm playback endpoints + which one actually opens |
| `python -m btbridge --list-capture` | winmm recording endpoints (finds `CABLE Output`) |
| `python -m btbridge --probe-audio` | tone into CABLE Input, record CABLE Output |
| `python -m btbridge --host 192.168.1.50:47110` | start with that phone prefilled |

## 3. Connect to the real phone

1. On the phone: start the BTBridge service (big switch on, status `Ready · Wi-Fi`).
2. Make sure PC and phone are on the same Wi-Fi network, phone service is on,
   and the phone's firewall allows inbound TCP **47110**.
3. Start `run.bat`. The **PHONES** list fills automatically:
   * `mDNS` — phone advertises `_btbridge._tcp.` with `name=<model>` in TXT;
   * `adb` — if a USB cable is attached and `adb devices -l` lists it
     (`adb` is taken from `BTBRIDGE_ADB`, then
     `C:\Users\Opi\AppData\Local\Temp\pt\platform-tools\adb.exe`, then `PATH`);
   * `manual` — type `host:port` and press **Add**.
4. Select the phone and press **Connect** (or double-click the row).
5. Set **Mic** and **Camera** (defaults: on, `640x480`, `10 fps`) — every change is
   sent immediately as a `cfg` frame. **Apply cfg** re-sends, **Stop streams**
   sends `stop` (socket stays open).
6. **MIC OUTPUT** shows the endpoint that was opened, e.g.
   `→ CABLE Input (VB-Audio Virtual Cable)`. The level bar and dB readout move
   with the phone mic.
7. In Discord / Zoom / OBS pick **CABLE Output (VB-Audio Virtual Cable)** as the
   microphone. Turn off that app's AGC/noise suppression if the level looks odd.
8. **CAMERA** shows the phone camera, aspect ratio preserved, resized to the
   canvas. Frame counters and decode time are under the preview.

If no `CABLE`/`VB-Audio` playback endpoint exists the window says so in red and
keeps working — the level bar is still live (it is computed from the received
PCM), only routing to apps is missing. Install VB-Cable and press **Rescan audio**;
no restart needed.

### Connection paths (in protocol order)

* **adb (USB, lowest latency)** — `adb forward tcp:47110 tcp:47110` is applied on
  connect and removed on disconnect, then `127.0.0.1:47110` is dialled.
* **USB tethering** — dial the phone's tether address (usually `192.168.42.x`)
  with **Manual**.
* **Wi-Fi** — mDNS result, or **Manual** with the phone's LAN IP.

## 4. Verification without a phone

```bat
run.bat --selftest
```

It starts a fake phone on `127.0.0.1:47110` (falls back to 47111+ if the port is
busy) which sends a `hello`, two `stat`s, an unknown frame type, an unknown JSON
message, 1 s of PCM16 48 kHz mono (50 × 20 ms frames of a 440 Hz tone) and three
640×480 JPEGs, then hangs up. The real client, real winmm sink and real Pillow
decoder process it. Checks:

* winmm enumeration and opening a cable endpoint;
* a tone played into `CABLE Input` is recorded back from `CABLE Output`
  (proves the routing Discord/Zoom will use);
* mDNS announce + browse finds a `_btbridge._tcp.` service and reads its TXT name;
* hello / stat / tolerance of unknown frames, ≥48 000 samples received, peak
  level ≈ 0.3, 3 JPEGs decoded at 640×480, `cfg` arriving at the phone with
  `mic/cam/camW/camH/fps`, and the dropped connection being noticed.

Exit code 0 = pass. `--no-audio` skips the cable loopback (e.g. if another app
holds `CABLE Output`), `--no-mdns` skips the mDNS part.

Developer checks (optional, no phone needed):

```bat
python dev\imports_check.py    :: every module imports + deps + Tk resolves
python dev\gui_smoke.py        :: drives the real window against the fake phone, screenshots it
python dev\extra_checks.py     :: stereo downmix + auto-reconnect loop
```

## 5. Troubleshooting

| symptom | cause / fix |
|---|---|
| no phones in the list | phone service off, different network, or Windows Firewall blocking inbound mDNS/UDP 5353 — use **Manual** `ip:47110` |
| `connect failed: timed out` | phone not reachable on 47110; check `ping <phone>` and that the phone app is serving |
| `adb: 0 device(s)` | no USB debugging authorisation; run `adb devices` and accept the prompt on the phone |
| `adb forward failed` | another adb client owns the port; `adb kill-server`, then refresh |
| MIC OUTPUT red | no VB-Cable playback endpoint; install VB-Cable, **Rescan audio** |
| level moves but Discord hears nothing | pick `CABLE Output (VB-Audio Virtual Cable)` as the input device in that app |
| camera blank, stats show 0 fps | phone camera busy / permission denied — the phone sends `{"t":"err"}` which appears in the log |
| window stays responsive but nothing arrives | the client declares the link dead after 15 s of silence and closes the socket |

## 6. Notes on the implementation

* **Threading**: one reader thread per connection, one JPEG decode thread, one
  waveOut feeder thread. Widgets are only touched from the Tk thread; workers
  post events into a queue drained every 60 ms, so a stalled phone cannot freeze
  the window (verified: worst `update()` 33 ms while a peer goes silent).
* **Audio**: `waveOutOpen` on the chosen device id with `WAVE_FORMAT_PCM`,
  48 kHz mono 16-bit, 30 ms buffers × 3, rotated by polling `WHDR_DONE` (no
  window callbacks). If the endpoint rejects `WAVE_FORMAT_DIRECT` it is reopened
  with the ACM converter. Queued audio is capped at 2 s and the oldest data is
  dropped if the phone outruns the device.
* **Device pick**: playback names are matched case-insensitively against
  `cable in 16`, `cable input`, `cable in`, `cable`, `vb-audio`, `vb audio`
  (winmm truncates names to 31 chars). Candidates are tried in that order and
  the first one that actually opens wins; the winner is remembered. On this
  machine `CABLE In 16ch` refuses allocation (`MMRESULT 4`) and
  `CABLE Input` is used.
* **Video**: JPEGs are decoded off the UI thread, one at a time, newest wins;
  the canvas is repainted at ~15 fps and images are LANCZOS-free bilinear
  resizes to keep the UI smooth.
* **Robustness**: any frame type and any interleaving is accepted; unknown frame
  types are counted and ignored; oversized frames (>32 MB) are a protocol error;
  a partial frame is never lost when the read timeout fires.
