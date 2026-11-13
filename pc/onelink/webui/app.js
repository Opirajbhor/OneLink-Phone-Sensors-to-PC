/* OneLink web UI — talks to the Python bridge through pywebview.
   When opened in a plain browser it falls back to a mock bridge so the
   layout can be designed without a phone. */

const POLL_MS = 120;

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------ theme */
const THEME_KEY = "onelink.theme";
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem(THEME_KEY, theme); } catch (_) {}
}
(function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch (_) {}
  const wantsDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (wantsDark ? "dark" : "light"));
})();
$("themeBtn").addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  applyTheme(next);
});

/* ------------------------------------------------------------ bridge */
function hasBridge() {
  return !!(window.pywebview && window.pywebview.api && window.pywebview.api.state);
}

async function call(method, ...args) {
  if (hasBridge()) return await window.pywebview.api[method](...args);
  return MOCK[method] ? MOCK[method](...args) : null;
}

/* A tiny fake phone so the UI can be previewed in a plain browser. */
const MOCK = (() => {
  let t = 0;
  const devices = [
    { key: "m1", name: "Redmi 7", source: "mDNS", addr: "192.168.0.101:47110", detail: "Wi-Fi" },
    { key: "m2", name: "Pixel 8a", source: "adb", addr: "127.0.0.1:47110", detail: "USB" },
  ];
  let sel = "m1", connected = false, mic = true, cam = true;
  return {
    async state() {
      t += POLL_MS;
      const level = connected && mic ? 0.18 + 0.16 * Math.abs(Math.sin(t / 260)) : 0;
      return {
        version: "mock",
        conn: connected
          ? { state: "connected", name: "Redmi 7", detail: "Wi-Fi" }
          : { state: "idle", name: "", detail: "" },
        devices, sel,
        mic: { enabled: mic, level, db: level > 0 ? 20 * Math.log10(level) : null,
               sink: "CABLE Input", sink_ok: true },
        cam: { enabled: cam, seq: connected && cam ? Math.floor(t / 500) : 0,
               frame: null, size: "640x480", fps: 10, facing: "back" },
        spk: { enabled: false, level: 0, ok: false, device: "", error: null },
        stats: { running: connected, rx_kbs: connected ? 96.4 : 0, vfps: connected && cam ? 9.8 : 0,
                 afps: connected && mic ? 50 : 0 },
        log: ["mock bridge — no Python behind this window"],
      };
    },
    async refresh() {},
    async add_manual() {},
    async select(k) { sel = k; },
    async connect() { connected = true; },
    async disconnect() { connected = false; },
    async set_cfg(m, c, s, f) { mic = m; cam = c; },
    set_theme() {},
  };
})();

/* ------------------------------------------------------------ render */
let selectedKey = null;
let lastFrameSeq = -1;
let lastLogLen = -1;
let lastDevSig = "";
let rotation = 0;            /* 0 | 90 | 180 | 270 — applied client-side */
let facing = "back";
let lastFrameImg = null;

function setPill(kind, text) {
  const pill = $("statusPill");
  pill.className = "pill " + kind;
  $("statusText").textContent = text;
}

function initials(name) {
  const clean = String(name || "?").trim();
  const parts = clean.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return clean.slice(0, 2).toUpperCase();
}

