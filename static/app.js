/* YouTube Automate — logique de l'interface */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  account: { connected: false },
  settings: {},
  categories: [],
  queue: [],
  history: [],
  job: {},
  authWindow: null,
  authPoll: null,
  jobPoll: null,
  openRows: new Set(),
  dirty: new Set(),
};

/* ------------------------------------------------------------------ utils */

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = `Erreur ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch { /* réponse non JSON */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => {
    el.classList.add("hide");
    setTimeout(() => el.remove(), 220);
  }, kind === "error" ? 6000 : 3200);
}

const pad = (n) => String(n).padStart(2, "0");
const dateOf = (iso) => (iso || "").slice(0, 10);
const timeOf = (iso) => (iso || "").slice(11, 16);

function formatSize(bytes) {
  if (!bytes) return "—";
  const mb = bytes / 1048576;
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} Go` : `${mb.toFixed(1)} Mo`;
}

function formatWhen(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" })
       + " · " + `${pad(d.getHours())}h${pad(d.getMinutes())}`;
}

function debounce(fn, delay = 450) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

/* ------------------------------------------------------------------ état */

async function refresh() {
  try {
    const data = await api("/api/state");
    Object.assign(state, data);
    renderAll();
  } catch (err) {
    toast(err.message, "error");
  }
}

function renderAll() {
  renderAccount();
  renderScheduler();
  renderQueue();
  renderHistory();
  renderSettings();
  renderActionbar();
}

/* ---------------------------------------------------------------- compte */

function renderAccount() {
  const { connected, channel } = state.account;
  const dot = $("#account-dot");
  const label = $("#account-label");
  const avatar = $("#account-avatar");
  const btn = $("#btn-account");

  dot.className = "dot " + (connected ? "on" : "off");
  avatar.hidden = !(connected && channel && channel.thumbnail);
  if (!avatar.hidden) avatar.src = channel.thumbnail;

  if (connected && channel) {
    const subs = channel.subscribers ? ` · ${Number(channel.subscribers).toLocaleString("fr-FR")} abonnés` : "";
    label.textContent = channel.title + subs;
  } else {
    label.textContent = connected ? "Compte connecté" : "Non connecté";
  }

  btn.textContent = connected ? "Déconnecter" : "Connecter";
  $("#connect-card").hidden = connected;
  $("#queue-workspace").hidden = !connected;

  // Sans client_secret.json, aucune connexion n'est possible : on l'annonce d'emblée.
  if (!connected && !state.account.client_secret) {
    $("#btn-connect").disabled = true;
    const hint = $("#connect-hint");
    hint.className = "connect-hint err";
    hint.innerHTML = "Le fichier <code>client_secret.json</code> est absent du dossier du projet. "
                   + "Voir l'étape 2 du <code>README.md</code>.";
  } else {
    $("#btn-connect").disabled = false;
  }
}

async function startAuth() {
  const hint = $("#connect-hint");
  hint.className = "connect-hint";
  hint.textContent = "Ouverture de la fenêtre Google…";

  try {
    const { auth_url } = await api("/api/auth/start", { method: "POST" });
    const popup = window.open(auth_url, "yta-oauth", "width=520,height=680,left=200,top=80");
    if (!popup) {
      hint.className = "connect-hint err";
      hint.innerHTML = `La fenêtre a été bloquée. <a href="${auth_url}" target="_blank" rel="noopener">Ouvrir l'autorisation</a>`;
      return;
    }
    state.authWindow = popup;
    hint.textContent = "Autorisez l'accès dans la fenêtre Google…";
    pollAuth();
  } catch (err) {
    hint.className = "connect-hint err";
    hint.textContent = err.message;
  }
}

function pollAuth() {
  clearInterval(state.authPoll);
  state.authPoll = setInterval(async () => {
    let status;
    try { status = await api("/api/auth/status"); } catch { return; }

    if (status.connected) {
      clearInterval(state.authPoll);
      try { state.authWindow?.close(); } catch { /* déjà fermée */ }
      $("#connect-hint").textContent = "";
      toast("Compte YouTube connecté", "ok");
      refresh();
    } else if (status.flow.status === "error") {
      clearInterval(state.authPoll);
      const hint = $("#connect-hint");
      hint.className = "connect-hint err";
      hint.textContent = status.flow.error || "La connexion a échoué.";
    }
  }, 1200);
}

async function logout() {
  if (!confirm("Déconnecter le compte YouTube ?")) return;
  await api("/api/auth/logout", { method: "POST" });
  toast("Compte déconnecté");
  refresh();
}

