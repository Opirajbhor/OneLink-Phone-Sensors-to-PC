"""Single-window Tk UI.

Threading contract:
  * every socket read happens on ``PhoneClient``'s reader thread;
  * every JPEG decode happens on ``CameraDecoder``'s worker;
  * audio bytes go straight from the reader into the winmm sink's feeder;
  * threads never touch widgets — they push tuples into ``self._ui_q`` and the
    Tk loop drains it from ``_pump()`` (every 60 ms), so a stalled phone can
    never freeze the window.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
import traceback

import tkinter as tk
from tkinter import ttk

from . import __version__, audio_win, discovery
from .camera import CameraDecoder, fit_rect
from .client import PhoneClient
from .protocol import DEFAULT_PORT

try:
    from PIL import Image, ImageTk
    _BILINEAR = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
except Exception:  # noqa: BLE001
    Image = None
    ImageTk = None
    _BILINEAR = None

BG = "#17171c"
PANEL = "#212129"
PANEL_2 = "#2a2a34"
TEXT = "#e8e8ee"
MUTED = "#9090a0"
ACCENT = "#4c9aff"
GOOD = "#4cd07d"
WARN = "#ffb454"
BAD = "#ff5f56"

CAM_SIZES = ("640x480", "1280x720", "960x540", "320x240")


class App(tk.Tk):
    def __init__(self, initial_host: str | None = None):
        super().__init__()
        self.title("BTBridge — PC client  v%s (protocol v1)" % __version__)
        self.geometry("1140x760")
        self.minsize(980, 700)
        self.configure(bg=BG)

        self._ui_q: "queue.Queue[tuple]" = queue.Queue()
        self.devices: dict[str, discovery.Device] = {}
        self.client = PhoneClient(on_audio=self._on_audio, on_jpeg=self._on_jpeg,
                                  on_event=self._on_client_event)
        self.decoder = CameraDecoder()
        if not self.decoder.start():
            self._log("Pillow unavailable: %s" % (self.decoder.error or "camera disabled"))
        self.sink = None
        self.mdns: discovery.MdnsDiscovery | None = None
        self.adb_path = discovery.find_adb()

        self._connected_device: discovery.Device | None = None
        self._last_device: discovery.Device | None = None
        self._forwarded_serial: str | None = None
        self._connecting = False
        self._disconnecting = False
        self._auto_reconnect = tk.BooleanVar(value=False)
        self._reconnect_job = None
        self._reconnect_tries = 0

        self._photo = None
        self._shown_seq = 0
        self._render_size = (0, 0)
        self._cam_placeholder = True

        self._level = 0.0
        self._rate_t0 = time.time()
        self._rate_audio_bytes = 0
        self._rate_jpegs = 0
        self._sps = 0.0
        self._vfps = 0.0
        self._last_ping = 0.0
        self._cfg_job = None
        self._n_playback = 0
        self._panels: list[tk.Frame] = []
        self._tree_rows = 5

        self._build_style()
        self._build_ui()
        self._startup(initial_host)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._pump)
        self.after(200, self._tick)

    # ------------------------------------------------------------------ style
    def _build_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Treeview", background=PANEL_2, fieldbackground=PANEL_2,
                     foreground=TEXT, borderwidth=1, rowheight=24)
        st.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                     relief="flat")
        st.map("Treeview", background=[("selected", ACCENT)],
               foreground=[("selected", "#0d0d10")])
        st.map("Treeview.Heading", background=[("active", PANEL)])
        st.configure("Vertical.TScrollbar", background=PANEL_2, troughcolor=BG,
                     arrowcolor=MUTED, borderwidth=0)
        st.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2,
                     foreground=TEXT, arrowcolor=TEXT, padding=(6, 3))
        st.configure("TSpinbox", fieldbackground=PANEL_2, background=PANEL_2,
                     foreground=TEXT, arrowcolor=TEXT, padding=(4, 3))

    def _btn(self, parent, text, command, kind="normal", width=None):
        colors = {"normal": (PANEL_2, TEXT), "accent": (ACCENT, "#0d0d10"),
                  "danger": ("#3a2124", BAD)}[kind]
        b = tk.Button(parent, text=text, command=command, bg=colors[0], fg=colors[1],
                      activebackground=colors[0], activeforeground=colors[1],
                      relief="flat", bd=0, highlightthickness=0, padx=12, pady=6,
                      cursor="hand2", font=("Segoe UI", 9))
        if width:
            b.configure(width=width)
        return b

    def _check(self, parent, text, var, command=None):
        return tk.Checkbutton(parent, text=text, variable=var, command=command,
                              bg=PANEL, fg=TEXT, selectcolor="#15151a",
                              activebackground=PANEL, activeforeground=TEXT,
                              highlightthickness=0, bd=0, anchor="w",
                              font=("Segoe UI", 9))

    # --------------------------------------------------------------------- UI
    def _build_ui(self):
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True, padx=12, pady=10)

        # header ------------------------------------------------------------
        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text="BTBridge", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 16)).pack(side="left")
        tk.Label(head, text="  phone mic → VB-Cable   ·   camera preview",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(4, 0))
        self.lbl_conn = tk.Label(head, text="disconnected", bg=BG, fg=MUTED,
                                 font=("Segoe UI Semibold", 10))
        self.lbl_conn.pack(side="right")

        # log is packed before the body so it reserves its height: the body
        # then takes exactly the rest (otherwise the left column gets clipped
        # at small window heights).
        self._build_log(root)

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=BG, width=384)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)
        self.left = left

        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        self._build_devices(left)
        self._build_streams(left)
        self._build_audio(left)
        self._build_camera(right)
        self.bind("<Configure>", self._on_root_configure)
        left.bind("<Configure>", lambda _e: self.after_idle(self._fit_tree))
        self.after(150, self._fit_tree)

    def _panel(self, parent, title):
        outer = tk.Frame(parent, bg=PANEL, bd=0, highlightthickness=1,
                         highlightbackground="#31313d")
        outer.pack(fill="x", pady=(0, 8))
        self._panels.append(outer)
        tk.Label(outer, text=title, bg=PANEL, fg=MUTED,
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=10, pady=(7, 2))
        inner = tk.Frame(outer, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        return inner

    # -- adaptive left column ------------------------------------------------
    def _fit_tree(self):
        """Give the device tree exactly the rows the window can afford.

        Without this the fixed-height Treeview pushes the mic panel off the
        bottom of the window at smaller heights.
        """
        try:
            if not self.tree.winfo_exists():
                return
            total = sum(p.winfo_reqheight() for p in self._panels)
            other = total - self.tree.winfo_reqheight() + 8 * len(self._panels)
            avail = self.left.winfo_height()
            rows = int((avail - other - 4) // 25) + 1
            rows = max(3, min(12, rows))
            if rows != self._tree_rows:
                self._tree_rows = rows
                self.tree.configure(height=rows)
        except Exception:  # noqa: BLE001
            pass

    def _on_root_configure(self, event):
        if event.widget is self:
            self.after_idle(self._fit_tree)

    # -- devices ------------------------------------------------------------
    def _build_devices(self, parent):
        inner = self._panel(parent, "PHONES")
        cols = ("name", "via", "addr")
        wrap = tk.Frame(inner, bg=PANEL)
        wrap.pack(fill="x")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=5,
                                 selectmode="browse")
        for c, label, w, stretch in (("name", "Device", 170, True),
                                     ("via", "Found via", 70, False),
                                     ("addr", "Address", 118, False)):
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w", stretch=stretch)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-Button-1>", lambda _e: self._on_connect())
        self.tree.tag_configure("adb", foreground="#9fd4ff")
        self.tree.tag_configure("mdns", foreground=TEXT)

        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x", pady=(8, 0))
        self.btn_refresh = self._btn(row, "Refresh", self._refresh_all)
        self.btn_refresh.pack(side="left")
        self.btn_connect = self._btn(row, "Connect", self._on_connect, kind="accent")
        self.btn_connect.pack(side="left", padx=6)
        self.btn_disconnect = self._btn(row, "Disconnect", self._on_disconnect)
        self.btn_disconnect.pack(side="left")
        self.btn_disconnect.configure(state="disabled")

        row2 = tk.Frame(inner, bg=PANEL)
        row2.pack(fill="x", pady=(8, 0))
        tk.Label(row2, text="Manual", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.ent_host = tk.Entry(row2, bg=PANEL_2, fg=TEXT, insertbackground=TEXT,
                                 relief="flat", bd=0, highlightthickness=1,
                                 highlightbackground="#3a3a46",
                                 highlightcolor=ACCENT, font=("Consolas", 9))
        self.ent_host.pack(side="left", fill="x", expand=True, padx=6, ipady=4)
        self.ent_host.insert(0, "192.168.0.0:%d" % DEFAULT_PORT)
        self.ent_host.bind("<Return>", lambda _e: self._add_manual())
        self._btn(row2, "Add", self._add_manual).pack(side="left")

        self.lbl_discovery = tk.Label(inner, text="", bg=PANEL, fg=MUTED,
                                      font=("Segoe UI", 8), anchor="w",
                                      justify="left", wraplength=350)
        self.lbl_discovery.pack(fill="x", pady=(6, 0))

    # -- streams ------------------------------------------------------------
    def _build_streams(self, parent):
        inner = self._panel(parent, "STREAMS  (sent to the phone as cfg frames)")
        self.var_mic = tk.BooleanVar(value=True)
        self.var_cam = tk.BooleanVar(value=True)
        self.var_size = tk.StringVar(value="640x480")
        self.var_fps = tk.StringVar(value="10")

        self._check(inner, "Mic  →  VB-Cable", self.var_mic,
                    self._send_cfg).pack(anchor="w")
        self._check(inner, "Camera  →  preview", self.var_cam,
                    self._send_cfg).pack(anchor="w")

        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text="Size", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.cmb_size = ttk.Combobox(row, values=CAM_SIZES, textvariable=self.var_size,
                                     width=11, state="readonly")
        self.cmb_size.pack(side="left", padx=(6, 14))
        self.cmb_size.bind("<<ComboboxSelected>>", lambda _e: self._send_cfg())
        tk.Label(row, text="fps", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.spn_fps = ttk.Spinbox(row, from_=1, to=30, width=5,
                                   textvariable=self.var_fps, command=self._send_cfg)
        self.spn_fps.pack(side="left", padx=6)
        self.spn_fps.bind("<Return>", lambda _e: self._send_cfg())

        row2 = tk.Frame(inner, bg=PANEL)
        row2.pack(fill="x", pady=(8, 0))
        self._btn(row2, "Apply cfg", self._send_cfg).pack(side="left")
        self._btn(row2, "Stop streams", self._on_stop_streams).pack(side="left", padx=6)
        self._check(row2, "auto-reconnect", self._auto_reconnect).pack(side="left", padx=(8, 0))

    # -- audio --------------------------------------------------------------
    def _build_audio(self, parent):
        inner = self._panel(parent, "MIC OUTPUT")
        self.lbl_audio = tk.Label(inner, text="not connected", bg=PANEL, fg=MUTED,
                                  font=("Segoe UI", 9), anchor="w", justify="left",
                                  wraplength=346)
        self.lbl_audio.pack(fill="x")

        self.level_canvas = tk.Canvas(inner, height=26, bg=PANEL, bd=0,
                                      highlightthickness=0)
        self.level_canvas.pack(fill="x", pady=(6, 0))
        self.level_canvas.bind("<Configure>", lambda _e: self._draw_level())

        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x", pady=(6, 0))
        self._btn(row, "Rescan audio", self._rescan_audio).pack(side="left")
        self.lbl_level = tk.Label(row, text="−∞ dB", bg=PANEL, fg=MUTED,
                                  font=("Consolas", 9))
        self.lbl_level.pack(side="right")

    # -- camera -------------------------------------------------------------
    def _build_camera(self, parent):
        outer = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                         highlightbackground="#31313d")
        outer.pack(fill="both", expand=True)
        head = tk.Frame(outer, bg=PANEL)
        head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(head, text="CAMERA", bg=PANEL, fg=MUTED,
                 font=("Segoe UI Semibold", 9)).pack(side="left")
        self.lbl_video = tk.Label(head, text="no frames", bg=PANEL, fg=MUTED,
                                  font=("Segoe UI", 8))
        self.lbl_video.pack(side="right")

        self.canvas = tk.Canvas(outer, bg="#0f0f13", bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self._cam_text = self.canvas.create_text(
            10, 10, anchor="nw", text="Camera preview appears here",
            fill="#4a4a58", font=("Segoe UI", 12))
        self._cam_img = self.canvas.create_image(0, 0, anchor="nw")

        self.lbl_stats = tk.Label(parent, text="", bg=BG, fg=MUTED,
                                  font=("Consolas", 9), anchor="w")
        self.lbl_stats.pack(fill="x", pady=(6, 0))

    # -- log ----------------------------------------------------------------
    def _build_log(self, parent):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(side="bottom", fill="x", pady=(8, 0))
        self.log = tk.Text(wrap, height=4, bg="#111116", fg="#c3c3d0",
                           insertbackground=TEXT, relief="flat", bd=0,
                           font=("Consolas", 8), wrap="word",
                           highlightthickness=1, highlightbackground="#2a2a34")
        sb = tk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ------------------------------------------------------------------ utils
    def _log(self, msg: str):
        stamped = time.strftime("%H:%M:%S ") + msg
        if threading.current_thread() is not threading.main_thread():
            self._ui_q.put(("log", stamped))
            return
        self.log.configure(state="normal")
        self.log.insert("end", stamped + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 400:
            self.log.delete("1.0", "%d.0" % (lines - 400))
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_conn(self, text: str, colour: str):
        self.lbl_conn.configure(text=text, fg=colour)
        connected = self.client.is_connected
        self.btn_connect.configure(state="disabled" if (connected or self._connecting)
                                   else "normal")
        self.btn_disconnect.configure(state="normal" if (connected or self._connecting)
                                      else "disabled")

    # -------------------------------------------------------------- startup
    def _startup(self, initial_host: str | None):
        self._log("BTBridge %s — protocol v1" % __version__)
        self._log("adb: %s" % (self.adb_path or "not found (USB discovery off)"))
        if initial_host:
            self.ent_host.delete(0, "end")
            self.ent_host.insert(0, initial_host)
        self._open_sink(quiet=True)
        self._start_mdns()
        threading.Thread(target=self._scan_adb, name="adb-scan", daemon=True).start()
        if initial_host:
            host, port = self._parse_hostport(initial_host)
            dev = discovery.Device(key="manual:%s" % initial_host, name=initial_host,
                                   source="manual", host=host, port=port,
                                   detail="%s:%d" % (host, port))
            self._add_device(dev)

    def _start_mdns(self):
        self.mdns = discovery.MdnsDiscovery(
            on_add=lambda dev: self._ui_q.put(("device", dev)),
            on_remove=lambda key: self._ui_q.put(("device_gone", key)),
            on_log=lambda m: self._ui_q.put(("log", m)))
        if self.mdns.start():
            self.lbl_discovery.configure(text="mDNS browsing _btbridge._tcp. …")
        else:
            self.lbl_discovery.configure(
                text="mDNS unavailable: %s — use adb or Manual" % (self.mdns.error or "?"))

    def _scan_adb(self):
        devs, msg = discovery.adb_devices(self.adb_path)
        self._ui_q.put(("adb", (devs, msg)))

    def _refresh_all(self):
        self._log("refresh: mDNS (continuous) + adb devices -l")
        self.lbl_discovery.configure(text="scanning adb …")
        threading.Thread(target=self._scan_adb, name="adb-scan", daemon=True).start()

    # -- device list --------------------------------------------------------
    def _add_device(self, dev: discovery.Device):
        self.devices[dev.key] = dev
        tag = "adb" if dev.source == "adb" else ("mdns" if dev.source == "mDNS" else "")
        values = (dev.name, dev.source, "%s:%d" % (dev.host, dev.port))
        if self.tree.exists(dev.key):
            self.tree.item(dev.key, values=values, tags=(tag,))
        else:
            self.tree.insert("", "end", iid=dev.key, values=values, tags=(tag,))
            self.tree.selection_set(dev.key)
        self.lbl_discovery.configure(
            text="%d phone(s) listed · mDNS %s · adb %s"
            % (len(self.devices), "on" if self.mdns else "off",
               os.path.basename(self.adb_path) if self.adb_path else "off"))

    def _remove_device(self, key: str):
        self.devices.pop(key, None)
        if self.tree.exists(key):
            self.tree.delete(key)

    def _parse_hostport(self, text: str) -> tuple[str, int]:
        text = text.strip()
        if text.startswith("["):                      # [ipv6]:port
            host, _, rest = text[1].partition("]")
            port = rest.lstrip(":")
            return host, int(port) if port.isdigit() else DEFAULT_PORT
        if text.count(":") == 1:
            host, _, port = text.partition(":")
            if port.isdigit():
                return host.strip(), int(port)
        return text, DEFAULT_PORT

    def _selected_device(self) -> discovery.Device | None:
        sel = self.tree.selection()
        if sel and sel[0] in self.devices:
            return self.devices[sel[0]]
        text = self.ent_host.get().strip()
        if text:
            host, port = self._parse_hostport(text)
            if host and host != "192.168.0.0":
                return discovery.Device(key="manual:%s" % text, name=text,
                                        source="manual", host=host, port=port,
                                        detail="%s:%d" % (host, port))
        return None

    def _add_manual(self):
        host, port = self._parse_hostport(self.ent_host.get())
        if not host:
            return
        dev = discovery.Device(key="manual:%s:%d" % (host, port),
                              name="%s:%d" % (host, port), source="manual",
                              host=host, port=port, detail="%s:%d" % (host, port))
        self._add_device(dev)
        self._log("manual entry added: %s:%d" % (host, port))

    # -------------------------------------------------------------- connect
    def _current_cfg(self) -> dict:
        """Snapshot the stream settings — call on the UI thread only."""
        return {"mic": bool(self.var_mic.get()), "cam": bool(self.var_cam.get()),
                "camW": self._cam_w(), "camH": self._cam_h(), "fps": self._fps()}

    def _on_connect(self):
        if self._connecting or self.client.is_connected:
            return
        dev = self._selected_device()
        if dev is None:
            self._log("pick a phone first (or type host:port and press Add)")
            return
        self._last_device = dev
        self._reconnect_tries = 0
        self._connecting = True
        self._set_conn("connecting to %s …" % dev.name, WARN)
        self.btn_connect.configure(state="disabled")
        self.btn_disconnect.configure(state="normal")
        self._log("connecting to %s (%s) …" % (dev.name, dev.detail or dev.source))
        threading.Thread(target=self._do_connect, args=(dev, self._current_cfg()),
                         name="connect", daemon=True).start()

    def _do_connect(self, dev: discovery.Device, cfg: dict):
        """Worker thread: adb forward (if needed) + dial + open audio device.

        ``cfg`` is snapshotted on the UI thread — Tk variables must never be
        read here (that raises "main thread is not in main loop").
        """
        try:
            if dev.source == "adb" and dev.serial:
                ok, msg = discovery.adb_forward(self.adb_path, dev.serial)
                self._ui_q.put(("log", msg))
                if ok:
                    self._forwarded_serial = dev.serial
                    dev = discovery.Device(**{**dev.__dict__,
                                              "host": "127.0.0.1"})
                else:
                    self._ui_q.put(("log", "adb forward failed, trying direct dial"))
            self._open_sink(quiet=True, on_ui_thread=False)
            if not self.client.connect(dev.host, dev.port, via=dev.source,
                                       serial=dev.serial):
                self._ui_q.put(("connect_failed", self.client.last_error))
                return
            self._connected_device = dev
            self._ui_q.put(("connected", dev))
            # always tell the phone what we want right after hello
            self.client.send_cfg(mic=cfg["mic"], cam=cfg["cam"],
                                 cam_w=cfg["camW"], cam_h=cfg["camH"],
                                 fps=cfg["fps"])
        except Exception:  # noqa: BLE001
            self._ui_q.put(("log", "connect crashed:\n" + traceback.format_exc()))
            self._ui_q.put(("connect_failed", "internal error"))

    def _drop_forward(self):
        """Remove the adb forward off the UI thread (subprocess can take a second)."""
        serial, self._forwarded_serial = self._forwarded_serial, None
        if not serial or not self.adb_path:
            return

        def work():
            discovery.adb_remove_forward(self.adb_path, serial)
            self._ui_q.put(("log", "adb forward removed (%s)" % serial))

        threading.Thread(target=work, name="adb-unforward", daemon=True).start()

    def _on_disconnect(self, quiet: bool = False):
        self._disconnecting = True
        self._cancel_reconnect()
        if not quiet:
            self._log("disconnecting")
        self.client.close(notify=False)
        self._close_sink()
        self._connected_device = None
        self._connecting = False
        self._drop_forward()
        self._disconnecting = False
        self._set_conn("disconnected", MUTED)
        self._update_audio_label()
        self._show_placeholder("Disconnected")

    # ---------------------------------------------------------------- audio
    def _open_sink(self, quiet: bool = False, on_ui_thread: bool = True):
        """Open the cable sink. Safe to call from a worker: pass on_ui_thread=False."""
        self._close_sink()
        sink, tried = audio_win.open_cable_sink()
        self.sink = sink
        try:
            self._n_playback = len(audio_win.list_devices())
        except Exception:  # noqa: BLE001
            pass
        if sink.ok:
            if not quiet:
                self._log("audio: opened %r (#%d, %d ms x %d buffers)"
                          % (sink.name, sink.device_id, sink.buffer_ms, sink.nbuffers))
        else:
            self._log("audio: %s — level bar only, nothing is routed to apps"
                      % sink.error)
            for dev, err in tried:
                self._log("        refused [%d] %s: %s" % (dev.index, dev.name, err))
        if on_ui_thread:
            self._update_audio_label()
        else:
            self._ui_q.put(("audio", None))
        return sink

    def _close_sink(self):
        sink, self.sink = self.sink, None
        if sink is not None:
            try:
                sink.close()
            except Exception:  # noqa: BLE001
                pass

    def _rescan_audio(self):
        self._open_sink()
        self._log("audio devices: " + ", ".join(
            "#%d %r" % (d.index, d.name) for d in audio_win.list_devices()))

    def _update_audio_label(self):
        sink = self.sink
        if sink is None:
            self.lbl_audio.configure(text="no audio device open", fg=MUTED)
        elif sink.ok:
            self.lbl_audio.configure(
                text="→ %s\nDiscord/Zoom input: “CABLE Output (VB-Audio Virtual Cable)”"
                % sink.name, fg=GOOD)
        else:
            self.lbl_audio.configure(
                text="⚠ %s\nLevel bar works, but audio is NOT routed to apps. "
                     "Install VB-Audio Virtual Cable, then press Rescan audio."
                % sink.error, fg=BAD)
        self._draw_level()

    def _on_audio(self, payload: bytes):
        """Reader thread: straight into the sink (thread-safe), never blocking."""
        sink = self.sink
        if sink is not None:
            try:
                sink.write(payload)
            except Exception:  # noqa: BLE001
                pass

    def _draw_level(self):
        c = self.level_canvas
        c.delete("all")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        bar_h = h - 12
        c.create_rectangle(0, 0, w, bar_h, fill="#15151a", outline="")
        lvl = max(0.0, min(1.0, self._level))
        if lvl > 0.001:
            fill = GOOD if lvl < 0.75 else (WARN if lvl < 0.95 else BAD)
            c.create_rectangle(0, 0, int(w * lvl), bar_h, fill=fill, outline="")
        # -60..0 dB scale ticks
        for db in (-60, -40, -20, -6, 0):
            x = int(w * (1 - 10 ** (db / 20.0)))
            c.create_line(x, bar_h, x, h - 2, fill="#3a3a46")
        if self._level > 0.0005:
            db = 20 * math.log10(self._level)
            self.lbl_level.configure(text="%5.1f dB" % db)
        else:
            self.lbl_level.configure(text="  −∞ dB")

    # --------------------------------------------------------------- camera
    def _on_jpeg(self, jpeg: bytes):
        self.decoder.push(jpeg)

    def _show_placeholder(self, text: str):
        self.canvas.itemconfigure(self._cam_img, image="")
        self._photo = None
        self._cam_placeholder = True
        self.canvas.itemconfigure(self._cam_text, text=text, state="normal")
        self._position_placeholder()

    def _position_placeholder(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        self.canvas.coords(self._cam_text, w // 2, h // 2)
        self.canvas.itemconfigure(self._cam_text, anchor="center")

    def _on_canvas_resize(self, _event=None):
        if self._cam_placeholder:
            self._position_placeholder()
        else:
            self._render_size = (0, 0)       # force rescale of the last frame

    def _tick_video(self):
        if ImageTk is None:
            return
        seq, img = self.decoder.latest()
        if img is None:
            return
        cw = max(2, self.canvas.winfo_width() - 4)
        ch = max(2, self.canvas.winfo_height() - 4)
        size = fit_rect(img.width, img.height, cw, ch)
        if not size[0]:
            return
        if seq == self._shown_seq and size == self._render_size:
            return
        try:
            scaled = (img.resize(size, _BILINEAR)
                      if size != img.size and _BILINEAR is not None else img)
            self._photo = ImageTk.PhotoImage(scaled)
        except Exception as exc:  # noqa: BLE001
            self.lbl_video.configure(text="render error: %s" % exc, fg=BAD)
            return
        self._shown_seq = seq
        self._render_size = size
        self._cam_placeholder = False
        self.canvas.itemconfigure(self._cam_text, state="hidden")
        self.canvas.itemconfigure(self._cam_img, image=self._photo)
        x = (self.canvas.winfo_width() - size[0]) // 2
        y = (self.canvas.winfo_height() - size[1]) // 2
        self.canvas.coords(self._cam_img, max(0, x), max(0, y))

    # -------------------------------------------------------------- events
    def _on_client_event(self, kind: str, payload=None):
        """Called from the reader thread."""
        self._ui_q.put(("client_" + kind, payload))

    def _send_cfg(self):
        if not self.client.is_connected:
            return
        ok = self.client.send_cfg(mic=self.var_mic.get(), cam=self.var_cam.get(),
                                  cam_w=self._cam_w(), cam_h=self._cam_h(),
                                  fps=self._fps())
        if not ok:
            self._log("cfg not sent: link is down")

    def _cam_w(self) -> int:
        try:
            return int(str(self.var_size.get()).split("x")[0])
        except Exception:  # noqa: BLE001
            return 640

    def _cam_h(self) -> int:
        try:
            return int(str(self.var_size.get()).split("x")[1])
        except Exception:  # noqa: BLE001
            return 480

    def _fps(self) -> float:
        try:
            return float(self.var_fps.get())
        except Exception:  # noqa: BLE001
            return 10.0

    def _on_stop_streams(self):
        if self.client.is_connected:
            self.client.send_stop()
            self.var_mic.set(False)
            self.var_cam.set(False)
            self._log("stop sent (socket stays open)")

    # --------------------------------------------------------------- pump
    def _pump(self):
        try:
            for _ in range(200):
                try:
                    kind, payload = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                self._handle_ui_event(kind, payload)
        except Exception:  # noqa: BLE001
            self._log("ui error:\n" + traceback.format_exc())
        self.after(60, self._pump)

    def _handle_ui_event(self, kind: str, payload):
        if kind == "log":
            self._log(payload)
        elif kind == "audio":
            self._update_audio_label()
        elif kind == "device":
            self._add_device(payload)
        elif kind == "device_gone":
            self._remove_device(payload)
        elif kind == "adb":
            devs, msg = payload
            for dev in devs:
                self._add_device(dev)
            self._log(msg)
            self.lbl_discovery.configure(text=msg)
        elif kind == "connected":
            self._connecting = False
            self.btn_connect.configure(state="disabled")
            self.btn_disconnect.configure(state="normal")
            self._set_conn("connected · %s" % payload.name, GOOD)
            self._log("connected to %s via %s" % (payload.name, payload.source))
            if self._cam_placeholder:
                self._show_placeholder("Waiting for camera frames…")
        elif kind == "connect_failed":
            self._connecting = False
            self._set_conn("connect failed", BAD)
            self._log("connect failed: %s" % payload)
            self._close_sink()
            self._update_audio_label()
            self._maybe_reconnect()
        elif kind == "client_hello":
            self._log("hello: %s" % payload)
            self._send_cfg()
            self._set_conn("connected · %s" % (payload.get("name") or "phone"), GOOD)
            if payload.get("mic") is False:
                self._log("phone reports mic=False (it may have no mic permission)")
        elif kind == "client_stat":
            self._update_stats_label(payload)
        elif kind == "client_pong":
            pass
        elif kind == "client_unknown_json":
            pass
        elif kind == "client_error":
            self._log("phone error: %s" % payload)
            self.lbl_video.configure(text=str(payload), fg=BAD)
        elif kind == "client_closed":
            was = self._connected_device
            self._connected_device = None
            self._close_sink()
            msg = str(payload)
            short = msg if len(msg) <= 46 else msg[:43] + "…"
            self._set_conn("disconnected · %s" % short,
                           WARN if "stall" in msg else MUTED)
            self._update_audio_label()
            self._show_placeholder("Connection closed")
            self._log("closed: %s" % msg)
            if was is not None and not self._disconnecting:
                self._maybe_reconnect()

    def _maybe_reconnect(self):
        if not self._auto_reconnect.get() or self._last_device is None:
            return
        if self._reconnect_tries >= 12:
            self._log("auto-reconnect: giving up after 12 attempts")
            return
        self._reconnect_tries += 1
        delay = min(8000, 1500 * self._reconnect_tries)
        self._log("auto-reconnect in %.1fs (attempt %d)"
                  % (delay / 1000.0, self._reconnect_tries))
        self._cancel_reconnect()
        self._reconnect_job = self.after(delay, self._reconnect_now)

    def _reconnect_now(self):
        self._reconnect_job = None
        if self.client.is_connected or self._connecting:
            return
        dev = self._last_device
        if dev is None:
            return
        self._connecting = True
        self._set_conn("reconnecting to %s …" % dev.name, WARN)
        threading.Thread(target=self._do_connect, args=(dev, self._current_cfg()),
                         name="reconnect", daemon=True).start()

    def _cancel_reconnect(self):
        if self._reconnect_job is not None:
            try:
                self.after_cancel(self._reconnect_job)
            except Exception:  # noqa: BLE001
                pass
            self._reconnect_job = None

    def _update_stats_label(self, stat: dict):
        self.lbl_video.configure(
            text="phone: mic=%s cam=%s fps=%s %sx%s"
            % (stat.get("mic"), stat.get("cam"), stat.get("fps"),
               stat.get("camW"), stat.get("camH")), fg=MUTED)

    # --------------------------------------------------------------- ticks
    def _tick(self):
        try:
            self._tick_video()

            # level: peak since last tick, decayed for readability
            sink = self.sink
            peak = 0.0
            if sink is not None:
                try:
                    peak = sink.read_peak()
                except Exception:  # noqa: BLE001
                    peak = 0.0
            self._level = max(peak, self._level * 0.78)
            if self._level < 0.0015:
                self._level = 0.0
            self._draw_level()

            now = time.time()
            if now - self._rate_t0 >= 1.0:
                dt = now - self._rate_t0
                dw = (self.client.audio_bytes - self._rate_audio_bytes)
                dj = (self.client.jpeg_frames - self._rate_jpegs)
                self._sps = dw / 2.0 / dt
                self._vfps = dj / dt
                self._rate_audio_bytes = self.client.audio_bytes
                self._rate_jpegs = self.client.jpeg_frames
                self._rate_t0 = now
                self._update_stats()

            if self.client.is_connected:
                if now - self._last_ping > 5.0:
                    self._last_ping = now
                    self.client.send_ping()
                if self._level == 0.0 and self.var_mic.get() and \
                        now - self.client.last_rx > 3.0:
                    self.lbl_video.configure(text="waiting for frames…", fg=WARN)
        except Exception:  # noqa: BLE001
            self._log("tick error:\n" + traceback.format_exc())
        self.after(60, self._tick)

    def _update_stats(self):
        c = self.client
        cam = self.decoder.stats()
        if c.is_connected:
            txt = ("audio %6.1f kS/s  ·  cam %4.1f fps  ·  pcm %d frames  ·  "
                   "jpeg %d (%d dropped, %sms decode)  ·  rx %.1f kB/s"
                   % (self._sps / 1000.0, self._vfps, c.audio_frames,
                      cam["decoded"], cam["dropped"], cam["last_ms"],
                      (c.audio_bytes + c.jpeg_bytes) / 1024.0 /
                      max(0.001, time.time() - (c.connected_at or time.time()) + 0.001)))
        else:
            txt = ("idle  ·  %d playback device(s)  ·  cable: %s"
                   % (self._n_playback,
                      (self.sink.name if self.sink and self.sink.ok else "not found")))
        self.lbl_stats.configure(text=txt)

    # --------------------------------------------------------------- close
    def _on_close(self):
        self._log("shutting down")
        self._cancel_reconnect()
        try:
            if self.client.is_connected:
                self.client.send_stop()
        except Exception:  # noqa: BLE001
            pass
        self.client.close(notify=False)
        self._close_sink()
        if self.mdns is not None:
            self.mdns.stop()
        # synchronously at shutdown: the process is about to exit, a daemon
        # thread would be killed before adb finishes
        if self._forwarded_serial and self.adb_path:
            discovery.adb_remove_forward(self.adb_path, self._forwarded_serial)
            self._forwarded_serial = None
        self.decoder.stop()
        try:
            self.destroy()
        except Exception:  # noqa: BLE001
            pass


def run_gui(initial_host: str | None = None) -> int:
    app = App(initial_host=initial_host)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app._on_close()
    return 0