function renderDevices(state) {
  const list = $("devList");
  const empty = $("empty");
  const devs = state.devices || [];
  $("devCount").textContent = String(devs.length);

  const sig = JSON.stringify(devs.map((d) => [d.key, d.name, d.source, d.addr]));
  if (sig !== lastDevSig) {
    lastDevSig = sig;
    list.textContent = "";
    for (const d of devs) {
      const li = document.createElement("li");
      li.className = "dev";
      li.dataset.key = d.key;
      li.setAttribute("role", "option");
      li.innerHTML =
        '<div class="avatar"></div>' +
        '<div class="dev-main"><div class="dev-name"></div><div class="dev-sub"></div></div>' +
        '<span class="badge"></span>';
      li.querySelector(".avatar").textContent = initials(d.name);
      li.querySelector(".dev-name").textContent = d.name;
      li.querySelector(".dev-sub").textContent = d.addr + (d.detail ? " · " + d.detail : "");
      const badge = li.querySelector(".badge");
      badge.textContent = d.source;
      if (d.source === "manual") badge.classList.add("manual");
      li.addEventListener("click", () => {
        selectDevice(d.key);
        call("select", d.key);
      });
      li.addEventListener("dblclick", () => connectTo(d.key));
      list.appendChild(li);
    }
  }

  empty.classList.toggle("hidden", devs.length > 0);
  const tips = document.querySelector(".tips");
  if (tips) tips.classList.toggle("hidden", devs.length > 0);

  const sel = state.sel || selectedKey;
  if (sel !== selectedKey || sig !== lastDevSig) selectedKey = sel;
  for (const li of list.children) {
    li.setAttribute("aria-selected", String(li.dataset.key === selectedKey));
  }

  const connected = state.conn && state.conn.state === "connected";
  const busy = state.conn && state.conn.state === "connecting";
  $("connectBtn").disabled = connected || busy || !selectedKey;
  $("disconnectBtn").disabled = !(connected || busy);
}

function selectDevice(key) {
  selectedKey = key;
  for (const li of $("devList").children) {
    li.setAttribute("aria-selected", String(li.dataset.key === key));
  }
  $("connectBtn").disabled = false;
}

function renderStatus(state) {
  const conn = state.conn || {};
  if (conn.state === "connected") {
    setPill("pill-live", conn.name ? "Connected · " + conn.name : "Connected");
  } else if (conn.state === "connecting") {
    setPill("pill-busy", "Connecting…");
  } else if (conn.state === "error") {
    setPill("pill-bad", conn.detail || "Connection lost");
  } else {
    setPill("pill-idle", "Not connected");
  }
}

function renderMic(state) {
  const mic = state.mic || {};
  const lvl = Math.max(0, Math.min(1, mic.level || 0));
  const bar = $("micBar");
  bar.style.width = (lvl * 100).toFixed(1) + "%";
  bar.style.background = lvl > 0.9
    ? "linear-gradient(90deg,var(--warn),var(--bad))"
    : "linear-gradient(90deg,var(--good),#37c98a)";
  $("micDb").textContent = mic.db == null || !mic.enabled ? "−∞ dB" : mic.db.toFixed(1) + " dB";

  const route = $("micRoute");
  route.classList.remove("ok", "bad");
  if (!mic.sink_ok) {
    route.textContent = "VB-Cable not found";
    route.classList.add("bad");
  } else if (mic.enabled && state.conn && state.conn.state === "connected") {
    route.textContent = "→ CABLE Output";
    route.classList.add("ok");
  } else {
    route.textContent = mic.sink ? mic.sink : "idle";
  }
  $("micToggle").checked = !!mic.enabled;
}

function drawFrame(img) {
  const canvas = $("camCanvas");
  const rect = canvas.getBoundingClientRect();
  const W = Math.max(2, Math.round(rect.width));
  const H = Math.max(2, Math.round(rect.height));
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(W / 2, H / 2);
  ctx.rotate((rotation * Math.PI) / 180);
  const swap = rotation % 180 !== 0;
  const boxW = swap ? H : W;
  const boxH = swap ? W : H;
  const scale = Math.min(boxW / img.width, boxH / img.height);
  const w = img.width * scale;
  const h = img.height * scale;
  ctx.drawImage(img, -w / 2, -h / 2, w, h);
  ctx.restore();
}

function renderCam(state) {
  const cam = state.cam || {};
  $("camToggle").checked = !!cam.enabled;
  const card = document.querySelector(".camera-card");
  if (card) card.classList.toggle("off", !cam.enabled);
  const stack = document.querySelector(".stack");
  if (stack) stack.classList.toggle("camoff", !cam.enabled);
  if (cam.size && document.activeElement !== $("sizeSel")) $("sizeSel").value = cam.size;
  if (cam.fps && document.activeElement !== $("fpsInput")) $("fpsInput").value = cam.fps;
  if (cam.facing && cam.facing !== facing) {
    facing = cam.facing;
    setFacingUI(facing);
  }

  const canvas = $("camCanvas");
  const holder = $("camPlaceholder");
  if (cam.frame && cam.seq !== lastFrameSeq) {
    lastFrameSeq = cam.seq;
    const img = new Image();
    img.onload = () => {
      lastFrameImg = img;
      drawFrame(img);
      canvas.classList.add("on");
      holder.style.display = "none";
    };
    img.src = cam.frame;
  } else if (!cam.seq) {
    canvas.classList.remove("on");
    holder.style.display = "";
    lastFrameSeq = -1;
    lastFrameImg = null;
  }
  $("camMeta").textContent = cam.seq
    ? (cam.size || "") + " · " + (state.stats && state.stats.vfps ? state.stats.vfps.toFixed(1) : "0") + " fps"
    : "no frames";
}