/* ----------------------------------------------------------- programmation */

function renderScheduler() {
  const s = state.settings;
  const first = state.queue[0];
  const base = first ? first.publish_at : state.next_slot;

  if (document.activeElement?.id !== "sched-date") $("#sched-date").value = dateOf(base);
  if (document.activeElement?.id !== "sched-time") $("#sched-time").value = timeOf(base) || `${pad(s.post_hour)}:${pad(s.post_minute)}`;
  if (document.activeElement?.id !== "sched-interval") $("#sched-interval").value = s.interval_days;

  const enabled = state.queue.filter((i) => i.enabled);
  $("#scheduler-summary").textContent = enabled.length
    ? `${enabled.length} vidéo(s) · du ${formatWhen(enabled[0].publish_at)} au ${formatWhen(enabled[enabled.length - 1].publish_at)}`
    : "";
}

async function applySchedule() {
  const [hour, minute] = ($("#sched-time").value || "08:30").split(":").map(Number);
  try {
    await api("/api/queue/reschedule", {
      method: "POST",
      body: JSON.stringify({
        start_date: $("#sched-date").value,
        hour, minute,
        interval_days: Number($("#sched-interval").value) || 1,
      }),
    });
    toast("Planning appliqué", "ok");
    refresh();
  } catch (err) {
    toast(err.message, "error");
  }
}

/* -------------------------------------------------------------- file d'attente */

function renderQueue() {
  const list = $("#queue-list");
  const tpl = $("#tpl-queue-item");
  const jobLog = Object.fromEntries((state.job.log || []).map((e) => [e.filename, e]));

  list.textContent = "";
  state.queue.forEach((item) => list.append(buildQueueRow(item, tpl, jobLog[item.filename])));

  $("#queue-empty").hidden = state.queue.length > 0;
  $("#count-queue").textContent = state.queue.length;
  $("#select-all").checked = state.queue.length > 0 && state.queue.every((i) => i.enabled);
}

function buildQueueRow(item, tpl, logEntry) {
  const row = tpl.content.firstElementChild.cloneNode(true);
  const name = item.filename;
  row.dataset.filename = name;
  row.classList.toggle("is-off", !item.enabled);

  $(".q-enabled", row).checked = item.enabled;
  $(".filename", row).textContent = name;
  $(".filename", row).title = name;
  $(".filesize", row).textContent = formatSize(item.size);

  const title = $(".q-title", row);
  title.value = item.title || "";
  updateCounter(row);

  $(".q-date", row).value = dateOf(item.publish_at);
  $(".q-time", row).value = timeOf(item.publish_at);
  $(".q-description", row).value = item.description || "";
  $(".q-tags", row).value = (item.tags || []).join(", ");

  const video = $(".thumb video", row);
  video.src = `/api/videos/${encodeURIComponent(name)}#t=0.5`;
  video.addEventListener("loadeddata", () => { video.dataset.ready = "1"; });

  if (state.openRows.has(name)) {
    row.classList.add("is-open");
    $(".qitem-details", row).hidden = false;
  }

  // --- état d'envoi
  const status = $(".qitem-status", row);
  if (state.job.running && state.job.current === name) {
    row.classList.add("is-busy");
    status.hidden = false;
    status.className = "qitem-status";
    status.innerHTML = `<span>Envoi…</span><div class="bar"><i style="width:${Math.round((state.job.current_progress || 0) * 100)}%"></i></div>`
      + `<span>${Math.round((state.job.current_progress || 0) * 100)} %</span>`;
  } else if (logEntry && logEntry.status === "error") {
    row.classList.add("is-error");
    status.hidden = false;
    status.className = "qitem-status error";
    status.textContent = logEntry.message;
  }

  wireQueueRow(row, name);
  return row;
}

function updateCounter(row) {
  const input = $(".q-title", row);
  const counter = $(".counter", row);
  const len = input.value.length;
  counter.textContent = `${len}/100`;
  counter.classList.toggle("warn", len > 90 || len === 0);
}

const saveItem = debounce(async (filename, payload) => {
  try {
    await api(`/api/queue/item/${encodeURIComponent(filename)}`, {
      method: "POST", body: JSON.stringify(payload),
    });
    state.dirty.delete(filename);
    const item = state.queue.find((i) => i.filename === filename);
    if (item) Object.assign(item, payload);
    renderScheduler();
  } catch (err) {
    toast(err.message, "error");
  }
});

