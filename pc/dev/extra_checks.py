"""Extra checks: stereo downmix + auto-reconnect loop in the real window."""
import array
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from btbridge import protocol as P
from btbridge.selftest import FakePhone
from btbridge.ui import App


def test_downmix():
    pairs = [(100, 200), (-100, -300), (32767, 32767), (0, 0)]
    src = array.array("h")
    for a, b in pairs:
        src.append(a)
        src.append(b)
    mono = P.downmix_stereo_to_mono(src.tobytes())
    out = array.array("h")
    out.frombytes(mono)
    want = [(a + b) // 2 for a, b in pairs]
    print("downmix in=%d pairs out=%d samples %s want %s -> %s"
          % (len(pairs), len(out), list(out), want,
             "PASS" if list(out) == want else "FAIL"))
    print("downmix odd/empty tolerated:",
          P.downmix_stereo_to_mono(b"\x01\x02") == b"", P.downmix_stereo_to_mono(b"") == b"")
    return list(out) == want


def test_reconnect():
    phone = FakePhone(audio_seconds=0.5, jpeg_count=1, close_after=0.2)
    assert phone.bind(), phone.error
    phone.start()
    app = App()
    app.lift()
    app.auto = app._auto_reconnect
    app.auto.set(True)
    app.ent_host.delete(0, "end")
    app.ent_host.insert(0, "127.0.0.1:%d" % phone.bound_port)
    app._add_manual()
    app.var_mic.set(True)
    app.var_cam.set(True)
    app._on_connect()

    t0 = time.time()
    attempts = 0
    closed_listener = False
    while time.time() - t0 < 14:
        app.update()
        txt = app.log.get("1.0", "end")
        attempts = txt.count("auto-reconnect in")
        if phone.done.is_set() and not closed_listener:
            phone.stop()      # close the listener so reconnects fail fast
            closed_listener = True
        if attempts >= 2:
            break
        time.sleep(0.02)
    log = app.log.get("1.0", "end")
    ok = attempts >= 2 and "shutting down" not in log
    print("auto-reconnect attempts scheduled:", attempts, "->", "PASS" if ok else "FAIL")
    app._on_close()
    phone.stop()
    phone.join(timeout=2)
    return ok


ok1 = test_downmix()
ok2 = test_reconnect()
print("RESULT:", "PASS" if (ok1 and ok2) else "FAIL")
sys.exit(0 if (ok1 and ok2) else 1)
