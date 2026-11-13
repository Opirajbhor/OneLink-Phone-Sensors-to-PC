/* OneLink UI — talks to the Python bridge through pywebview (or the loopback
   HTTP API when opened in a browser). No framework, no bundler. */

const $ = (id) => document.getElementById(id);
const POLL_MS = 200;

const ICONS = {
  phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18h2"/></svg>',
  cable: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15V9a4 4 0 014-4h2"/><rect x="14" y="9" width="6" height="6" rx="1"/><path d="M10 15h4"/></svg>',
  router: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="8" rx="2"/><path d="M7 11V7M17 11V7"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="1"/><path d="M6 15H5a1 1 0 01-1-1V5a1 1 0 011-1h9a1 1 0 011 1v1"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 12.5l5 4.5L19 7"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 007 0l3-3a5 5 0 00-7-7l-1 1"/><path d="M14 11a5 5 0 00-7 0l-3 3a5 5 0 007 7l1-1"/></svg>',
  laptop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="6" width="14" height="9" rx="1"/><path d="M3 18h18"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
};

const TRANSPORT = { mdns: "Wi-Fi", adb: "USB", manual: "Manual" };
const transportOf = (source) => TRANSPORT[(source || "").toLowerCase()] || (source || "—");

let facing = "back";
let rotation = 0;
let lastFrameImg = null;
let sparkHistory = [];
let clipFilter = "all";
let lastLogCount = 0;

/* ------------------------------------------------------------ transport */
function hasBridge() {
  return !!(window.pywebview && window.pywebview.api && window.pywebview.api.state);
}

