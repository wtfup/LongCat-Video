/* WTF Avatar Factory — Approval Console (W2)
   Vanilla JS, zero dependencies, zero external resource references.
   Talks only to its own origin (the FastAPI app that serves it).

   XSS discipline: every dynamic value that reaches innerHTML goes through
   esc() (attribute-safe HTML escaping). The console's data source is the
   factory DB that only trusted factory components write to, and even so no
   raw job field is ever interpolated unescaped. */

"use strict";

const CHIPS = [
  { key: "all", label: "All" },
  { key: "gated", label: "Needs review" },
  { key: "rendered", label: "Rendered" },
  { key: "queued", label: "Queued" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
  { key: "published", label: "Published" },
  { key: "failed", label: "Failed" },
];

const STAT_CARDS = [
  { key: "queued", label: "Queued" },
  { key: "gated", label: "Awaiting review", cls: "is-gated" },
  { key: "approved", label: "Approved", cls: "is-approved" },
  { key: "rejected", label: "Rejected", cls: "is-rejected" },
  { key: "published", label: "Published" },
  { key: "failed", label: "Failed", cls: "is-failed" },
];

const DEFAULT_BRANDS = ["WTF Gyms", "WTF Franchise", "EVRYDAY", "Reboot", "Amplify", "WTF Academy", "DigiWTF"];

const state = {
  jobs: [],
  health: null,
  filter: "all",
  brand: "",
  q: "",
  openJobId: null,
  booted: false,
};

const $ = (sel) => document.querySelector(sel);

/* ------------------------------------------------------------------ utils */

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtTs(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function pretty(raw) {
  try { return JSON.stringify(JSON.parse(raw), null, 2); } catch (err) { return String(raw); }
}

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch (err) { body = null; }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : body;
    let msg = res.status + " " + res.statusText;
    if (typeof detail === "string") msg = detail;
    else if (detail && typeof detail === "object") msg = detail.error || detail.message || msg;
    const error = new Error(msg);
    error.status = res.status;
    error.body = body;
    throw error;
  }
  return body;
}

function toast(message, kind) {
  const el = document.createElement("div");
  el.className = "toast " + (kind === "err" ? "err" : "ok");
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4600);
}

function statusPill(status) {
  return '<span class="pill pill-' + esc(status) + '">' + esc(status) + "</span>";
}

function monogram(job) {
  const src = String(job.brand || "WTF").replace(/[^A-Za-z0-9 ]/g, "").trim();
  const letters = src.split(/\s+/).slice(0, 2).map((w) => (w[0] || "").toUpperCase());
  return esc(letters.join("")) || "WTF";
}

function hasLocalRender(job) {
  return Boolean(job.render_path) && String(job.render_path).indexOf("://") === -1;
}

/* ------------------------------------------------------------------ load */

async function loadAll(opts) {
  const silent = Boolean(opts && opts.silent);
  try {
    const [health, list] = await Promise.all([api("/health"), api("/jobs?limit=500")]);
    state.health = health;
    state.jobs = list.jobs || [];
    state.booted = true;
    renderHealth(null);
    renderStats();
    renderChips();
    renderBrandOptions();
    renderGrid();
    if (state.openJobId) await refreshDrawer(true);
  } catch (err) {
    renderHealth(err);
    if (!silent) toast("Failed to load: " + err.message, "err");
  }
}

/* ------------------------------------------------------------------ render */

function renderHealth(err) {
  const pill = $("#health-pill");
  if (err) {
    pill.className = "pill pill-failed";
    pill.textContent = "Console offline";
    pill.title = err.message;
    return;
  }
  const h = state.health || {};
  if (h.status === "ok") {
    pill.className = "pill pill-approved";
    pill.textContent = "Live · " + (h.jobs_total || 0) + " jobs";
    pill.title = h.db_path || "";
  } else {
    pill.className = "pill pill-failed";
    pill.textContent = "DB degraded";
    pill.title = h.error || "";
  }
}