function wireQueueRow(row, name) {
  const title = $(".q-title", row);
  title.addEventListener("input", () => {
    updateCounter(row);
    saveItem(name, { title: title.value });
  });

  $(".q-regen", row).addEventListener("click", async () => {
    try {
      const { title: fresh } = await api(`/api/queue/item/${encodeURIComponent(name)}/title`, { method: "POST" });
      title.value = fresh;
      updateCounter(row);
      const item = state.queue.find((i) => i.filename === name);
      if (item) item.title = fresh;
    } catch (err) { toast(err.message, "error"); }
  });

  const pushDate = () => {
    const d = $(".q-date", row).value;
    const t = $(".q-time", row).value || "08:30";
    if (!d) return;
    saveItem(name, { publish_at: `${d}T${t}:00` });
  };
  $(".q-date", row).addEventListener("change", pushDate);
  $(".q-time", row).addEventListener("change", pushDate);

  $(".q-enabled", row).addEventListener("change", (e) => {
    row.classList.toggle("is-off", !e.target.checked);
    saveItem(name, { enabled: e.target.checked });
    const item = state.queue.find((i) => i.filename === name);
    if (item) item.enabled = e.target.checked;
    renderActionbar();
  });

  $(".q-description", row).addEventListener("input", (e) => saveItem(name, { description: e.target.value }));
  $(".q-tags", row).addEventListener("input", (e) => saveItem(name, { tags: e.target.value }));

  $(".q-toggle", row).addEventListener("click", () => {
    const details = $(".qitem-details", row);
    details.hidden = !details.hidden;
    row.classList.toggle("is-open", !details.hidden);
    details.hidden ? state.openRows.delete(name) : state.openRows.add(name);
  });

  $(".q-remove", row).addEventListener("click", async () => {
    const hard = confirm(`Retirer « ${name} » de la file ?\n\nOK = retirer de la file\nAnnuler = ne rien faire`);
    if (!hard) return;
    await api(`/api/queue/item/${encodeURIComponent(name)}`, { method: "DELETE" });
    toast("Vidéo retirée de la file");
    refresh();
  });

  wireDrag(row);
}

/* ------------------------------------------------------------ drag & drop */

let dragged = null;

function wireDrag(row) {
  row.addEventListener("dragstart", (e) => {
    if (e.target.closest("input, textarea, button")) { e.preventDefault(); return; }
    dragged = row;
    row.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", row.dataset.filename);
  });

  row.addEventListener("dragend", () => {
    row.classList.remove("dragging");
    $$(".qitem").forEach((r) => r.classList.remove("drop-target"));
    dragged = null;
  });

  row.addEventListener("dragover", (e) => {
    if (!dragged || dragged === row) return;
    e.preventDefault();
    row.classList.add("drop-target");
  });

  row.addEventListener("dragleave", () => row.classList.remove("drop-target"));

  row.addEventListener("drop", async (e) => {
    if (!dragged || dragged === row) return;
    e.preventDefault();
    row.classList.remove("drop-target");
    const list = $("#queue-list");
    const rows = [...list.children];
    list.insertBefore(dragged, rows.indexOf(dragged) < rows.indexOf(row) ? row.nextSibling : row);

    const order = [...list.children].map((r) => r.dataset.filename);
    try {
      await api("/api/queue/reorder", { method: "POST", body: JSON.stringify({ filenames: order }) });
      refresh();
    } catch (err) { toast(err.message, "error"); }
  });
}

/* ------------------------------------------------------------ dépôt fichiers */

function wireDropzone() {
  const zone = $("#dropzone");
  const input = $("#file-input");

  $("#btn-browse").addEventListener("click", () => input.click());
  input.addEventListener("change", () => { uploadFiles([...input.files]); input.value = ""; });

  ["dragenter", "dragover"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("is-over"); }));

  zone.addEventListener("drop", (e) => uploadFiles([...(e.dataTransfer?.files || [])]));
}

function uploadFiles(files) {
  const allowed = /\.(mp4|mov|avi|mkv|webm)$/i;
  const valid = files.filter((f) => allowed.test(f.name));
  if (!valid.length) {
    if (files.length) toast("Format non pris en charge", "error");
    return;
  }

  const form = new FormData();
  valid.forEach((f) => form.append("files", f));

  const box = $("#copy-progress");
  const bar = $("#copy-progress .bar > i");
  const label = $("#copy-progress span");
  box.hidden = false;
  bar.style.width = "0%";
  label.textContent = "0 %";

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/videos/upload");
  xhr.upload.addEventListener("progress", (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((e.loaded / e.total) * 100);
    bar.style.width = `${pct}%`;
    label.textContent = `${pct} %`;
  });
  xhr.addEventListener("load", () => {
    box.hidden = true;
    if (xhr.status >= 200 && xhr.status < 300) {
      const res = JSON.parse(xhr.responseText);
      toast(`${res.saved.length} vidéo(s) ajoutée(s)`, "ok");
      refresh();
    } else {
      toast("L'ajout des fichiers a échoué", "error");
    }
  });
  xhr.addEventListener("error", () => { box.hidden = true; toast("L'ajout des fichiers a échoué", "error"); });
  xhr.send(form);
}

