/* 分轨工作台前端逻辑：文件列表 → 分离进度 → 混音台 */
"use strict";

const TRACK_COLORS = {
  vocals: "#e5b567", drums: "#d4726a", bass: "#7aa2e8", guitar: "#8fbf6f",
  piano: "#c792ea", trumpet: "#e8a838", strings: "#6fc3c9", other: "#9aa0ab",
};

const $ = (sel) => document.querySelector(sel);
const views = { files: $("#view-files"), progress: $("#view-progress"), mixer: $("#view-mixer") };

/* 浏览器令牌: 免登录的会话隔离, 每个浏览器只看到自己上传的歌 */
const CLIENT_ID = (() => {
  let id = localStorage.getItem("stem_client_id");
  if (!id) {
    id = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)).replace(/-/g, "").slice(0, 12);
    localStorage.setItem("stem_client_id", id);
  }
  return id;
})();

function api(url, opts = {}) {
  opts.headers = { ...(opts.headers || {}), "X-Client-Id": CLIENT_ID };
  return fetch(url, opts);
}

function showView(name) {
  for (const [k, el] of Object.entries(views)) el.hidden = k !== name;
}

function fmtTime(sec) {
  if (!isFinite(sec)) sec = 0;
  const m = Math.floor(sec / 60), s = sec - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}
function fmtSize(bytes) { return (bytes / 1048576).toFixed(1) + " MB"; }

/* ============================================================ 文件列表 */

function fileRow(f) {
  const row = document.createElement("div");
  row.className = "file-row";
  row.innerHTML = `
    <span class="file-name"></span>
    <span class="file-info mono">${fmtTime(f.duration)} · ${fmtSize(f.size)} · ${(f.samplerate / 1000).toFixed(1)}kHz</span>`;
  row.querySelector(".file-name").textContent = f.name;
  if (f.separated) {
    const open = document.createElement("button");
    open.className = "btn btn-primary";
    open.textContent = "打开工作台";
    open.onclick = () => openMixer(f.job_id);
    const redo = document.createElement("button");
    redo.className = "btn";
    redo.textContent = "重新分离";
    redo.onclick = () => {
      if (confirm(`重新分离《${f.name}》？现有分轨会被覆盖。`)) startSeparate(f, true);
    };
    row.append(open, redo);
  } else {
    const go = document.createElement("button");
    go.className = "btn btn-primary";
    go.textContent = "开始分离";
    go.onclick = () => startSeparate(f, false);
    row.append(go);
  }
  return row;
}

async function loadFiles() {
  showView("files");
  $("#topbar-right").textContent = "";
  const list = $("#file-list");
  list.innerHTML = "";
  let files;
  try {
    files = await (await api("/api/files")).json();
  } catch (e) {
    list.innerHTML = `<div class="file-empty">无法连接后端服务，请确认服务器已启动</div>`;
    return;
  }
  const mine = files.filter((f) => f.scope === "mine");
  const shared = files.filter((f) => f.scope !== "mine");
  if (mine.length) {
    const h = document.createElement("div");
    h.className = "group-title";
    h.textContent = "我的上传";
    list.append(h);
    mine.forEach((f) => list.append(fileRow(f)));
  }
  if (shared.length) {
    const h = document.createElement("div");
    h.className = "group-title";
    h.textContent = "共享曲库";
    list.append(h);
    shared.forEach((f) => list.append(fileRow(f)));
  }
  if (!files.length) {
    list.innerHTML = `<div class="file-empty">还没有歌曲——点上方「上传歌曲」，或把音频放进服务器的曲库目录后刷新</div>`;
  }
}

/* ---------------- 上传 ---------------- */

