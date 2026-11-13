"""OneLink desktop app: one process, a real window, and a tray icon.

No browser, no address bar, no localhost tab. The window is a pywebview shell
(the same engine Electron uses on this machine) hosted inside our own EXE, so
it appears in the taskbar and Alt-Tab as *OneLink*, not as a web page.

Closing the window hides it to the tray instead of quitting; the process keeps
serving the phone connection and clipboard sync while the window is hidden.
"""

from __future__ import annotations

import os
import threading

from .webhost import INDEX, Bridge


def _icon_image():
    from PIL import Image
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "icon.png")
    return Image.open(here)


def _make_icon(bridge: "Bridge", window, state: dict, apply_cfg):
    import pystray

    def show(icon=None, item=None):
        if window is None:
            return
        window.show()
        window.restore()

    def quit_app(icon, item):
        state["closing"] = True
        try:
            icon.stop()
        finally:
            if window is not None:
                window.destroy()

    menu = pystray.Menu(
        pystray.MenuItem("Open OneLink", show, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Microphone", lambda i, it: bridge.set_mic(not bridge.mic_enabled),
                         checked=lambda it: bridge.mic_enabled),
        pystray.MenuItem("Camera", lambda i, it: bridge.set_cam(not bridge.cam_enabled),
                         checked=lambda it: bridge.cam_enabled),
        pystray.MenuItem("Speaker", lambda i, it: bridge.set_spk(not bridge.spk_enabled),
                         checked=lambda it: bridge.spk_enabled),
        pystray.MenuItem("Clipboard sync", lambda i, it: bridge.set_clip(not bridge.clip_enabled),
                         checked=lambda it: bridge.clip_enabled),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    return pystray.Icon("OneLink", _icon_image(), "OneLink", menu)


def run_app(initial_host: str | None = None, tray: bool = True) -> int:
    try:
        import webview
    except Exception as exc:  # noqa: BLE001
        print("pywebview is not available (%s) — falling back to tray only." % exc)
        from .tray import run_tray
        return run_tray(initial_host)

    bridge = Bridge(initial_host)
    state = {"closing": False}
    window = webview.create_window(
        "OneLink", INDEX, js_api=bridge, width=1140, height=772,
        min_size=(760, 540), background_color="#f4f6fa", text_select=True,
    )

    def on_closing():
        if state["closing"]:
            return True
        window.hide()          # X means "back to the tray", not "quit"
        return False

    try:
        window.events.closing += on_closing
    except Exception:  # noqa: BLE001
        pass

    icon = None
    if tray:
        try:
            icon = _make_icon(bridge, window, state, None)
            threading.Thread(target=icon.run, name="tray", daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            print("tray unavailable: %s" % exc)
            icon = None

    try:
        webview.start()
    finally:
        state["closing"] = True
        bridge.shutdown()
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass
    return 0