/* ----------------------------------------------------------------- envoi */

function renderActionbar() {
  const bar = $("#actionbar");
  const job = state.job || {};
  const selected = state.queue.filter((i) => i.enabled && i.exists);

  if (!state.account.connected || (!selected.length && !job.running)) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;

  const running = job.running;
  $("#actionbar-progress").hidden = !running;
  $("#btn-cancel").hidden = !running;
  $("#btn-start").disabled = running || !selected.length;
  $("#btn-start").textContent = running ? "Envoi en cours…" : `Envoyer ${selected.length} vidéo(s)`;

  if (running) {
    const pct = Math.round((job.current_progress || 0) * 100);
    $("#actionbar-info").innerHTML = `<strong>${job.done}</strong> / ${job.total} envoyée(s)`;
    $("#progress-label").textContent = job.current || "Préparation…";
    $("#progress-count").textContent = `${pct} %`;
    $("#progress-bar").style.width = `${pct}%`;
  } else {
    const first = selected[0];
    $("#actionbar-info").innerHTML = first
      ? `Première publication le <strong>${formatWhen(first.publish_at)}</strong>`
      : "";
  }
}

async function startJob() {
  const selected = state.queue.filter((i) => i.enabled && i.exists);
  if (!selected.length) return;

  const past = selected.filter((i) => new Date(i.publish_at) <= new Date());
  if (past.length && !confirm(`${past.length} vidéo(s) ont une date déjà passée et seront refusées par YouTube.\n\nContinuer quand même ?`)) return;
  if (!confirm(`Envoyer ${selected.length} vidéo(s) sur YouTube ?\n\nElles seront privées puis publiées automatiquement aux dates indiquées.`)) return;

  try {
    await api("/api/job/start", { method: "POST", body: JSON.stringify({ filenames: selected.map((i) => i.filename) }) });
    state.job = { ...state.job, running: true, done: 0, total: selected.length };
    renderActionbar();
    pollJob();
  } catch (err) {
    toast(err.message, "error");
  }
}

function pollJob() {
  clearInterval(state.jobPoll);
  state.jobPoll = setInterval(async () => {
    let job;
    try { job = await api("/api/job"); } catch { return; }

    const wasRunning = state.job.running;
    state.job = job;

    if (job.running) {
      renderActionbar();
      updateBusyRow(job);
    } else if (wasRunning) {
      clearInterval(state.jobPoll);
      const failed = job.failed || 0;
      if (job.error) toast(job.error, "error");
      else if (failed) toast(`${job.done} envoyée(s), ${failed} en échec`, "error");
      else toast(`${job.done} vidéo(s) programmée(s) sur YouTube`, "ok");
      refresh();
    } else {
      clearInterval(state.jobPoll);
    }
  }, 700);
}

function updateBusyRow(job) {
  $$(".qitem").forEach((row) => {
    const isCurrent = row.dataset.filename === job.current;
    row.classList.toggle("is-busy", isCurrent);
    const status = $(".qitem-status", row);
    if (!isCurrent) return;
    const pct = Math.round((job.current_progress || 0) * 100);
    status.hidden = false;
    status.className = "qitem-status";
    status.innerHTML = `<span>Envoi…</span><div class="bar"><i style="width:${pct}%"></i></div><span>${pct} %</span>`;
  });
}

/* -------------------------------------------------------------- historique */

