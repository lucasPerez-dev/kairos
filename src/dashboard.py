"""Dashboard web FastAPI (LAN + Tailscale)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Settings
from .service import KairosService

PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kairos · PortAventura</title>
<style>
  :root {
    --bg: #0f1420; --card: #1a2233; --card2: #222c42; --txt: #e8edf7;
    --muted: #8b98b3; --green: #2ecc71; --amber: #f39c12; --red: #e74c3c;
    --accent: #4aa8ff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background: var(--bg); color: var(--txt); }
  header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
           justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid #2a3450; }
  h1 { margin: 0; font-size: 20px; letter-spacing: .5px; }
  h1 span { color: var(--accent); }
  .badges { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .badge { background: var(--card2); padding: 4px 10px; border-radius: 999px; font-size: 12px; color: var(--muted); }
  .badge.phase { color: var(--txt); }
  select { background: var(--card2); color: var(--txt); border: 1px solid #33405f;
           border-radius: 8px; padding: 6px 8px; font-size: 13px; }
  main { padding: 20px; max-width: 1100px; margin: 0 auto; }
  .recos { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .reco { background: var(--card); border: 1px solid #2a3450; border-left: 4px solid var(--accent);
          border-radius: 12px; padding: 14px; }
  .reco .rank { font-size: 12px; color: var(--muted); }
  .reco .name { font-size: 17px; font-weight: 700; margin: 4px 0; }
  .reco .meta { font-size: 13px; color: var(--muted); }
  .reco .why { font-size: 12px; color: var(--muted); margin-top: 8px; font-style: italic; }
  .wait { font-weight: 700; }
  .grid section { margin-bottom: 24px; }
  .grid h2 { font-size: 15px; color: var(--accent); border-bottom: 1px solid #2a3450; padding-bottom: 6px; }
  .zone { margin-bottom: 14px; }
  .zone h3 { font-size: 13px; color: var(--muted); margin: 10px 0 6px; text-transform: uppercase; letter-spacing: .06em; }
  .ride { display: flex; align-items: center; justify-content: space-between; gap: 10px;
          background: var(--card); border: 1px solid #232e46; border-radius: 8px;
          padding: 8px 12px; margin-bottom: 5px; font-size: 14px; }
  .ride.done { opacity: .45; text-decoration: line-through; }
  .ride.closed { opacity: .55; }
  .ride .left { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .ride .left .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tag { font-size: 10px; padding: 1px 6px; border-radius: 999px; background: var(--card2); color: var(--muted); }
  .tag.p3 { background: #3a2a10; color: #ffb84d; }
  .w { min-width: 64px; text-align: right; font-variant-numeric: tabular-nums; }
  .w.green { color: var(--green); } .w.amber { color: var(--amber); } .w.red { color: var(--red); }
  .arrow.r { color: var(--red); } .arrow.f { color: var(--green); } .arrow.s { color: var(--muted); }
  footer { padding: 16px 20px; color: var(--muted); font-size: 12px; text-align: center; }
  footer a { color: var(--accent); }
  .err { color: var(--red); }
</style>
</head>
<body>
<header>
  <h1>🎢 Kairos <span>· PortAventura World</span></h1>
  <div class="badges">
    <span class="badge phase" id="phase">—</span>
    <span class="badge" id="mode">—</span>
    <select id="zoneSelect" title="Zone actuelle"></select>
    <span class="badge" id="poll">—</span>
  </div>
</header>
<main>
  <div class="recos" id="recos"></div>
  <div class="grid" id="grid"></div>
</main>
<footer>
  <span id="attr"></span> · MAJ <span id="updated">—</span> · <span id="err" class="err"></span>
</footer>
<script>
const PHASE_LABELS = {
  opening: "Ouverture", peak: "Heures pleines (11h–15h)", afternoon: "Après-midi",
  closing: "Dernière heure", closed: "Fermé", before_open: "Avant ouverture", unknown: "Horaires inconnus"
};
const ARROW = { rising: ["↗", "r"], falling: ["↘", "f"], stable: ["→", "s"] };
const CAT = { coaster: "coaster", thrill: "sensations", water: "eau", family: "familiale",
              kids: "enfants", show: "spectacle", walkthrough: "walk", transport: "transport" };

function waitClass(m) { if (m == null) return ""; if (m <= 15) return "green"; if (m <= 40) return "amber"; return "red"; }
function fmtWait(m) { return m == null ? "—" : m + " min"; }
function esc(s) { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function renderRecos(data) {
  const el = document.getElementById("recos");
  if (!data.recommendations.length) { el.innerHTML = '<div class="reco"><div class="name">Aucune attraction ouverte</div></div>'; return; }
  const medals = ["🥇", "🥈", "🥉", "4.", "5."];
  el.innerHTML = data.recommendations.map((r, i) => {
    const [a, cls] = ARROW[r.trend_label] || ["→", "s"];
    return `<div class="reco">
      <div class="rank">${medals[i] || "•"} · score ${r.score}</div>
      <div class="name">${esc(r.name)}</div>
      <div class="meta">${esc(r.zone)} · <span class="wait ${waitClass(r.wait_minutes)}">${fmtWait(r.wait_minutes)}</span>
        <span class="arrow ${cls}">${a}</span> · ${r.travel_minutes} min de marche</div>
      <div class="why">${esc(r.reason)}</div>
    </div>`;
  }).join("");
}

function renderGrid(data) {
  const el = document.getElementById("grid");
  let html = "";
  for (const park of data.parks) {
    const rides = data.rides.filter(r => r.park === park.key);
    if (!rides.length) continue;
    const zones = [...new Set(rides.map(r => r.zone))];
    html += `<section><h2>${esc(park.name)}</h2>`;
    for (const zone of zones) {
      const zr = rides.filter(r => r.zone === zone).sort((a,b) => (b.wait_minutes ?? -1) - (a.wait_minutes ?? -1));
      html += `<div class="zone"><h3>${esc(zone)}</h3>`;
      for (const r of zr) {
        const [a, cls] = ARROW[r.trend_label] || ["→", "s"];
        const classes = ["ride"]; if (r.done) classes.push("done"); if (!r.is_open) classes.push("closed");
        html += `<div class="${classes.join(" ")}">
          <div class="left"><span class="nm">${esc(r.name)}</span>
            <span class="tag ${r.priority === 3 ? "p3" : ""}">${CAT[r.category] || r.category}</span></div>
          <div class="w ${r.is_open ? waitClass(r.wait_minutes) : ""}">${r.is_open ? fmtWait(r.wait_minutes) : "fermé"}
            <span class="arrow ${cls}">${r.is_open ? a : ""}</span></div>
        </div>`;
      }
      html += `</div>`;
    }
    html += `</section>`;
  }
  el.innerHTML = html;
}

function renderZones(data) {
  const sel = document.getElementById("zoneSelect");
  const current = data.current_zone || "";
  sel.innerHTML = '<option value="">Zone : inconnue</option>' +
    data.zones.map(z => `<option value="${esc(z)}" ${z === current ? "selected" : ""}>${esc(z)}</option>`).join("");
}

async function load() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    const data = await res.json();
    document.getElementById("phase").textContent = PHASE_LABELS[data.phase] || data.phase;
    document.getElementById("mode").textContent = data.mode === "trip" ? "mode séjour" : "mode collecte";
    document.getElementById("poll").textContent = data.last_poll ? "poll " + new Date(data.last_poll).toLocaleTimeString() : "poll —";
    document.getElementById("updated").textContent = new Date(data.generated_at).toLocaleTimeString();
    document.getElementById("err").textContent = data.last_error || "";
    const attr = document.getElementById("attr");
    attr.innerHTML = `<a href="${data.attribution.url}" target="_blank" rel="noopener">${data.attribution.text}</a>`;
    renderZones(data); renderRecos(data); renderGrid(data);
  } catch (e) { document.getElementById("err").textContent = String(e); }
}

document.getElementById("zoneSelect").addEventListener("change", async (ev) => {
  await fetch("/api/zone", { method: "POST", headers: { "Content-Type": "application/json" },
                             body: JSON.stringify({ zone: ev.target.value || null }) });
  load();
});

load();
setInterval(load, 60000);
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

    @app.post("/api/zone")
    async def set_zone(payload: dict) -> JSONResponse:
        zone = payload.get("zone")
        service.store.set_state("current_zone", zone or None)
        return JSONResponse({"ok": True, "zone": zone})

    @app.get("/healthz")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True, "last_poll": service.last_poll.isoformat() if service.last_poll else None})

    return app