async function call(method, ...args) {
  if (hasBridge()) return await window.pywebview.api[method](...args);
  const r = await fetch("/api/" + method, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  return await r.json();
}

/* --------------------------------------------------------------- render */
function render(state) {
  renderHeader(state);
  renderDevices(state);
  renderUsb(state);
  renderTarget(state);
  renderSpark(state);
  renderStreams(state);
  renderClip(state);
  renderCam(state);
  renderSettings(state);
  renderLog(state);
}

function renderHeader(state) {
  const conn = state.conn || {};
  const net = state.net || {};
  $("versionChip").textContent = "v" + (state.version || "—");
  $("localIp").textContent = net.local_ip || "—";
  $("statusText").textContent =
    conn.state === "connected" ? "Connected" :
    conn.state === "connecting" ? "Connecting…" :
    conn.state === "error" ? "Error" : "Not connected";
  const pill = $("statusPill");
  pill.className = "pill " + (conn.state === "connected" ? "pill-live" :
    conn.state === "error" ? "pill-bad" :
    conn.state === "connecting" ? "pill-busy" : "pill-idle");
  $("mdnsChip").classList.toggle("off", net.mdns === false);
}

function renderDevices(state) {
  const list = $("devList");
  const devs = state.devices || [];
  $("devCount").textContent = String(devs.length);
  $("empty").classList.toggle("hidden", devs.length > 0);

  const sig = JSON.stringify([devs.map((d) => [d.key, d.name, d.source, d.addr]), state.sel,
    (state.conn || {}).state, (state.conn || {}).rtt_ms]);
  if (sig === renderDevices.sig) return;
  renderDevices.sig = sig;

  list.textContent = "";
  for (const d of devs) {
    const selected = d.key === state.sel;
    const connected = selected && (state.conn || {}).state === "connected";
    const li = document.createElement("li");
    li.className = "endpoint" + (selected ? " selected" : "");
    li.dataset.key = d.key;
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", selected ? "true" : "false");

    const main = document.createElement("div");
    main.className = "ep-main";

    const avatar = document.createElement("div");
    const isUsb = (d.source || "").toLowerCase() === "adb";
    avatar.className = "ep-avatar" + (isUsb ? " usb" : "");
    avatar.innerHTML = isUsb ? ICONS.cable : ICONS.phone;

    const body = document.createElement("div");
    body.className = "ep-body";
    const title = document.createElement("div");
    title.className = "ep-title";
    const nameEl = document.createElement("b");
    nameEl.textContent = d.name;
    title.appendChild(nameEl);
    const chip = document.createElement("span");
    chip.className = "chip " + ((d.source || "").toLowerCase() === "adb" ? "chip-usb" : "chip-wifi");
    chip.textContent = transportOf(d.source);
    title.appendChild(chip);
    if (connected) {
      const live = document.createElement("span");
      live.className = "chip chip-live";
      live.textContent = "live";
      title.appendChild(live);
    }
    body.appendChild(title);

    const sub = document.createElement("div");
    sub.className = "ep-sub";
    const addr = document.createElement("span");
    addr.className = "mono";
    addr.textContent = d.addr + (d.detail ? " · " + d.detail : "");
    sub.appendChild(addr);
    if (connected && (state.conn || {}).rtt_ms) {
      const rtt = document.createElement("span");
      rtt.className = "ep-rtt mono";
      rtt.textContent = state.conn.rtt_ms.toFixed(1) + " ms";
      sub.appendChild(rtt);
    }
    body.appendChild(sub);
    main.appendChild(avatar);
    main.appendChild(body);

    const badges = document.createElement("div");
    badges.className = "ep-badges";
    const action = document.createElement("button");
    action.className = "ep-connect" + (connected ? " on" : "");
    action.innerHTML = connected
      ? ICONS.check + "<span>Connected</span>"
      : ICONS.link + "<span>Connect</span>";
    action.disabled = connected;
    action.title = connected ? "This phone is linked" : "Connect to " + d.name;
    action.addEventListener("click", (e) => {
      e.stopPropagation();
      connectTo(d.key);
    });
    badges.appendChild(action);
    main.appendChild(badges);
    li.appendChild(main);

    if (connected) {
      const metrics = document.createElement("div");
      metrics.className = "ep-metrics";
      const cam = document.createElement("span");
      cam.textContent = (state.cam || {}).enabled ? "camera streaming" : "camera idle";
      const mic = document.createElement("span");
      mic.textContent = (state.mic || {}).enabled ? "mic streaming" : "mic idle";
      const proto = document.createElement("span");
      proto.className = "mono";
      proto.textContent = "protocol v1";
      metrics.append(cam, mic, proto);
      li.appendChild(metrics);
    }

    li.addEventListener("click", () => selectDevice(d.key));
    li.addEventListener("dblclick", () => connectTo(d.key));
    list.appendChild(li);
  }
}

function renderUsb(state) {
  const usb = (state.devices || []).find((d) => d.source === "adb");
  const banner = $("usbBanner");
  banner.classList.toggle("hidden", !usb);
  const btn = $("quickConnectUsb");
  btn.onclick = usb ? () => connectTo(usb.key) : null;
}

function renderTarget(state) {
  const devs = state.devices || [];
  const sel = devs.find((d) => d.key === state.sel) || null;
  const conn = state.conn || {};
  const connected = conn.state === "connected";

  $("targetName").textContent = sel ? sel.name : "No phone selected";
  $("tTransport").textContent = sel ? transportOf(sel.source) : "—";
  $("tTransportSub").textContent = connected ? "socket open" : (sel ? "ready" : "pick a phone");
  $("tPort").textContent = connected && conn.port ? conn.port + " / tcp" : (sel ? sel.addr.split(":")[1] + " / tcp" : "—");
  $("tRtt").textContent = conn.rtt_ms ? conn.rtt_ms.toFixed(1) + " ms" : "—";
  $("tUptime").textContent = connected ? formatUptime(conn.uptime || 0) : "—";

  const pill = $("targetPill");
  const text = $("targetStatusText");
  if (connected) {
    pill.className = "status-pill live";
    text.textContent = "Connected";
  } else if (conn.state === "connecting") {
    pill.className = "status-pill busy";
    text.textContent = "Connecting…";
  } else if (conn.state === "error") {
    pill.className = "status-pill bad";
    text.textContent = "Error";
  } else {
    pill.className = "status-pill idle";
    text.textContent = sel ? "Ready to link" : "Pick a device";
  }

  $("teleGrid").classList.toggle("hidden", !connected);
  $("sparkCard").classList.toggle("hidden", !connected);
  $("sessionHint").classList.toggle("hidden", connected);
  $("sessionHint").textContent = sel
    ? "Press Connect on the " + sel.name + " card to open a session."
    : "Pick a phone, then press Connect on its card.";
  $("disconnectBtn").disabled = !connected;
}

function formatUptime(sec) {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  return String(m).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}

function renderSpark(state) {
  const stats = state.stats || {};
  const kbs = stats.rx_kbs || 0;
  sparkHistory.push(kbs);
  if (sparkHistory.length > 90) sparkHistory.shift();

  const peak = Math.max(...sparkHistory, 0.001);
  $("sparkPeak").textContent = (peak * 8 / 1024).toFixed(1) + " Mb/s";
  $("sparkNow").textContent = kbs.toFixed(0) + " kB/s now";
  $("sparkFrames").textContent = (stats.vfps || 0).toFixed(1) + " fps · " +
    (stats.afps || 0).toFixed(0) + " audio fps";

  const canvas = $("spark");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 300;
  const h = canvas.clientHeight || 60;
  if (canvas.width !== Math.round(w * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#0053d7";
  const n = sparkHistory.length;
  if (n < 2) return;
  const x = (i) => (i / (n - 1)) * w;
  const y = (v) => h - 4 - (v / peak) * (h - 10);

  ctx.beginPath();
  ctx.moveTo(0, h);
  sparkHistory.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(w, h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, accent + "55");
  grad.addColorStop(1, accent + "00");
  ctx.fillStyle = grad;
  ctx.fill();

  ctx.beginPath();
  sparkHistory.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = accent;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.stroke();
}

function renderStreams(state) {
  const mic = state.mic || {};
  const spk = state.spk || {};
  const stats = state.stats || {};
  const level = Math.max(0, Math.min(1, mic.level || 0));
  $("micBar").style.width = (level * 100).toFixed(1) + "%";
  $("micDb").textContent = mic.db == null ? "−∞ dB" : mic.db.toFixed(1) + " dB";
  $("micRoute").textContent = mic.sink || "not connected";
  $("micRoute").classList.toggle("bad", mic.sink_ok === false);

  $("spkBar").style.width = Math.min(100, (stats.rx_kbs || 0) / 2).toFixed(0) + "%";
  $("spkKbps").textContent = spk.enabled ? (stats.rx_kbs || 0).toFixed(0) + " kB/s" : "idle";
  $("spkRoute").textContent = spk.device || (spk.mute_pc ? "PC muted while streaming" : "PC sound → phone");
}

function classify(text) {
  if (/^https?:\/\//i.test(text)) return "link";
  if (/[{};<>]|=>|function |import |def /.test(text)) return "code";
  return "text";
}

function timeAgo(ts) {
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  return Math.floor(s / 3600) + "h ago";
}

function renderClip(state) {
  const clip = state.clip || {};
  const items = (clip.items || []).filter((i) => clipFilter === "all" || classify(i.text || "") === clipFilter);
  const all = clip.items || [];
  $("clipCount").textContent = String(all.length);
  $("clipToggle").checked = clip.enabled !== false;
  $("clipEmpty").style.display = items.length ? "none" : "";

  const body = $("clipBody");
  const sig = JSON.stringify(items.map((i) => [i.hash, i.origin])) + clipFilter;
  if (sig !== renderClip.sig) {
    renderClip.sig = sig;
    body.textContent = "";
    for (const item of items.slice(0, 12)) {
      const tr = document.createElement("tr");
      const text = (item.text || "").replace(/\s+/g, " ");

      const dir = document.createElement("td");
      const tag = document.createElement("span");
      const fromPhone = item.origin === "phone";
      tag.className = "src-icon " + (fromPhone ? "phone" : "pc");
      tag.innerHTML = fromPhone ? ICONS.phone : ICONS.laptop;
      tag.title = fromPhone ? "Copied on the phone" : "Copied on the PC";
      dir.appendChild(tag);

      const prev = document.createElement("td");
      prev.className = "mono preview";
      prev.textContent = text.slice(0, 64);
      prev.title = text;

      const type = document.createElement("td");
      type.textContent = classify(text);

      const when = document.createElement("td");
      when.className = "muted";
      when.textContent = item.ts ? timeAgo(item.ts) : "—";

      const act = document.createElement("td");
      act.className = "right";
      const btn = document.createElement("button");
      btn.className = "icon-btn tiny";
      btn.title = "Copy again";
      btn.innerHTML = ICONS.copy;
      btn.onclick = () => call("copy_to_clip", item.text || "");
      act.appendChild(btn);

      tr.append(dir, prev, type, when, act);
      body.appendChild(tr);
    }
  }
}

/* ------------------------------------------------------------- camera */
function renderCam(state) {
  const cam = state.cam || {};
  $("camToggle").checked = !!cam.enabled;
  $("previewCard").classList.toggle("hidden", !cam.enabled);
  if (cam.size && document.activeElement !== $("sizeSel")) $("sizeSel").value = cam.size;
  if (cam.fps && document.activeElement !== $("fpsInput")) {
    const want = String(Math.round(cam.fps));
    const sel = $("fpsInput");
    if ([...sel.options].some((o) => o.value === want)) sel.value = want;
  }
  if (cam.facing && cam.facing !== facing) {
    facing = cam.facing;
    setFacingUI(facing);
  }
  const canvas = $("camCanvas");
  $("camPlaceholder").style.display = cam.frame ? "none" : "";
  $("camMeta").textContent = cam.frame
    ? (cam.size || "") + " · " + (cam.fps || 0).toFixed(0) + " fps"
    : "no frames";
  if (cam.frame && cam.frame !== lastFrameImg) {
    lastFrameImg = cam.frame;
    const img = new Image();
    img.onload = () => drawFrame(img);
    img.src = cam.frame;
  }
}

function drawFrame(img) {
  const canvas = $("camCanvas");
  const stage = $("camStage");
  if (!canvas || !stage) return;
  const cw = stage.clientWidth;
  const ch = stage.clientHeight;
  const swap = rotation === 90 || rotation === 270;
  const scale = Math.min(cw / (swap ? img.height : img.width), ch / (swap ? img.width : img.height));
  const w = img.width * scale;
  const h = img.height * scale;
  canvas.width = cw;
  canvas.height = ch;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, cw, ch);
  ctx.save();
  ctx.translate(cw / 2, ch / 2);
  ctx.rotate((rotation * Math.PI) / 180);
  ctx.drawImage(img, -w / 2, -h / 2, w, h);
  ctx.restore();
}

function setRotation(next) {
  rotation = ((next % 360) + 360) % 360;
  $("rotLabel").textContent = rotation + "°";
  if (lastFrameImg) drawFrame(lastFrameImg);
}

function setFacingUI(value) {
  for (const b of document.querySelectorAll("#facingSeg button")) {
    b.classList.toggle("on", b.dataset.facing === value);
  }
}

/* ----------------------------------------------------------- settings */
function renderSettings(state) {
  const mic = state.mic || {};
  const cam = state.cam || {};
  const spk = state.spk || {};
  const clip = state.clip || {};
  $("setClipCount").textContent = String((clip.items || []).length);
  if (mic.sink) $("setSinkName").textContent = mic.sink;
  const sinkOk = $("setSinkState");
  sinkOk.textContent = mic.sink_ok ? "ready" : "missing";
  sinkOk.classList.toggle("on", !!mic.sink_ok);
  $("setVersion").textContent = "version " + (state.version || "—");
  if (document.activeElement !== $("clipToggle2")) $("clipToggle2").checked = clip.enabled !== false;
}

function renderLog(state) {
  const log = state.log || [];
  const pre = $("log");
  pre.textContent = log.join("\n");
  if (log.length !== lastLogCount) {
    lastLogCount = log.length;
    pre.scrollTop = pre.scrollHeight;
  }
}

/* -------------------------------------------------------------- actions */
async function selectDevice(key) {
  await call("select", key);
  renderDevices.sig = null;
  const state = await call("state");
  render(state);
}

async function connectTo(key) {
  if (key) {
    await call("select", key);
    renderDevices.sig = null;
  }
  await call("connect", key || "");
  const state = await call("state");
  render(state);
}

async function refresh() {
  $("refreshBtn").classList.add("spin");
  try {
    await call("refresh");
  } finally {
    setTimeout(() => $("refreshBtn").classList.remove("spin"), 600);
  }
}

function pushCfg() {
  call("set_cfg",
    $("micToggle").checked, $("camToggle").checked,
    $("sizeSel").value, Number($("fpsInput").value) || 10,
    facing, $("spkToggle").checked, $("muteChk").checked,
    $("clipToggle").checked || $("clipToggle2").checked);
}

/* ------------------------------------------------------------- wiring */
$("refreshBtn").addEventListener("click", refresh);
$("disconnectBtn").addEventListener("click", () => call("disconnect"));
$("openSettingsBtn").addEventListener("click", () => showPage("settings"));

$("manualForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = $("manualInput").value.trim();
  if (!value) return;
  await call("add_manual", value);
  $("manualInput").value = "";
  renderDevices.sig = null;
});

for (const el of ["micToggle", "camToggle", "spkToggle", "muteChk", "clipToggle", "clipToggle2"]) {
  $(el).addEventListener("change", () => {
    $("clipToggle").checked = $("clipToggle2").checked;
    pushCfg();
  });
}
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

for (const b of document.querySelectorAll("#clipFilters .filter-chip")) {
  b.addEventListener("click", () => {
    clipFilter = b.dataset.filter;
    for (const o of document.querySelectorAll("#clipFilters .filter-chip")) {
      o.classList.toggle("on", o === b);
    }
    renderClip.sig = null;
  });
}

const clearHistory = async () => {
  await call("clear_clip_history");
  renderClip.sig = null;
};
$("clearClipBtn").addEventListener("click", clearHistory);
$("clearClipBtn2").addEventListener("click", clearHistory);
$("openSoundBtn").addEventListener("click", () => call("open_sound_settings"));

$("logToggle").addEventListener("click", () => {
  const log = $("log");
  log.classList.toggle("hidden");
  $("logToggle").textContent = log.classList.contains("hidden") ? "show" : "hide";
});

/* pages */
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

/* theme */
const THEME_KEY = "onelink-theme";
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("themeBtn").title = theme === "dark" ? "Switch to light" : "Switch to dark";
}
applyTheme(localStorage.getItem(THEME_KEY) ||
  (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
$("themeBtn").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
  renderSpark.sig = null;
});

setRotation(0);
window.addEventListener("resize", () => lastFrameImg && drawFrame(lastFrameImg));

/* boot */
async function tick() {
  try {
    const state = await call("state");
    render(state);
  } catch (err) {
    /* the bridge is not up yet */
  }
}
window.addEventListener("pywebviewready", () => tick());
tick();
setInterval(tick, POLL_MS);

const spinStyle = document.createElement("style");
spinStyle.textContent = ".spin svg{animation:spin .6s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}";
document.head.appendChild(spinStyle);
