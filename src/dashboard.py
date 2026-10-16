"""Dashboard web FastAPI (LAN + Tailscale).

Fichier unique, HTML inline + JS vanilla (aucun build). Refonte « héros + board
compact », orientée mobile : répond d'abord à « où aller maintenant », puis un
tableau scannable avec barres proportionnelles et filtres.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Settings
from .service import KairosService

PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0f1420">
<title>Kairos · PortAventura</title>
<style>
  :root {
    --bg: #0f1420; --card: #1a2233; --card2: #222c42; --line: #2a3450;
    --txt: #e8edf7; --muted: #8b98b3;
    --green: #2ecc71; --amber: #f39c12; --red: #e74c3c; --accent: #4aa8ff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background: var(--bg); color: var(--txt);
         padding-bottom: calc(24px + env(safe-area-inset-bottom)); }
  a { color: var(--accent); }

  header.top { position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap;
    gap: 10px 16px; align-items: center; justify-content: space-between;
    padding: 12px 16px; background: rgba(15,20,32,.94); backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--line); }
  .brand { display: flex; flex-direction: column; gap: 6px; }
  h1 { margin: 0; font-size: 18px; letter-spacing: .3px; }
  h1 span { color: var(--accent); }
  .parks { display: flex; gap: 6px; flex-wrap: wrap; }
  .park { display: inline-flex; align-items: center; gap: 6px; background: var(--card2);
          padding: 3px 9px; border-radius: 999px; font-size: 12px; color: var(--muted); }
  .park .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .park.open .dot { background: var(--green); }
  .park.closing .dot { background: var(--amber); }
  .park.closed .dot { background: var(--red); }
  .park .pn { color: var(--txt); }
  .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .badge { background: var(--card2); padding: 4px 10px; border-radius: 999px; font-size: 12px; color: var(--muted); }
  .badge.phase { color: var(--txt); }
  select, .btn { background: var(--card2); color: var(--txt); border: 1px solid #33405f;
                 border-radius: 8px; padding: 7px 9px; font-size: 13px; }
  .btn { cursor: pointer; }
  .btn:disabled { opacity: .6; cursor: default; }
  .fresh { font-size: 12px; color: var(--muted); }
  .fresh.stale { color: var(--amber); }

  .banner { display: none; padding: 8px 16px; font-size: 12.5px; background: #3a2a10;
            color: #ffb84d; border-bottom: 1px solid #5a4520; cursor: pointer;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .banner.show { display: block; }
  .banner.expanded { white-space: normal; }

  main { max-width: 1000px; margin: 0 auto; padding: 16px; display: flex;
         flex-direction: column; gap: 20px; }

  .hero-card { background: linear-gradient(180deg, #1e2740, var(--card));
    border: 1px solid var(--line); border-left: 4px solid var(--accent);
    border-radius: 14px; padding: 16px; }
  .hero-card.empty { color: var(--muted); }
  .hero-rank { font-size: 12px; color: var(--muted); }
  .hero-name { font-size: 24px; font-weight: 800; margin: 2px 0 6px; }
  .hero-meta { font-size: 13px; color: var(--muted); display: flex; gap: 8px;
               align-items: center; flex-wrap: wrap; }
  .hero-meter { height: 6px; border-radius: 6px; background: #0c1220; margin: 12px 0 8px; overflow: hidden; }
  .hero-meter > span { display: block; height: 100%; border-radius: 6px; background: var(--green); }
  .hero-meter > span.amber { background: var(--amber); }
  .hero-meter > span.red { background: var(--red); }
  .hero-why { font-size: 13px; color: var(--muted); font-style: italic; }

  .alt { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
  .alt-row { display: grid; grid-template-columns: 28px 1fr auto auto auto; gap: 10px;
    align-items: center; background: var(--card); border: 1px solid #232e46;
    border-radius: 10px; padding: 8px 12px; }
  .alt-rank { color: var(--muted); }
  .alt-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .alt-zone { font-size: 12px; color: var(--muted); }
  .alt-wait { font-weight: 700; font-variant-numeric: tabular-nums; }

  .board { display: flex; flex-direction: column; gap: 12px; }
  .toolbar { position: sticky; top: var(--head-h, 0px); z-index: 6; display: flex; gap: 8px;
    align-items: center; margin: 0 -16px; padding: 8px 16px; background: var(--bg);
    border-bottom: 1px solid var(--line); overflow-x: auto; scrollbar-width: none; }
  .toolbar::-webkit-scrollbar { display: none; }
  .tabs, .chips { display: flex; gap: 6px; align-items: center; flex: 0 0 auto; }
  .sep { flex: 0 0 auto; width: 1px; height: 20px; background: var(--line); }
  #sortSelect { flex: 0 0 auto; }
  .tab, .chip { background: var(--card); border: 1px solid var(--line); color: var(--muted);
    border-radius: 999px; padding: 6px 12px; font-size: 13px; cursor: pointer; }
  .tab.active, .chip.active { background: var(--card2); color: var(--txt); border-color: var(--accent); }

  .park-sec { background: var(--card); border: 1px solid #232e46; border-radius: 12px; overflow: hidden; }
  .park-head { display: flex; justify-content: space-between; align-items: baseline; gap: 10px;
               padding: 12px 14px; border-bottom: 1px solid #232e46; }
  .park-head h2 { margin: 0; font-size: 16px; color: var(--accent); }
  .park-head .sum { font-size: 12px; color: var(--muted); text-align: right; }

  .row { position: relative; display: grid; grid-template-columns: 1fr auto auto; gap: 12px;
         align-items: center; padding: 9px 14px; border-bottom: 1px solid #1f2840; overflow: hidden; }
  .row:last-child { border-bottom: none; }
  .row .meter { position: absolute; left: 0; top: 0; bottom: 0; opacity: .13; }
  .row .meter.green { background: var(--green); }
  .row .meter.amber { background: var(--amber); }
  .row .meter.red { background: var(--red); }
  .rname { min-width: 0; display: flex; flex-direction: column; }
  .rname .cnm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rname .czone { font-size: 11px; color: var(--muted); }
  .rwait { justify-self: end; white-space: nowrap; }
  .rprio { min-width: 16px; text-align: center; }
  .row.is-closed { opacity: .55; }
  details.closed summary { cursor: pointer; padding: 9px 14px; color: var(--muted); font-size: 13px; }
  .empty { color: var(--muted); padding: 16px; text-align: center; }

  .w { font-weight: 700; font-variant-numeric: tabular-nums; }
  .w.green { color: var(--green); } .w.amber { color: var(--amber); } .w.red { color: var(--red); }
  .arrow.up { color: var(--red); } .arrow.down { color: var(--green); } .arrow.flat { color: var(--muted); }

  footer { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; padding: 16px;
           color: var(--muted); font-size: 12px; }
  .err { color: var(--red); }

  @media (max-width: 560px) {
    h1 { font-size: 16px; }
    main { padding: 12px; gap: 14px; }
    .toolbar { margin: 0 -12px; padding: 8px 12px; }
    .hero-card { padding: 14px; }
    .hero-name { font-size: 20px; }
    .alt-row { grid-template-columns: 24px 1fr auto auto; }
    .alt-row:nth-child(4) { display: none; }  /* 3 alternatives maxi sur mobile */
    .alt-zone { display: none; }
    .badge { font-size: 11px; padding: 3px 8px; }
    .row { padding: 9px 12px; }
  }
</style>
</head>
<body>
<header class="top">
  <div class="brand">
    <h1>🎢 Kairos <span>· PortAventura World</span></h1>
    <div class="parks" id="parks"></div>
  </div>
  <div class="controls">
    <span class="badge phase" id="phase">—</span>
    <span class="badge" id="mode">—</span>
    <select id="zoneSelect" title="Zone actuelle"></select>
    <button class="btn" id="refreshBtn" title="Forcer une actualisation">♻️</button>
    <span class="fresh" id="fresh">—</span>
  </div>
</header>
<div class="banner" id="banner"></div>

<main>
  <section class="hero">
    <div id="heroReco"></div>
    <div class="alt" id="altRecos"></div>
  </section>

  <section class="board">
    <div class="toolbar">
      <div class="tabs" id="parkTabs"></div>
      <span class="sep"></span>
      <div class="chips" id="filters">
        <button class="chip active" data-filter="all">Toutes</button>
        <button class="chip" data-filter="open">Ouvertes</button>
        <button class="chip" data-filter="p3">🔥 Priorité 3</button>
      </div>
      <span class="sep"></span>
      <select id="sortSelect">
        <option value="wait">Tri : attente</option>
        <option value="prio">Tri : priorité</option>
        <option value="zone">Tri : zone</option>
      </select>
    </div>
    <div id="board"></div>
  </section>
</main>

<footer>
  <span id="attr"></span>
  <span id="updated"></span>
  <span id="err" class="err"></span>
</footer>

<script>
const PHASE_LABELS = {
  opening: "Ouverture", peak: "Heures pleines (11h–15h)", afternoon: "Après-midi",
  closing: "Dernière heure", closed: "Fermé", before_open: "Avant ouverture", unknown: "Horaires inconnus"
};
const ARROW = { rising: ["↗", "up"], falling: ["↘", "down"], stable: ["→", "flat"] };
const CAT_ICON = { coaster: "🎢", thrill: "⚡", water: "💦", family: "🎠",
                   kids: "🧸", show: "🎭", walkthrough: "🚶", transport: "🚂" };

let THRESHOLD = 25;
let LATEST = null;
const ui = { park: "all", filter: "all", sort: "wait" };

function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function fmtWait(m) { return m == null ? "—" : m + " min"; }
function waitClass(m) {
  if (m == null) return "";
  if (m < THRESHOLD) return "green";
  if (m < THRESHOLD * 2) return "amber";
  return "red";
}
function barPct(m) { return m == null ? 0 : Math.max(4, Math.min(100, Math.round(m / 60 * 100))); }
function stars(p) { return p >= 3 ? "🔥" : ""; }
function phaseClass(phase) {
  if (phase === "closing") return "closing";
  if (phase === "opening" || phase === "peak" || phase === "afternoon") return "open";
  return "closed";
}

/* ------------------------------------------------------------- en-tête --- */

function renderParks(data) {
  document.getElementById("parks").innerHTML = (data.parks || []).map(p => {
    const cls = phaseClass(p.phase);
    let label = PHASE_LABELS[p.phase] || p.phase;
    if (p.phase === "closing" && p.minutes_to_close != null && p.minutes_to_close > 0)
      label += ` (${Math.round(p.minutes_to_close)} min)`;
    return `<span class="park ${cls}"><span class="dot"></span>` +
           `<span class="pn">${esc(p.name)}</span> <span class="ps">${esc(label)}</span></span>`;
  }).join("");
}

function syncHeadHeight() {
  const head = document.querySelector("header.top");
  document.documentElement.style.setProperty("--head-h", head.offsetHeight + "px");
}

function renderBanner(data) {
  const el = document.getElementById("banner");
  if (data.hours_warning) {
    el.textContent = "🗓️ " + data.hours_warning;
    el.title = data.hours_warning;
    el.classList.add("show");
  } else {
    el.classList.remove("show", "expanded");
    el.textContent = "";
    el.removeAttribute("title");
  }
  syncHeadHeight();
}

function renderZones(data) {
  const sel = document.getElementById("zoneSelect");
  const current = data.current_zone || "";
  sel.innerHTML = '<option value="">Zone : inconnue</option>' +
    data.zones.map(z => `<option value="${esc(z)}" ${z === current ? "selected" : ""}>${esc(z)}</option>`).join("");
}

/* ---------------------------------------------------------------- héros --- */

function renderHero(data) {
  const hero = document.getElementById("heroReco");
  const alt = document.getElementById("altRecos");
  const recos = data.recommendations || [];
  if (!recos.length) {
    hero.innerHTML = '<div class="hero-card empty">Aucune attraction ouverte pour l\u2019instant.</div>';
    alt.innerHTML = "";
    return;
  }
  const r = recos[0];
  const [a, cls] = ARROW[r.trend_label] || ["→", "flat"];
  hero.innerHTML = `<div class="hero-card">
    <div class="hero-rank">🥇 Meilleur choix</div>
    <div class="hero-name">${esc(r.name)}</div>
    <div class="hero-meta">
      <span class="w ${waitClass(r.wait_minutes)}">${fmtWait(r.wait_minutes)}</span>
      <span class="arrow ${cls}">${a}</span> · ${esc(r.zone)} · ${r.travel_minutes} min de marche
    </div>
    <div class="hero-meter"><span class="${waitClass(r.wait_minutes)}" style="width:${barPct(r.wait_minutes)}%"></span></div>
    <div class="hero-why">${esc(r.reason)}</div>
  </div>`;
  const medals = ["🥈", "🥉", "4.", "5."];
  alt.innerHTML = recos.slice(1, 5).map((x, i) => {
    const [aa, cc] = ARROW[x.trend_label] || ["→", "flat"];
    return `<div class="alt-row">
      <span class="alt-rank">${medals[i]}</span>
      <span class="alt-name">${esc(x.name)}</span>
      <span class="alt-zone">${esc(x.zone)}</span>
      <span class="alt-wait w ${waitClass(x.wait_minutes)}">${fmtWait(x.wait_minutes)}</span>
      <span class="arrow ${cc}">${aa}</span>
    </div>`;
  }).join("");
}

/* ---------------------------------------------------------------- board --- */

function sortRides(list) {
  const w = r => (r.wait_minutes == null ? -1 : r.wait_minutes);
  if (ui.sort === "prio") list.sort((a, b) => (b.priority - a.priority) || (w(b) - w(a)));
  else if (ui.sort === "zone") list.sort((a, b) => a.zone.localeCompare(b.zone) || (w(b) - w(a)));
  else list.sort((a, b) => w(b) - w(a));
}

function rowHtml(r, closed = false) {
  const [a, cls] = ARROW[r.trend_label] || ["→", "flat"];
  const icon = CAT_ICON[r.category] || "";
  const wc = r.is_open ? waitClass(r.wait_minutes) : "";
  const value = r.is_open ? `${fmtWait(r.wait_minutes)} <span class="arrow ${cls}">${a}</span>` : "fermé";
  const meter = r.is_open ? `<span class="meter ${wc}" style="width:${barPct(r.wait_minutes)}%"></span>` : "";
  return `<div class="row ${r.is_open ? "" : "is-closed"}">
    ${meter}
    <span class="rname"><span class="cnm">${icon} ${esc(r.name)}</span><span class="czone">${esc(r.zone)}</span></span>
    <span class="rwait ${wc}">${value}</span>
    <span class="rprio">${stars(r.priority)}</span>
  </div>`;
}

function renderBoard(data) {
  document.getElementById("parkTabs").innerHTML =
    `<button class="tab ${ui.park === "all" ? "active" : ""}" data-park="all">Tous</button>` +
    data.parks.map(p => `<button class="tab ${ui.park === p.key ? "active" : ""}" data-park="${esc(p.key)}">${esc(p.name)}</button>`).join("");

  const board = document.getElementById("board");
  let html = "";
  for (const park of data.parks) {
    if (ui.park !== "all" && park.key !== ui.park) continue;
    let rides = data.rides.filter(r => r.park === park.key);
    if (!rides.length) continue;
    if (ui.filter === "open") rides = rides.filter(r => r.is_open);
    if (ui.filter === "p3") rides = rides.filter(r => r.priority >= 3);
    const open = rides.filter(r => r.is_open);
    const closed = rides.filter(r => !r.is_open);
    sortRides(open);
    const avg = open.length ? Math.round(open.reduce((s, r) => s + (r.wait_minutes || 0), 0) / open.length) : null;
    const sum = (avg != null ? `moy <b>${avg} min</b> · ` : "") +
      `${open.length} ouvertes` + (closed.length ? ` · ${closed.length} fermées` : "");
    html += `<section class="park-sec">
      <div class="park-head"><h2>${esc(park.name)}</h2><span class="sum">${sum}</span></div>`;
    html += open.map(r => rowHtml(r)).join("");
    if (closed.length) {
      html += `<details class="closed"><summary>Fermées (${closed.length})</summary>` +
              closed.map(r => rowHtml(r, true)).join("") + `</details>`;
    }
    html += `</section>`;
  }
  board.innerHTML = html || '<div class="empty">Aucune attraction pour ce filtre.</div>';
}

/* ------------------------------------------------------------ fraîcheur --- */

function tickFresh() {
  const el = document.getElementById("fresh");
  if (!LATEST) { el.textContent = "—"; return; }
  const age = Math.max(0, Math.round((Date.now() - new Date(LATEST.generated_at).getTime()) / 1000));
  el.textContent = age < 60 ? `maj il y a ${age} s` : `maj il y a ${Math.round(age / 60)} min`;
  el.classList.toggle("stale", age > 600);
}
setInterval(tickFresh, 5000);

/* ----------------------------------------------------------------- load --- */

async function load() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    const data = await res.json();
    LATEST = data;
    THRESHOLD = data.alert_threshold ?? 25;
    document.getElementById("phase").textContent = PHASE_LABELS[data.phase] || data.phase;
    document.getElementById("mode").textContent = data.mode === "trip" ? "mode séjour" : "mode collecte";
    document.getElementById("updated").textContent = "MAJ " + new Date(data.generated_at).toLocaleTimeString();
    document.getElementById("err").textContent = data.last_error || "";
    document.getElementById("attr").innerHTML =
      `<a href="${data.attribution.url}" target="_blank" rel="noopener">${esc(data.attribution.text)}</a>`;
    renderZones(data); renderParks(data); renderBanner(data);
    renderHero(data); renderBoard(data); tickFresh();
  } catch (e) { document.getElementById("err").textContent = String(e); }
}

document.getElementById("parkTabs").addEventListener("click", e => {
  const btn = e.target.closest("[data-park]");
  if (btn) { ui.park = btn.dataset.park; renderBoard(LATEST); }
});
document.getElementById("filters").addEventListener("click", e => {
  const btn = e.target.closest("[data-filter]");
  if (btn) {
    ui.filter = btn.dataset.filter;
    document.querySelectorAll("#filters .chip").forEach(c => c.classList.toggle("active", c === btn));
    renderBoard(LATEST);
  }
});
document.getElementById("sortSelect").addEventListener("change", e => {
  ui.sort = e.target.value; renderBoard(LATEST);
});
document.getElementById("banner").addEventListener("click", e => e.currentTarget.classList.toggle("expanded"));
window.addEventListener("resize", syncHeadHeight);
window.addEventListener("load", syncHeadHeight);
syncHeadHeight();

document.getElementById("zoneSelect").addEventListener("change", async e => {
  await fetch("/api/zone", { method: "POST", headers: { "Content-Type": "application/json" },
                             body: JSON.stringify({ zone: e.target.value || null }) });
  load();
});
document.getElementById("refreshBtn").addEventListener("click", async e => {
  const btn = e.currentTarget;
  btn.disabled = true; const old = btn.textContent; btn.textContent = "⏳";
  try { await fetch("/api/refresh", { method: "POST" }); } catch (_) {}
  await load();
  btn.disabled = false; btn.textContent = old;
});

load();
setInterval(() => { if (!document.hidden) load(); }, 60000);
</script>
</body>
</html>
"""


def build_dashboard(service: KairosService, settings: Settings) -> FastAPI:
    app = FastAPI(title="Kairos", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(PAGE)

    @app.get("/api/state")
    async def state() -> JSONResponse:
        return JSONResponse(service.state_payload())

    @app.post("/api/refresh")
    async def refresh() -> JSONResponse:
        await service.poll_once()
        return JSONResponse({"ok": True, "last_poll": service.last_poll.isoformat() if service.last_poll else None})

    @app.post("/api/zone")
    async def set_zone(payload: dict) -> JSONResponse:
        zone = payload.get("zone")
        service.store.set_state("current_zone", zone or None)
        return JSONResponse({"ok": True, "zone": zone})

    @app.get("/healthz")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True, "last_poll": service.last_poll.isoformat() if service.last_poll else None})

    return app
