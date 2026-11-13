"""Entry point: ``python -m onelink`` (GUI) or the diagnostic subcommands."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="onelink",
        description="OneLink PC client — protocol v1 (mic to VB-Cable, camera preview)")
    ap.add_argument("--host", help="prefill/dial this phone address (host:port)")
    ap.add_argument("--selftest", action="store_true",
                    help="run the headless self-test against a fake phone and exit")
    ap.add_argument("--no-audio", action="store_true",
                    help="with --selftest: skip the VB-Cable loopback probe")
    ap.add_argument("--no-mdns", action="store_true",
                    help="with --selftest: skip the mDNS check")
    ap.add_argument("--tk", action="store_true",
                    help="use the classic Tk window instead of the web UI")
    ap.add_argument("--tray", action="store_true",
                    help="tray only, no window (used for sign-in autostart)")
    ap.add_argument("--window", action="store_true",
                    help="window only, no tray icon")
    ap.add_argument("--no-tray", action="store_true", help="alias for --window")
    ap.add_argument("--serve", action="store_true",
                    help="headless: serve the UI on 127.0.0.1 and print the URL")
    ap.add_argument("--list-devices", action="store_true",
                    help="print the winmm playback device list and exit")
    ap.add_argument("--list-capture", action="store_true",
                    help="print the winmm recording device list and exit")
    ap.add_argument("--probe-audio", action="store_true",
                    help="play a tone into CABLE Input and record CABLE Output")
    ap.add_argument("--version", action="version", version="onelink 1.0.0")
    args = ap.parse_args(argv)

    if args.selftest:
        from .selftest import run
        return run(no_audio=args.no_audio, no_mdns=args.no_mdns)

    if args.list_devices:
        from . import audio_win
        devices = audio_win.list_devices()
        print("waveOutGetNumDevs() = %d" % len(devices))
        for d in devices:
            print("  [%d] %-40s %dch" % (d.index, d.name, d.channels))
        ranked = audio_win.rank_cable(devices)
        print("cable candidates (best first): %s"
              % ([(d.index, d.name) for d in ranked] or "none"))
        sink, tried = audio_win.open_cable_sink(devices)
        for dev, err in tried:
            print("  refused [%d] %s: %s" % (dev.index, dev.name, err))
        opened = bool(sink.ok)
        if opened:
            print("OPENS: [%d] %r (%d ms x %d buffers)"
                  % (sink.device_id, sink.name, sink.buffer_ms, sink.nbuffers))
            sink.close()
        else:
            print("OPENS: nothing — %s" % sink.error)
        return 0 if opened else 1

    if args.list_capture:
        from .cable_probe import list_capture_devices
        caps = list_capture_devices()
        print("waveInGetNumDevs() = %d" % len(caps))
        for i, name, ch in caps:
            print("  [%d] %-40s %dch" % (i, name, ch))
        return 0

    if args.probe_audio:
        from .cable_probe import loopback_test
        res = loopback_test()
        print("playback : %s" % res["playback"])
        print("capture  : %s" % res["capture"])
        print("rms      : %s (expected ~%s)" % (res["rms"], res["expected_rms"]))
        print("bytes    : %s" % res["recorded_bytes"])
        print("%s: %s" % ("PASS" if res["ok"] else "FAIL", res["reason"]))
        return 0 if res["ok"] else 1

    # GUI: one instance per machine, however it was launched
    if not args.serve:
        from .single import acquire
        if not acquire():
            print("OneLink is already running - brought its window to the front.")
            return 0

    if args.tk:
        from .ui import run_gui
        return run_gui(initial_host=args.host)
    if args.serve:
        from .server import UiServer
        from .webhost import Bridge
        bridge = Bridge(args.host)
        ui = UiServer(bridge)
        print(ui.start(), flush=True)
        try:
            while True:
                import time
                time.sleep(3600)
        except KeyboardInterrupt:
            ui.stop()
            bridge.shutdown()
        return 0
    if args.window or args.no_tray:
        from .desktop import run_app
        return run_app(initial_host=args.host, tray=False)
    if args.tray:
        from .tray import run_tray
        return run_tray(initial_host=args.host)
    from .desktop import run_app
    return run_app(initial_host=args.host, tray=True)


if __name__ == "__main__":
    sys.exit(main())