function renderHistory() {
  const tbody = $("#history-table tbody");
  const query = ($("#history-search").value || "").toLowerCase();
  const rows = state.history.filter((r) =>
    !query || (r.filename + " " + (r.title || "")).toLowerCase().includes(query));

  tbody.textContent = "";
  rows.forEach((r) => {
    const tr = document.createElement("tr");

    const cell = document.createElement("td");
    const title = document.createElement("span");
    title.className = "t-title";
    title.textContent = r.title || r.filename;
    const file = document.createElement("span");
    file.className = "t-file";
    file.textContent = r.filename;
    cell.append(title, file);

    const when = document.createElement("td");
    when.textContent = formatWhen(r.scheduled_publish);

    const badge = document.createElement("td");
    const span = document.createElement("span");
    span.className = "badge " + (r.published ? "ok" : "scheduled");
    span.textContent = r.published ? "Publiée" : "Programmée";
    badge.append(span);

    const link = document.createElement("td");
    if (r.video_id) {
      const a = document.createElement("a");
      a.href = `https://studio.youtube.com/video/${r.video_id}/edit`;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "Ouvrir";
      link.append(a);
    }

    tr.append(cell, when, badge, link);
    tbody.append(tr);
  });

  $("#history-empty").hidden = rows.length > 0;
  $("#count-history").textContent = state.history.length;
}

/* ---------------------------------------------------------------- réglages */

function renderSettings() {
  const s = state.settings;
  const select = $("#set-category");

  if (!select.options.length) {
    state.categories.forEach((c) => select.add(new Option(c.label, c.id)));
  }
  if (document.activeElement?.closest("#panel-settings")) return;

  $("#set-time").value = `${pad(s.post_hour)}:${pad(s.post_minute)}`;
  $("#set-interval").value = s.interval_days;
  select.value = s.category_id;
  $("#set-similar").value = s.similar_video_id || "";
  $("#set-description").value = s.description_template || "";
  $("#set-tags").value = (s.tags || []).join(", ");
  $("#set-autotitle").checked = !!s.auto_title;
  $("#set-kids").checked = !!s.made_for_kids;
  $("#set-channel").checked = !!s.show_channel_info;
}

const saveSettings = debounce(async (payload, note) => {
  try {
    state.settings = await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    if (note) toast(note);
    else toast("Réglages enregistrés", "ok");
    if ("show_channel_info" in payload) refresh();
  } catch (err) {
    toast(err.message, "error");
  }
}, 600);

function wireSettings() {
  $("#set-time").addEventListener("change", (e) => {
    const [h, m] = e.target.value.split(":").map(Number);
    saveSettings({ post_hour: h, post_minute: m });
  });
  $("#set-interval").addEventListener("change", (e) => saveSettings({ interval_days: Number(e.target.value) }));
  $("#set-category").addEventListener("change", (e) => saveSettings({ category_id: e.target.value }));
  $("#set-similar").addEventListener("input", (e) => saveSettings({ similar_video_id: e.target.value.trim() }));
  $("#set-description").addEventListener("input", (e) => saveSettings({ description_template: e.target.value }));
  $("#set-tags").addEventListener("input", (e) => saveSettings({ tags: e.target.value }));
  $("#set-autotitle").addEventListener("change", (e) => saveSettings({ auto_title: e.target.checked }));
  $("#set-kids").addEventListener("change", (e) => saveSettings({ made_for_kids: e.target.checked }));
  $("#set-channel").addEventListener("change", (e) =>
    saveSettings({ show_channel_info: e.target.checked },
      e.target.checked ? "Reconnectez le compte pour autoriser la lecture des infos de chaîne." : null));
  $("#btn-logout").addEventListener("click", logout);
}

/* ------------------------------------------------------------------ init */

function wireTabs() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
      $$(".panel").forEach((p) => p.classList.toggle("is-active", p.id === `panel-${tab.dataset.tab}`));
      $("#actionbar").style.display = tab.dataset.tab === "queue" ? "" : "none";
    });
  });
}

function init() {
  wireTabs();
  wireDropzone();
  wireSettings();

  $("#btn-connect").addEventListener("click", startAuth);
  $("#btn-account").addEventListener("click", () => (state.account.connected ? logout() : startAuth()));
  $("#btn-apply-sched").addEventListener("click", applySchedule);
  $("#btn-refresh").addEventListener("click", () => { refresh(); toast("Dossier analysé"); });
  $("#btn-start").addEventListener("click", startJob);
  $("#btn-cancel").addEventListener("click", async () => {
    await api("/api/job/cancel", { method: "POST" });
    toast("Annulation après la vidéo en cours…");
  });
  $("#history-search").addEventListener("input", renderHistory);

  $("#select-all").addEventListener("change", async (e) => {
    const value = e.target.checked;
    await Promise.all(state.queue.map((i) =>
      api(`/api/queue/item/${encodeURIComponent(i.filename)}`, {
        method: "POST", body: JSON.stringify({ enabled: value }),
      })));
    refresh();
  });

  refresh().then(() => { if (state.job.running) pollJob(); });
}

init();