function renderStats() {
  const by = (state.health && state.health.by_status) || {};
  $("#stats").innerHTML = STAT_CARDS.map((c) => {
    const n = by[c.key] || 0;
    return (
      '<div class="stat ' + (c.cls || "") + '">' +
      '<div class="num">' + n + "</div>" +
      '<div class="lbl">' + esc(c.label) + "</div>" +
      "</div>"
    );
  }).join("");
}

function renderChips() {
  const counts = {};
  state.jobs.forEach((j) => { counts[j.status] = (counts[j.status] || 0) + 1; });
  $("#status-chips").innerHTML = CHIPS.map((c) => {
    const n = c.key === "all" ? state.jobs.length : counts[c.key] || 0;
    const active = state.filter === c.key ? " active" : "";
    return (
      '<button type="button" class="chip' + active + '" data-chip="' + c.key + '">' +
      esc(c.label) + ' <span class="n">' + n + "</span></button>"
    );
  }).join("");
}

function allBrands() {
  const set = new Set(DEFAULT_BRANDS);
  state.jobs.forEach((j) => { if (j.brand) set.add(j.brand); });
  return Array.from(set).sort();
}

function renderBrandOptions() {
  const brands = allBrands();
  const select = $("#brand-filter");
  select.innerHTML =
    '<option value="">All brands</option>' +
    brands.map((b) => '<option value="' + esc(b) + '">' + esc(b) + "</option>").join("");
  select.value = state.brand;
  $("#brand-options").innerHTML = brands.map((b) => '<option value="' + esc(b) + '"></option>').join("");
}