function setFacingUI(value) {
  for (const b of document.querySelectorAll("#facingSeg button")) {
    b.classList.toggle("on", b.dataset.facing === value);
  }
}

function setRotation(next) {
  rotation = ((next % 360) + 360) % 360;
  $("rotLabel").textContent = rotation + "°";
  const r2 = $("rotLabel2");
  if (r2) r2.textContent = rotation + "°";
  if (lastFrameImg) drawFrame(lastFrameImg);
}

/* Settings page: read-only mirrors of the live state. */
function renderSettings(state) {
  const onOff = (el, on) => {
    const node = $(el);
    if (!node) return;
    node.textContent = on ? "on" : "off";
    node.classList.toggle("on", !!on);
  };
  onOff("setMicState", (state.mic || {}).enabled);
  onOff("setCamState", (state.cam || {}).enabled);
  onOff("setSpkState", (state.spk || {}).enabled);
  onOff("setClipState", (state.clip || {}).enabled);
  const clip = state.clip || {};
  $("setClipCount").textContent = String((clip.items || []).length);
  const mic = state.mic || {};
  if (mic.sink) $("setSinkName").textContent = mic.sink;
  const sinkOk = $("setSinkState");
  sinkOk.textContent = mic.sink_ok ? "ready" : "missing";
  sinkOk.classList.toggle("on", !!mic.sink_ok);
  $("setVersion").textContent = "version " + (state.version || "—");
  const note = $("camSizeNote");
  if (note) note.textContent = ((state.cam || {}).size || "640x480") + " · " +
    Math.round((state.cam || {}).fps || 0) + " fps";
}

function renderClip(state) {
  const clip = state.clip || {};
  $("clipToggle").checked = clip.enabled !== false;
  $("clipMeta").textContent = clip.enabled === false ? "sync off" : "sync on";
  const items = clip.items || [];
  const list = $("clipList");
  $("clipEmpty").style.display = items.length ? "none" : "";
  const sig = JSON.stringify(items.map((i) => i.hash));
  if (sig === renderClip.sig) return;
  renderClip.sig = sig;
  list.textContent = "";
  for (const item of items.slice(0, 12)) {
    const li = document.createElement("li");
    li.className = "clip-item";
    li.title = "Click to copy again";
    const t = document.createElement("span");
    t.className = "clip-text";
    t.textContent = (item.text || "").replace(/\s+/g, " ").slice(0, 120);
    const src = document.createElement("span");
    src.className = "clip-src" + (item.origin === "phone" ? " phone" : "");
    src.textContent = item.origin === "phone" ? "phone" : "pc";
    li.appendChild(t);
    li.appendChild(src);
    li.addEventListener("click", () => call("copy_to_clip", item.text || ""));
    list.appendChild(li);
  }
}
renderClip.sig = null;

function renderSpk(state) {
  const spk = state.spk || {};
  $("spkToggle").checked = !!spk.enabled;
  if (document.activeElement !== $("muteChk")) $("muteChk").checked = spk.mute_pc !== false;
  const lvl = Math.max(0, Math.min(1, (spk.level || 0) * 1.6));
  $("spkBar").style.width = (lvl * 100).toFixed(1) + "%";
  const route = $("spkRoute");
  route.classList.remove("ok", "bad");
  if (spk.enabled && spk.error) {
    route.textContent = spk.error;
    route.classList.add("bad");
  } else if (spk.enabled && spk.ok) {
    route.textContent = spk.switched ? "PC silent → phone" : "→ phone";
    route.classList.add("ok");
  } else {
    route.textContent = "PC sound → phone";
  }
}

