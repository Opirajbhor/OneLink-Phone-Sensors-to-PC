"""Tray-only mode: toggles in the notification area, no window at all.

Used by `--tray` and as the fallback when pywebview cannot start. No browser
hand-off here on purpose — the normal app is a real window
(`desktop.run_app`); this mode exists for sign-in autostart where the user
wants zero windows.
"""

from __future__ import annotations

import os

from .webhost import Bridge


def _icon_image():
    from PIL import Image
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "icon.png")
    return Image.open(here)


def run_tray(initial_host: str | None = None) -> int:
    try:
        import pystray
    except Exception as exc:  # noqa: BLE001
        print("pystray is not available (%s). Install it with: pip install pystray" % exc)
        return 2

    bridge = Bridge(initial_host)

    def quit_app(icon, item):
        try:
            bridge.shutdown()
        finally:
            icon.stop()

    menu = pystray.Menu(
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
    icon = pystray.Icon("OneLink", _icon_image(), "OneLink", menu)
    try:
        icon.run()
    finally:
        bridge.shutdown()
    return 0
