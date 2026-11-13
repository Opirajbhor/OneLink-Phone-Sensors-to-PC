"""Optional developer check: drive the real Tk window against the fake phone.

Not needed to use the app — `python -m onelink --selftest` covers the core
headlessly. This one opens the GUI, connects it to `FakePhone`, verifies the
level bar / preview / device list, screenshots the window, then connects a peer
that accepts and goes silent to prove the window stays responsive.

    python dev\\gui_smoke.py
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from onelink.selftest import FakePhone
from onelink.ui import App

OUT = os.environ.get("SHOT", "C:/Users/Opi/AppData/Local/hermes/cache/scratch/onelink_ui.png")


def pump(app, seconds, stop_on=None):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.update()
        if stop_on and stop_on():
            return True
        time.sleep(0.01)
    return False


def main():
    phone = FakePhone(audio_seconds=3.0, jpeg_count=6, close_after=8.0)
    # the fake phone must keep sending for the UI test
    phone.audio_delay = 0.02
    assert phone.bind(), phone.error
    phone.start()
    port = phone.bound_port
    print("fake phone on", port)

    app = App()
    app.lift()
    app.focus_force()
    app.ent_host.delete(0, "end")
    app.ent_host.insert(0, "127.0.0.1:%d" % port)
    app._add_manual()
    app.var_mic.set(True)
    app.var_cam.set(True)
    app._on_connect()

    got_video = pump(app, 12, stop_on=lambda: app.decoder.frames_decoded >= 3
                     and app._level > 0.01 and not app._cam_placeholder)
    print("connected:", app.client.status, "via", app.client.via)
    print("audio sink:", type(app.sink).__name__,
          getattr(app.sink, "name", None), "ok=", getattr(app.sink, "ok", None))
    print("frames decoded:", app.decoder.frames_decoded, "level:", round(app._level, 3))
    print("canvas image item set:", bool(app.canvas.itemcget(app._cam_img, "image")))
    print("tree rows:", app.tree.get_children())
    print("stats label:", app.lbl_stats.cget("text"))
    print("audio label:", app.lbl_audio.cget("text").replace("\n", " | "))

    pump(app, 0.5)
    try:
        from PIL import ImageGrab
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        img.save(OUT)
        print("screenshot:", OUT, img.size)
    except Exception as exc:  # noqa: BLE001
        print("screenshot failed:", exc)

    logtxt = app.log.get("1.0", "end")
    bad_threads = ("main thread is not in main loop" in logtxt
                   or "RuntimeError" in logtxt)
    print("worker-thread Tk errors in log:", bad_threads)

    app._on_disconnect()
    print("after disconnect:", app.client.status, "sink:", app.sink)

    # ---- phase 2: a peer that accepts and then goes silent -------------------
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    silent_port = srv.getsockname()[1]
    holder = {}

    def accept_and_hang():
        c, _ = srv.accept()
        holder["c"] = c
        time.sleep(6.0)

    threading.Thread(target=accept_and_hang, daemon=True).start()
    app.client.idle_timeout = 2.0
    app.client.hello_timeout = 2.0   # no hello yet: this limit applies first
    app.ent_host.delete(0, "end")
    app.ent_host.insert(0, "127.0.0.1:%d" % silent_port)
    app._add_manual()
    app._on_connect()

    worst = 0.0
    t0 = time.time()
    stalled = False
    while time.time() - t0 < 5.0:
        s = time.perf_counter()
        app.update()
        worst = max(worst, time.perf_counter() - s)
        if app.client.status == "error":
            stalled = True
            break
        time.sleep(0.01)
    print("silent peer: stalled detected=%s after %.1fs, worst update() = %.3fs"
          % (stalled, time.time() - t0, worst))
    print("silent peer status:", app.lbl_conn.cget("text"), "|", app.client.last_error)
    pump(app, 0.5)

    header_after = app.lbl_conn.cget("text")
    ok = (got_video and not bad_threads and stalled and worst < 0.25
          and "connect failed" not in header_after)

    app._on_disconnect()
    app._on_close()
    phone.stop()
    phone.join(timeout=3)
    try:
        holder.get("c").close()
    except Exception:  # noqa: BLE001
        pass
    srv.close()

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
