"""Tray-first mode: no window, no WebView — the UI opens in the browser on demand.

Idle cost is one Python process: pystray + the discovery/clipboard threads.
Nothing polls the audio stack until Speaker is switched on, and the camera only
streams while a window has it enabled. That is what keeps CPU and RAM low.
"""

from __future__ import annotations

import os
import webbrowser

from .server import UiServer
from .webhost import Bridge


def _icon_image():
    from PIL import Image
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "icon.png")
    return Image.open(here)


def run_tray(initial_host: str | None = None) -> int:
    try:
        import pystray
    except Exception:  # noqa: BLE001
        from .webhost import run_gui
        return run_gui(initial_host)

    bridge = Bridge(initial_host)
    server = UiServer(bridge)

    def apply():
        bridge.set_cfg(bridge.mic_enabled, bridge.cam_enabled, bridge.size, bridge.fps,
                       bridge.facing, bridge.spk_enabled, bridge.mute_pc,
                       bridge.clip_enabled)

    def open_ui(icon=None, item=None):
        webbrowser.open(server.start())

    def toggle_mic(icon, item):
        bridge.mic_enabled = not bridge.mic_enabled
        apply()

    def toggle_cam(icon, item):
        bridge.cam_enabled = not bridge.cam_enabled
        apply()

    def toggle_spk(icon, item):
        bridge.spk_enabled = not bridge.spk_enabled
        apply()

    def toggle_clip(icon, item):
        bridge.clip_enabled = not bridge.clip_enabled
        apply()

    def quit_app(icon, item):
        try:
            server.stop()
            bridge.shutdown()
        finally:
            icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open OneLink", open_ui, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Microphone", toggle_mic, checked=lambda item: bridge.mic_enabled),
        pystray.MenuItem("Camera", toggle_cam, checked=lambda item: bridge.cam_enabled),
        pystray.MenuItem("Speaker", toggle_spk, checked=lambda item: bridge.spk_enabled),
        pystray.MenuItem("Clipboard sync", toggle_clip, checked=lambda item: bridge.clip_enabled),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("OneLink", _icon_image(), "OneLink", menu)
    try:
        icon.run()
    finally:
        server.stop()
        bridge.shutdown()
    return 0