function visibleJobs() {
  const q = state.q.trim().toLowerCase();
  return state.jobs.filter((j) => {
    if (state.filter !== "all" && j.status !== state.filter) return false;
    if (state.brand && j.brand !== state.brand) return false;
    if (q) {
      const hay = [j.id, j.title, j.brand, j.pillar].map((v) => String(v || "")).join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function cardHtml(job) {
  const gate = job.gate_score != null
    ? '<span class="tag gold">QA ' + Number(job.gate_score).toFixed(2) + "</span>"
    : "";
  const play = hasLocalRender(job)
    ? '<span class="play" aria-hidden="true"><svg viewBox="0 0 20 20" width="15" height="15"><path d="M6 4l10 6-10 6z" fill="currentColor"/></svg></span>'
    : "";
  const actions = job.status === "gated"
    ? '<button class="btn btn-ghost btn-sm" data-act="reject" data-id="' + esc(job.id) + '">Reject</button>' +
      '<button class="btn btn-gold btn-sm" data-act="approve" data-id="' + esc(job.id) + '">Approve</button>'
    : '<button class="btn btn-ghost btn-sm" data-act="open" data-id="' + esc(job.id) + '">Open</button>';

  return (
    '<article class="card">' +
      '<div class="thumb" data-act="open" data-id="' + esc(job.id) + '" title="Open preview">' +
        '<span class="monogram">' + monogram(job) + "</span>" +
        play +
        '<div class="badges">' + statusPill(job.status) + "</div>" +
      "</div>" +
      '<div class="card-body">' +
        '<div class="card-title">' + esc(job.title || "(untitled)") + "</div>" +
        '<div class="card-sub">' + esc(job.id) + "</div>" +
        '<div class="card-meta">' +
          '<span class="tag">' + esc(job.brand) + "</span>" +
          '<span class="tag">' + esc(job.pillar) + "</span>" +
          '<span class="tag">' + esc(job.language) + "</span>" +
          gate +
        "</div>" +
        '<div class="card-actions">' + actions + "</div>" +
      "</div>" +
    "</article>"
  );
}

function renderGrid() {
  const jobs = visibleJobs();
  const grid = $("#grid");
  const empty = $("#empty");

  if (!state.booted) {
    grid.innerHTML = Array.from({ length: 6 }, () => '<div class="skeleton"></div>').join("");
    return;
  }
  if (!jobs.length) {
    grid.innerHTML = "";
    empty.hidden = false;
    const anyJobs = state.jobs.length > 0;
    $("#empty-title").textContent = anyJobs ? "Nothing in this view" : "Queue is clear";
    $("#empty-copy").textContent = anyJobs
      ? "Try another status chip, brand or search term."
      : "New renders will appear here as the factory completes them.";
    return;
  }
  empty.hidden = true;
  grid.innerHTML = jobs.map(cardHtml).join("");
}

/* ------------------------------------------------------------------ drawer */

function drawerNote(reason) {
  const notes = {
    no_render_path: "No render yet — this job has not reached the render stage.",
    file_missing: "Render file is missing on disk.",
    outside_media_root: "Render path escapes the media root — refused by policy.",
    remote_reference: "Remote reference — the console never proxies external files.",
    unresolvable_path: "Render path could not be resolved.",
  };
  return notes[reason] || "Preview unavailable.";
}

function decisionNote(status) {
  const notes = {
    queued: "Decisions unlock once the QA gate marks this job 'gated'.",
    rendering: "Rendering in progress — decisions unlock at 'gated'.",
    rendered: "Awaiting QA gate — decisions unlock at 'gated'.",
    approved: "Approved — ready for the publish queue.",
    rejected: "Rejected — job closed. Queue a new job to re-cut.",
    published: "Published.",
    failed: "Render failed — check pipeline logs.",
  };
  return notes[status] || "";
}

async function openDrawer(id) {
  state.openJobId = id;
  const drawer = $("#drawer");
  drawer.hidden = false;
  $("#scrim").hidden = false;
  document.body.classList.add("drawer-open");
  drawer.innerHTML = '<div class="drawer-body"><div class="skeleton" style="min-height:320px"></div></div>';
  await refreshDrawer(false);
}

function closeDrawer() {
  state.openJobId = null;
  $("#drawer").hidden = true;
  $("#scrim").hidden = $("#modal").hidden;
  document.body.classList.remove("drawer-open");
}

async function refreshDrawer(silent) {
  const id = state.openJobId;
  if (!id) return;
  try {
    const job = await api("/jobs/" + encodeURIComponent(id));
    if (state.openJobId === id) renderDrawer(job);
  } catch (err) {
    if (state.openJobId === id) closeDrawer();
    if (!silent) toast("Could not open job: " + err.message, "err");
  }
}

function renderDrawer(job) {
  const events = job.events || [];
  const timeline = events.map((e) => {
    let who = "";
    let label = e.event;
    if (e.meta) {
      try {
        const m = JSON.parse(e.meta);
        if (m && m.to) label = "status → " + m.to;
        if (m && (m.by || m.source)) who = " · " + esc(m.by || m.source);
      } catch (err) { who = ""; }
    }
    return (
      '<li><div class="t-ev">' + esc(label) + '</div>' +
      '<div class="t-ts">' + esc(fmtTs(e.ts)) + who + "</div></li>"
    );
  }).join("");

  const player = job.render_available
    ? '<video class="player" controls preload="metadata" src="/jobs/' + encodeURIComponent(job.id) + '/render"></video>'
    : '<div class="player-off">' + esc(drawerNote(job.render_note)) + "</div>";

  const gateCard = job.gate_score != null
    ? '<div class="gate-card"><div><div class="sec-label" style="margin-bottom:2px">QA gate score</div>' +
      '<div class="gate-note">Written by the QA gate (W7)</div></div>' +
      '<div class="gate-score">' + Number(job.gate_score).toFixed(2) + "</div></div>"
    : '<div class="gate-card"><div class="gate-note">' + esc(decisionNote(job.status)) + "</div></div>";

  const gateJson = job.gate_json
    ? '<div><div class="sec-label">Gate detail</div><pre class="block">' + esc(pretty(job.gate_json)) + "</pre></div>"
    : "";

  const notes = job.notes
    ? '<div><div class="sec-label">Notes</div><pre class="block">' + esc(job.notes) + "</pre></div>"
    : "";

  const actions = job.status === "gated"
    ? '<input id="reject-reason" class="input" placeholder="Reject reason (optional)" />' +
      '<div class="decide-row">' +
      '<button class="btn btn-maroon" data-act="reject" data-id="' + esc(job.id) + '">Reject</button>' +
      '<button class="btn btn-gold" data-act="approve" data-id="' + esc(job.id) + '">Approve</button>' +
      "</div>"
    : '<div class="decided-note">' + esc(decisionNote(job.status)) + "</div>";

  $("#drawer").innerHTML =
    '<div class="drawer-head">' +
      "<div><h2>" + esc(job.title || "(untitled)") + "</h2>" +
      '<div class="sub">' + esc(job.id) + " · " + esc(job.brand) + " · " + esc(job.pillar) + " · " + esc(job.language) + "</div></div>" +
      '<button class="btn btn-ghost btn-icon" data-act="close-drawer" aria-label="Close">✕</button>' +
    "</div>" +
    '<div class="drawer-body">' +
      player + gateCard + gateJson +
      (job.script ? '<div><div class="sec-label">Script</div><pre class="block">' + esc(job.script) + "</pre></div>" : "") +
      notes +
      '<div><div class="sec-label">History</div><ul class="timeline">' + timeline + "</ul></div>" +
    "</div>" +
    '<div class="drawer-actions">' + statusPill(job.status) + actions + "</div>";
}

/* ------------------------------------------------------------------ decide */

async function decide(id, kind) {
  const isReject = kind === "reject";
  const verb = isReject ? "Reject" : "Approve";
  if (!window.confirm(verb + " " + id + "?")) return;
  let payload = {};
  if (isReject) {
    const input = document.getElementById("reject-reason");
    const reason = input && input.value.trim();
    if (reason) payload = { reason: reason };
  }
  try {
    const res = await api("/jobs/" + encodeURIComponent(id) + "/" + kind, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast(
      (res.changed ? verb + "d " : "Already " + res.status + ": ") + id,
      "ok"
    );
    await loadAll({ silent: true });
    if (state.openJobId === id) await refreshDrawer(true);
  } catch (err) {
    toast("Decision failed: " + err.message, "err");
  }
}

/* ------------------------------------------------------------------ modal */

function openModal() {
  $("#modal").hidden = false;
  $("#scrim").hidden = false;
  document.body.classList.add("drawer-open");
}

function closeModal() {
  $("#modal").hidden = true;
  $("#scrim").hidden = $("#drawer").hidden;
  if ($("#drawer").hidden) document.body.classList.remove("drawer-open");
}

async function submitNewJob(ev) {
  ev.preventDefault();
  const form = ev.target;
  const data = Object.fromEntries(new FormData(form).entries());
  const body = {
    brand: String(data.brand || "").trim(),
    pillar: data.pillar,
    language: data.language,
  };
  if (String(data.title || "").trim()) body.title = String(data.title).trim();
  if (String(data.script || "").trim()) body.script = data.script;
  try {
    const job = await api("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    toast("Queued " + job.id, "ok");
    closeModal();
    form.reset();
    await loadAll({ silent: true });
  } catch (err) {
    toast("Could not queue: " + err.message, "err");
  }
}

/* ------------------------------------------------------------------ boot */

function boot() {
  $("#btn-refresh").addEventListener("click", () => loadAll());
  $("#btn-new").addEventListener("click", openModal);
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal-cancel").addEventListener("click", closeModal);
  $("#new-job-form").addEventListener("submit", submitNewJob);
  $("#search").addEventListener("input", (ev) => { state.q = ev.target.value; renderGrid(); });
  $("#brand-filter").addEventListener("change", (ev) => { state.brand = ev.target.value; renderGrid(); });

  document.addEventListener("click", (ev) => {
    const action = ev.target.closest("[data-act]");
    if (action) {
      const act = action.dataset.act;
      const id = action.dataset.id;
      if (act === "open" && id) openDrawer(id);
      else if (act === "approve" && id) decide(id, "approve");
      else if (act === "reject" && id) decide(id, "reject");
      else if (act === "close-drawer") closeDrawer();
      return;
    }
    const chip = ev.target.closest("[data-chip]");
    if (chip) {
      state.filter = chip.dataset.chip;
      renderChips();
      renderGrid();
      return;
    }
    if (ev.target === $("#scrim")) { closeDrawer(); closeModal(); }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { closeDrawer(); closeModal(); }
  });

  renderStats();
  renderChips();
  renderGrid();
  loadAll();
  setInterval(() => loadAll({ silent: true }), 15000);
}

boot();
