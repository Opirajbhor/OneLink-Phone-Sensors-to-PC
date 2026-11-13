"""Import every module in isolation (catches missing names at import time)."""
import importlib
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODULES = ["btbridge", "btbridge.protocol", "btbridge.discovery", "btbridge.client",
           "btbridge.audio_win", "btbridge.cable_probe", "btbridge.camera",
           "btbridge.selftest", "btbridge.ui", "btbridge.__main__"]
OPTIONAL = {"tkinter", "PIL", "zeroconf"}

bad = 0
for name in MODULES:
    try:
        mod = importlib.import_module(name)
        print("  OK   %-22s %s" % (name, (getattr(mod, "__file__", "") or "").replace("\\", "/")))
    except Exception:
        bad += 1
        print("  FAIL %-22s" % name)
        traceback.print_exc()

# optional third-party dependencies actually resolved?
for dep in sorted(OPTIONAL):
    try:
        m = importlib.import_module(dep)
        print("  dep  %-22s %s" % (dep, getattr(m, "__version__", "?")))
    except Exception as exc:  # noqa: BLE001
        bad += 1
        print("  dep  %-22s MISSING (%s)" % (dep, exc))

# tkinter really usable (needs a display; this box has one)
try:
    import tkinter
    r = tkinter.Tk()
    r.withdraw()
    print("  tk   %-22s Tk %s" % ("tkinter", r.tk.call("info", "patchlevel")))
    r.destroy()
except Exception as exc:  # noqa: BLE001
    bad += 1
    print("  tk   tkinter FAILED: %s" % exc)

print("IMPORTS:", "OK" if not bad else "%d PROBLEM(S)" % bad)
sys.exit(1 if bad else 0)