function renderStats(state) {
  const s = state.stats || {};
  const parts = [];
  if (s.running) {
    const kb = s.rx_kbs || 0;
    parts.push(kb >= 1000 ? (kb / 1024).toFixed(1) + " MB/s" : Math.round(kb) + " kB/s");
    if (s.vfps) parts.push(s.vfps.toFixed(1) + " fps");
  } else {
    parts.push("idle");
  }
  $("streamStats").textContent = parts.join(" · ");
}

function renderLog(state) {
  const lines = state.log || [];
  if (lines.length === lastLogLen) return;
  lastLogLen = lines.length;
  const el = $("log");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 12;
  el.textContent = lines.slice(-300).join("\n");
  if (atBottom) el.scrollTop = el.scrollHeight;
}

async function tick() {
  try {
    const state = await call("state");
    if (!state) return;
    renderStatus(state);
    renderDevices(state);
    renderMic(state);
    renderCam(state);
    renderSpk(state);
    renderClip(state);
    renderSettings(state);
    renderStats(state);
    renderLog(state);
  } catch (err) {
    console.error(err);
  }
}

/* ------------------------------------------------------------ actions */
async function refresh() {
  $("refreshBtn").classList.add("spin");
  try { await call("refresh"); } finally {
    setTimeout(() => $("refreshBtn").classList.remove("spin"), 600);
  }
}

async function connectTo(key) {
  if (key) selectDevice(key);
  await call("connect", selectedKey || "");
}

$("refreshBtn").addEventListener("click", refresh);
$("connectBtn").addEventListener("click", () => connectTo(null));
$("disconnectBtn").addEventListener("click", () => call("disconnect"));

$("manualForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = $("manualInput").value.trim();
  if (!value) return;
  await call("add_manual", value);
  $("manualInput").value = "";
});

function pushCfg() {
  call("set_cfg", $("micToggle").checked, $("camToggle").checked,
       $("sizeSel").value, Number($("fpsInput").value) || 10,
       facing, $("spkToggle").checked, $("muteChk").checked,
       $("clipToggle").checked);
}
$("micToggle").addEventListener("change", pushCfg);
$("camToggle").addEventListener("change", pushCfg);
$("spkToggle").addEventListener("change", pushCfg);
$("muteChk").addEventListener("change", pushCfg);
$("clipToggle").addEventListener("change", pushCfg);
$("sizeSel").addEventListener("change", pushCfg);
$("fpsInput").addEventListener("change", pushCfg);

for (const b of document.querySelectorAll("#facingSeg button")) {
  b.addEventListener("click", () => {
    facing = b.dataset.facing;
    setFacingUI(facing);
    pushCfg();
  });
}
$("rotateBtn").addEventListener("click", () => setRotation(rotation + 90));
$("rotateBtn2").addEventListener("click", () => setRotation(rotation + 90));
$("clearClipBtn").addEventListener("click", async () => {
  await call("clear_clip_history");
  renderClip.sig = null;
});
$("openSoundBtn").addEventListener("click", () => call("open_sound_settings"));

/* pages: Connect / Settings, LocalSend-style tabs */
function showPage(name) {
  for (const b of document.querySelectorAll(".tab")) {
    b.classList.toggle("active", b.dataset.page === name);
  }
  $("pageConnect").classList.toggle("active", name === "connect");
  $("pageSettings").classList.toggle("active", name === "settings");
  if (lastFrameImg) setTimeout(() => drawFrame(lastFrameImg), 60);
}
for (const b of document.querySelectorAll(".tab")) {
  b.addEventListener("click", () => showPage(b.dataset.page));
}

window.addEventListener("resize", () => {
  if (lastFrameImg) drawFrame(lastFrameImg);
});
setRotation(0);

$("logToggle").addEventListener("click", () => {
  const log = $("log");
  log.classList.toggle("hidden");
  $("logToggle").textContent = log.classList.contains("hidden") ? "show" : "hide";
});

/* ------------------------------------------------------------ boot */
window.addEventListener("pywebviewready", () => tick());
tick();
setInterval(tick, POLL_MS);

/* subtle affordance: the refresh icon spins for a moment */
const style = document.createElement("style");
style.textContent = ".spin svg{animation:spin .6s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}";
document.head.appendChild(style);