function setupUpload() {
  const input = $("#upload-input");
  $("#btn-upload").onclick = () => input.click();
  input.onchange = () => {
    if (!input.files.length) return;
    const file = input.files[0];
    const bar = $("#upload-progress"), fill = $("#upload-fill"), status = $("#upload-status");
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.setRequestHeader("X-Client-Id", CLIENT_ID);
    bar.hidden = false;
    status.textContent = `正在上传 ${file.name}…`;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) fill.style.width = ((e.loaded / e.total) * 100).toFixed(0) + "%";
    };
    xhr.onload = () => {
      bar.hidden = true;
      fill.style.width = "0%";
      input.value = "";
      if (xhr.status === 200) {
        status.textContent = "上传完成";
        loadFiles();
      } else {
        let msg = "上传失败";
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) {}
        status.textContent = msg;
      }
    };
    xhr.onerror = () => {
      bar.hidden = true;
      status.textContent = "上传失败，请检查网络";
    };
    xhr.send(form);
  };
}

/* ============================================================ 分离进度 */

let pollTimer = null;

async function startSeparate(file, force) {
  const res = await api("/api/separate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file: file.name, scope: file.scope || "shared", force }),
  });
  if (!res.ok) { alert("启动分离失败: " + (await res.text())); return; }
  const { job_id } = await res.json();
  watchProgress(job_id, file.name);
}

function watchProgress(jobId, songName) {
  showView("progress");
  $("#progress-song").textContent = songName || jobId;
  $("#progress-error").hidden = true;
  $("#btn-progress-back").onclick = () => { clearTimeout(pollTimer); loadFiles(); };
  const poll = async () => {
    let st;
    try {
      st = await (await api(`/api/job/${encodeURIComponent(jobId)}/status`)).json();
    } catch (e) { pollTimer = setTimeout(poll, 2000); return; }
    $("#progress-stage").textContent =
      st.queue_pos > 0 ? `排队中（第 ${st.queue_pos} 位，GPU 一次处理一首）` : (st.stage || "");
    $("#progress-fill").style.width = (st.percent || 0) + "%";
    $("#progress-pct").textContent = (st.percent || 0).toFixed(0) + "%";
    if (st.error) {
      const errBox = $("#progress-error");
      errBox.textContent = st.error;
      errBox.hidden = false;
      return;
    }
    if (st.done) { openMixer(jobId); return; }
    pollTimer = setTimeout(poll, 1000);
  };
  poll();
}

/* ============================================================ 混音台 */

const mixer = {
  jobId: null, meta: null, peaks: null,
  ctx: null, masterGain: null,
  channels: [],   // {id,label,color,el,gainNode,state:{gain,mute,solo},canvas,dim,bright,rmsEl,volEl,btnM,btnS}
  playing: false, rafId: null, syncTimer: null,
};

async function openMixer(jobId) {
  teardownMixer();
  const enc = encodeURIComponent(jobId);
  let meta, peaks;
  try {
    [meta, peaks] = await Promise.all([
      (await fetch(`/jobs/${enc}/meta.json`)).json(),
      (await fetch(`/jobs/${enc}/peaks.json`)).json(),
    ]);
  } catch (e) { alert("读取分轨数据失败"); loadFiles(); return; }

  mixer.jobId = jobId;
  mixer.meta = meta;
  mixer.peaks = peaks;
  showView("mixer");

  // 顶栏: 歌名 + 返回
  const right = $("#topbar-right");
  right.innerHTML = "";
  const nameSpan = document.createElement("span");
  nameSpan.textContent = meta.source_file;
  const back = document.createElement("button");
  back.className = "btn";
  back.textContent = "换一首";
  back.onclick = () => { teardownMixer(); loadFiles(); };
  right.append(nameSpan, back);

  $("#tc-total").textContent = fmtTime(meta.duration);
  $("#tc-now").textContent = fmtTime(0);
  $("#export-note").textContent = "";

  mixer.ctx = new (window.AudioContext || window.webkitAudioContext)();
  mixer.masterGain = mixer.ctx.createGain();
  mixer.masterGain.connect(mixer.ctx.destination);

  const rack = $("#rack");
  rack.innerHTML = "";
  const tpl = $("#tpl-channel");
  mixer.channels = [];

  for (const t of meta.tracks) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    const color = TRACK_COLORS[t.id] || "#9aa0ab";
    node.querySelector(".scribble-color").style.background = color;
    node.querySelector(".ch-label").textContent = t.label;
    node.querySelector(".ch-label").style.color = color;
    node.querySelector(".ch-rms").textContent = `RMS ${t.rms_db} dB`;
    const badges = node.querySelector(".ch-badges");
    if (t.experimental) badges.innerHTML += `<span class="badge">实验性</span>`;
    if (!t.active) {
      badges.innerHTML += `<span class="badge">无内容</span>`;
      node.classList.add("inactive");
    }

    const el = new Audio(`/jobs/${enc}/${t.file}`);
    el.preload = "auto";
    const src = mixer.ctx.createMediaElementSource(el);
    const gainNode = mixer.ctx.createGain();
    src.connect(gainNode).connect(mixer.masterGain);

    const ch = {
      id: t.id, label: t.label, color, el, gainNode,
      state: { gain: 1.0, mute: false, solo: false },
      canvas: node.querySelector("canvas"),
      volEl: node.querySelector(".ch-vol"),
      btnM: node.querySelector(".chbtn-m"),
      btnS: node.querySelector(".chbtn-s"),
      dim: null, bright: null,
    };

    ch.btnM.onclick = () => { ch.state.mute = !ch.state.mute; ch.btnM.classList.toggle("on", ch.state.mute); applyGains(); };
    ch.btnS.onclick = () => { ch.state.solo = !ch.state.solo; ch.btnS.classList.toggle("on", ch.state.solo); applyGains(); };
    const fader = node.querySelector(".ch-fader");
    fader.oninput = () => {
      ch.state.gain = fader.value / 100;
      ch.volEl.textContent = fader.value + "%";
      applyGains();
    };
    ch.canvas.parentElement.addEventListener("pointerdown", (ev) => seekFromPointer(ev, ch));

    mixer.channels.push(ch);
    rack.append(node);
  }

  applyGains();
  requestAnimationFrame(() => { renderAllWaves(); startRaf(); });

  $("#btn-play").onclick = togglePlay;
  $("#btn-rewind").onclick = () => seekAll(0);
  $("#btn-export").onclick = doExport;
  window.addEventListener("resize", renderAllWaves);
}

function teardownMixer() {
  if (mixer.rafId) cancelAnimationFrame(mixer.rafId);
  if (mixer.syncTimer) clearInterval(mixer.syncTimer);
  for (const ch of mixer.channels) { ch.el.pause(); ch.el.src = ""; }
  if (mixer.ctx) mixer.ctx.close();
  window.removeEventListener("resize", renderAllWaves);
  mixer.channels = [];
  mixer.ctx = null;
  mixer.playing = false;
  const btn = $("#btn-play");
  btn.textContent = "▶";
  btn.classList.remove("playing");
}

/* ---------------- 音量 / 静音 / 独奏 ---------------- */

function applyGains() {
  const anySolo = mixer.channels.some((c) => c.state.solo);
  for (const ch of mixer.channels) {
    let g = ch.state.gain;
    if (ch.state.mute) g = 0;
    else if (anySolo && !ch.state.solo) g = 0;
    ch.gainNode.gain.setTargetAtTime(g, mixer.ctx.currentTime, 0.015);
  }
}

/* ---------------- 播放控制与同步 ---------------- */

function master() { return mixer.channels[0] ? mixer.channels[0].el : null; }

async function togglePlay() {
  if (!mixer.channels.length) return;
  if (mixer.ctx.state === "suspended") await mixer.ctx.resume();
  const btn = $("#btn-play");
  if (mixer.playing) {
    mixer.channels.forEach((c) => c.el.pause());
    mixer.playing = false;
    btn.textContent = "▶";
    btn.classList.remove("playing");
    clearInterval(mixer.syncTimer);
  } else {
    const m = master();
    if (m.ended || m.currentTime >= mixer.meta.duration - 0.05) seekAll(0);
    await Promise.all(mixer.channels.map((c) => c.el.play().catch(() => {})));
    mixer.playing = true;
    btn.textContent = "⏸";
    btn.classList.add("playing");
    mixer.syncTimer = setInterval(syncTracks, 700);
  }
}

function syncTracks() {
  const m = master();
  if (!m || !mixer.playing) return;
  if (m.ended) {
    mixer.playing = false;
    const btn = $("#btn-play");
    btn.textContent = "▶";
    btn.classList.remove("playing");
    clearInterval(mixer.syncTimer);
    return;
  }
  for (const ch of mixer.channels.slice(1)) {
    if (Math.abs(ch.el.currentTime - m.currentTime) > 0.06) ch.el.currentTime = m.currentTime;
  }
}

function seekAll(t) {
  mixer.channels.forEach((c) => { c.el.currentTime = t; });
}

function seekFromPointer(ev, ch) {
  const rect = ch.canvas.getBoundingClientRect();
  const frac = Math.min(Math.max((ev.clientX - rect.left) / rect.width, 0), 1);
  seekAll(frac * mixer.meta.duration);
}

/* ---------------- 波形渲染 ---------------- */

function renderWave(peaks, color, w, h, alpha) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const g = c.getContext("2d");
  g.globalAlpha = alpha;
  g.fillStyle = color;
  const n = peaks.length;
  const mid = h / 2;
  const barW = Math.max(w / n, 1);
  for (let i = 0; i < n; i++) {
    const x = (i / n) * w;
    const [mn, mx] = peaks[i];
    const y1 = mid - mx * mid * 0.94;
    const y2 = mid - mn * mid * 0.94;
    g.fillRect(x, y1, barW, Math.max(y2 - y1, 1));
  }
  return c;
}

function renderAllWaves() {
  const dpr = window.devicePixelRatio || 1;
  for (const ch of mixer.channels) {
    const rect = ch.canvas.parentElement.getBoundingClientRect();
    const w = Math.max(Math.round(rect.width * dpr), 10);
    const h = Math.max(Math.round(rect.height * dpr), 10);
    ch.canvas.width = w;
    ch.canvas.height = h;
    const pk = mixer.peaks[ch.id] || [];
    ch.dim = renderWave(pk, ch.color, w, h, 0.38);
    ch.bright = renderWave(pk, ch.color, w, h, 1.0);
  }
}

function startRaf() {
  const loop = () => {
    const m = master();
    if (m && mixer.meta) {
      const t = m.currentTime;
      $("#tc-now").textContent = fmtTime(t);
      const frac = Math.min(t / mixer.meta.duration, 1);
      for (const ch of mixer.channels) {
        if (!ch.dim) continue;
        const g = ch.canvas.getContext("2d");
        const w = ch.canvas.width, h = ch.canvas.height;
        g.clearRect(0, 0, w, h);
        g.drawImage(ch.dim, 0, 0);
        const x = Math.round(frac * w);
        if (x > 0) g.drawImage(ch.bright, 0, 0, x, h, 0, 0, x, h);
        g.fillStyle = "#ffb454";
        g.fillRect(x, 0, Math.max(window.devicePixelRatio, 1), h);
      }
    }
    mixer.rafId = requestAnimationFrame(loop);
  };
  loop();
}

/* ---------------- 导出 ---------------- */

async function doExport() {
  const btn = $("#btn-export");
  const note = $("#export-note");
  btn.disabled = true;
  note.textContent = "正在导出…";
  const tracks = {};
  for (const ch of mixer.channels) tracks[ch.id] = { ...ch.state };
  try {
    const res = await api(`/api/job/${encodeURIComponent(mixer.jobId)}/export`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tracks }),
    });
    if (!res.ok) throw new Error(await res.text());
    const out = await res.json();
    note.textContent = out.clipped ? "已导出（总和超出满幅，已自动压低防削波）" : "已导出";
    const a = document.createElement("a");
    a.href = out.url;
    a.download = out.filename;
    a.click();
  } catch (e) {
    note.textContent = "导出失败: " + e.message;
  } finally {
    btn.disabled = false;
  }
}

/* ============================================================ 启动 */
setupUpload();
loadFiles();
