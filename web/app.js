"use strict";

const API_HEADERS = { "Content-Type": "application/json", "X-Plane-Overhead": "1" };
// Map marker shapes on a 24x24 grid, nose up. `shape` comes from the tracker
// (aircraft type database, falling back to the ADS-B category).
const SHAPES = {
  jet: { scale: 1, body: '<path d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z" transform="translate(.5 0)"/>' },
  heavy: { scale: 1.25, body: '<path d="M12 .5c-.9 0-1.6.9-1.6 2v6.3L.5 14.6v2.2l9.9-2.9v5.6l-2.7 2v1.9l4.3-1.1 4.3 1.1v-1.9l-2.7-2v-5.6l9.9 2.9v-2.2l-9.9-5.8V2.5c0-1.1-.7-2-1.6-2z"/><path d="M5.6 12.4h1.5v2.8H5.6zM16.9 12.4h1.5v2.8h-1.5z"/>' },
  bizjet: { scale: 0.9, body: '<path d="M12 2.5c-.6 0-1 .6-1 1.4V10l-6.5 3.8v1.5l6.5-1.6v4.2l-2.2 1.5v1.3l3.2-.7 3.2.7v-1.3L13 17.9v-4.2l6.5 1.6v-1.5L13 10V3.9c0-.8-.4-1.4-1-1.4z"/><path d="M9.2 14.6h1.6v3H9.2zM13.2 14.6h1.6v3h-1.6z"/>' },
  turboprop: { scale: 0.95, body: '<path d="M11 2.8c0-.7.4-1.3 1-1.3s1 .6 1 1.3V8.5h9.5v2.3L13 12v6.2l3 1.3v1.6l-4-.8-4 .8v-1.6l3-1.3V12l-9.5-1.2V8.5H11z"/><path d="M5.5 6h1.8v5.5H5.5zM16.7 6h1.8v5.5h-1.8z"/>' },
  light: { scale: 0.8, body: '<path d="M11.1 3.6h1.8v3.6H22v2.4l-9.1.7v7.4h3.6v2.1H7.5v-2.1h3.6v-7.4l-9.1-.7V7.2h9.1z"/><path d="M8.5 2.4h7v1.2h-7z"/>' },
  heli: { scale: 0.95, body: '<circle cx="12" cy="10" r="9.3" fill="none" stroke="currentColor" stroke-width="1.3" opacity=".6"/><path d="M12 5.5a3.2 3.2 0 0 1 3.2 3.2v3.6a3.2 3.2 0 0 1-2.2 3V21h2.4v1.6H8.6V21H11v-5.7a3.2 3.2 0 0 1-2.2-3V8.7A3.2 3.2 0 0 1 12 5.5z"/>' },
  fighter: { scale: 0.95, body: '<path d="M12 1l1.3 4.5.4 4.5 8.3 7.5v2l-8.2-2.6-.4 2.6 2.6 1.9V23L12 22l-4 1v-1.6l2.6-1.9-.4-2.6L2 19.5v-2L10.3 10l.4-4.5z"/>' },
  glider: { scale: 0.95, body: '<path d="M11.4 4h1.2v7h10.9v1.6l-10.9.6V19l3 .5v1.4H8.4v-1.4l3-.5v-5.8L.5 12.6V11h10.9z"/>' },
  balloon: { scale: 0.85, fixed: true, body: '<circle cx="12" cy="9.5" r="7.5"/><path d="M9.5 17.5h5l-1 2.5h-3z"/><rect x="10.3" y="20.3" width="3.4" height="2.7" rx=".5"/>' },
  drone: { scale: 0.75, body: '<path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.4"/><circle cx="6" cy="6" r="3.2"/><circle cx="18" cy="6" r="3.2"/><circle cx="6" cy="18" r="3.2"/><circle cx="18" cy="18" r="3.2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/>' },
  ground: { scale: 0.8, body: '<rect x="8" y="5" width="8" height="14" rx="2"/>' },
};
const SHAPE_NAMES = {
  jet: "Airliner", heavy: "Wide-body / heavy", bizjet: "Business or regional jet", turboprop: "Twin prop / turboprop",
  light: "Light single-engine", heli: "Helicopter", fighter: "Military jet", glider: "Glider / ultralight",
  balloon: "Balloon / airship", drone: "Drone", ground: "Ground vehicle",
};
const MI_TO_M = 1609.344;
const $ = (sel) => document.querySelector(sel);

let state = null;
let clockOffset = 0;      // server time minus browser time, in seconds
let selectedHex = null;
let selectedFromAlert = null;
let followSelected = false;
let picking = false;
let settingsLoaded = false;
let centeredOnce = false;
let pendingPan = false;   // a #plane= link arrived before the first data load
const markers = new Map(); // hex -> { marker, key }

// ---------- map ----------

const map = L.map("map", { zoomControl: true, worldCopyJump: true }).setView([39.5, -98.35], 4);
// OpenStreetMap tiles; dark mode is a CSS filter on the tile layer (see style.css).
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · flight data <a href="https://adsb.lol">adsb.lol</a>',
}).addTo(map);
map.on("zoomstart", () => $("#map").classList.add("zooming"));
map.on("zoomend", () => $("#map").classList.remove("zooming"));
map.on("dragstart", () => { followSelected = false; });

const homeMarker = L.marker([0, 0], {
  icon: L.divIcon({ className: "home-icon", html: "📍", iconSize: [24, 24], iconAnchor: [12, 22] }),
  keyboard: false, zIndexOffset: 1000,
}).bindTooltip("You", { direction: "top", offset: [0, -20] });
const passCircle = L.circle([0, 0], { radius: 1, color: "#dc2626", weight: 1.5, fillOpacity: 0.07, interactive: false });
const favCircle = L.circle([0, 0], { radius: 1, color: "#d97706", weight: 1, dashArray: "6 6", fill: false, interactive: false });
const trailLine = L.polyline([], { color: "#2563eb", weight: 3, opacity: 0.8, interactive: false }).addTo(map);
const futureLine = L.polyline([], { color: "#2563eb", weight: 2, dashArray: "4 8", opacity: 0.9, interactive: false }).addTo(map);
const cpaMarker = L.circleMarker([0, 0], { radius: 6, color: "#dc2626", weight: 2, fillOpacity: 0.3 });
const airportMarker = L.circleMarker([0, 0], { radius: 8, color: "#2563eb", weight: 2, fillOpacity: 0.15 });

map.on("click", (e) => {
  if (picking) return finishPick(e.latlng);
  if (selectedHex) select(null);
});

// ---------- helpers ----------

const nowSec = () => Date.now() / 1000 + clockOffset;
const fmtInt = (n) => Math.round(n).toLocaleString();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtClock(sec) {
  sec = Math.max(0, Math.round(sec));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}
function fmtAgo(t) {
  const d = nowSec() - t;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function fmtTime(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function fmtDuration(sec) {
  return sec < 60 ? `${sec} s` : `${sec / 60} min`;
}

/** Dead-reckon a plane forward from its last report so it moves smoothly between polls. */
function livePos(p, extraSec = 0) {
  const elapsed = Math.min(90, nowSec() - (state.ts - p.age)) + extraSec;
  if (p.on_ground || p.track == null || !p.gs) return [p.lat, p.lon];
  const nm = (p.gs / 3600) * elapsed;
  const trk = (p.track * Math.PI) / 180;
  const lat = p.lat + (nm * Math.cos(trk)) / 60;
  const lon = p.lon + (nm * Math.sin(trk)) / (60 * Math.cos((p.lat * Math.PI) / 180));
  return [lat, lon];
}

function planeColor(p) {
  if (p.hex === selectedHex) return "var(--accent)";
  if (p.candidate) return "var(--alert)";
  if (p.favorite) return "var(--fav)";
  if (p.on_ground) return "var(--ground)";
  return "var(--plane)";
}

function planeIcon(p) {
  const shape = SHAPES[p.shape] || SHAPES.jet;
  const base = p.on_ground ? 16 : p.hex === selectedHex || p.candidate || p.favorite ? 30 : 22;
  const size = Math.round(base * shape.scale);
  const turn = shape.fixed ? 0 : p.track || 0;
  const html = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" style="color:${planeColor(p)};transform:rotate(${turn}deg)">
    <g fill="currentColor" stroke="var(--surface)" stroke-width="0.8">${shape.body}</g></svg>`;
  return L.divIcon({ className: "plane-icon", html, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
}

const byHex = (hex) => state?.aircraft.find((p) => p.hex === hex);
const planeTitle = (p) => p.name + (p.type ? ` · ${p.type}` : "");

// ---------- data ----------

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: API_HEADERS, body: JSON.stringify(body) });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
}

async function refresh() {
  try {
    const want = selectedHex || selectedFromAlert?.hex;
    const s = await api("/api/state" + (want ? `?trail=${encodeURIComponent(want)}` : ""));
    clockOffset = s.now - Date.now() / 1000;
    state = s;
    render();
  } catch (e) {
    setStatus("err", "App is not responding. Is it running?");
  }
}

function setStatus(kind, text) {
  $("#status").className = `status ${kind}`;
  $("#status-text").textContent = text;
}

// ---------- render ----------

function render() {
  const s = state.settings;
  const located = s.latitude != null && s.longitude != null;

  if (state.error) setStatus("err", state.error);
  else if (state.paused_until) setStatus("ok", `Alerts paused · ${state.aircraft.length} planes tracked`);
  else if (state.quiet) setStatus("ok", `Quiet hours · ${state.aircraft.length} planes tracked`);
  else setStatus("ok", `Watching · ${state.aircraft.length} planes within range`);

  if (located) {
    const home = [s.latitude, s.longitude];
    homeMarker.setLatLng(home).addTo(map);
    passCircle.setLatLng(home).setRadius(s.pass_distance_mi * MI_TO_M).addTo(map);
    favCircle.setLatLng(home).setRadius(s.favorite_radius_mi * MI_TO_M);
    state.favorites.length ? favCircle.addTo(map) : favCircle.remove();
    if (!centeredOnce) {
      centeredOnce = true;
      if (!selectedHex) map.setView(home, 11);
    }
  } else if (!settingsLoaded) {
    showTab("settings");
  }

  if (!settingsLoaded) {
    fillSettings(s);
    settingsLoaded = true;
  }
  $("#fav-radius-hint").textContent = s.favorite_radius_mi;
  $("#autostart").checked = !!state.autostart;
  $("#paused").hidden = !state.paused_until;
  if (state.paused_until) $("#paused-until").textContent = fmtTime(state.paused_until);

  renderMarkers();
  renderLists();
  renderDetail();
  if (pendingPan && byHex(selectedHex)) {
    pendingPan = false;
    map.setView(livePos(byHex(selectedHex)), 11);
  }
}

function renderMarkers() {
  const seen = new Set();
  for (const p of state.aircraft) {
    seen.add(p.hex);
    const key = `${planeColor(p)}|${p.shape}|${Math.round(p.track || 0)}|${p.on_ground}|${p.candidate}|${p.favorite}|${p.hex === selectedHex}`;
    let m = markers.get(p.hex);
    if (!m) {
      const marker = L.marker(livePos(p), { icon: planeIcon(p), keyboard: false })
        .on("click", (e) => { L.DomEvent.stopPropagation(e); select(p.hex, false); })
        .addTo(map);
      m = { marker, key };
      markers.set(p.hex, m);
    } else if (m.key !== key) {
      m.marker.setIcon(planeIcon(p));
      m.key = key;
    }
    const z = p.hex === selectedHex ? 900 : p.candidate ? 800 : p.favorite ? 700 : p.on_ground ? 0 : 100;
    m.marker.setZIndexOffset(z);
    const labelled = p.hex === selectedHex || p.candidate || p.favorite;
    if (labelled && !m.marker.getTooltip()) {
      m.marker.bindTooltip(esc(p.name), { permanent: true, direction: "right", offset: [14, 0], className: "plane-label" });
    } else if (!labelled && m.marker.getTooltip()) {
      m.marker.unbindTooltip();
    }
  }
  for (const [hex, m] of markers) {
    if (!seen.has(hex)) { m.marker.remove(); markers.delete(hex); }
  }
}

function animate() {
  if (!state) return;
  for (const p of state.aircraft) markers.get(p.hex)?.marker.setLatLng(livePos(p));
  const p = byHex(selectedHex);
  if (p) {
    drawPaths(p);
    if (followSelected) map.panTo(livePos(p), { animate: true, duration: 0.9 });
  }
  tickCountdowns();
}

function tickCountdowns() {
  document.querySelectorAll("[data-eta]").forEach((el) => {
    el.textContent = fmtClock(Number(el.dataset.eta) - nowSec());
  });
}

function item({ cls = "", icon, title, sub, side = "", hex, extra = "" }) {
  return `<li class="item ${cls} ${hex && hex === selectedHex ? "selected" : ""}" ${hex ? `data-hex="${esc(hex)}"` : ""}>
    <span class="icon">${icon}</span>
    <span class="main"><div class="title">${title}</div><div class="sub">${sub}</div></span>
    ${side ? `<span class="side">${side}</span>` : ""}${extra}</li>`;
}
const empty = (text) => `<li class="empty">${text}</li>`;

function renderLists() {
  const s = state.settings;
  const upcoming = state.upcoming.map(byHex).filter(Boolean);
  $("#upcoming").innerHTML = upcoming.length
    ? upcoming.map((p) => item({
        cls: "overhead", icon: "✈", hex: p.hex, title: esc(planeTitle(p)),
        sub: `${fmtInt(p.pass.agl)} ft · within ${p.pass.miss_mi.toFixed(1)} mi`,
        side: `<span data-eta="${state.ts + p.pass.t}">${fmtClock(state.ts + p.pass.t - nowSec())}</span><small>away</small>`,
      })).join("")
    : empty(s.latitude == null ? "Set your location in Settings to start." : "Nothing low enough is headed your way right now.");

  const favLive = state.aircraft.filter((p) => p.favorite).sort((a, b) => a.dist_mi - b.dist_mi);
  $("#fav-live").innerHTML = favLive.length
    ? favLive.map((p) => item({
        cls: "fav", icon: "★", hex: p.hex, title: esc(planeTitle(p)),
        sub: p.on_ground ? "On the ground" : `${fmtInt(p.alt)} ft · ${fmtInt(p.gs)} kt`,
        side: `${p.dist_mi.toFixed(0)} mi`,
      })).join("")
    : empty(state.favorites.length ? "None of your favorites are in range." : "No favorites yet. Click a plane and press ☆.");

  const low = state.aircraft
    .filter((p) => !p.on_ground && p.agl > 0 && p.agl <= s.max_altitude_agl_ft && !p.candidate)
    .sort((a, b) => a.dist_mi - b.dist_mi).slice(0, 8);
  $("#nearby").innerHTML = low.length
    ? low.map((p) => item({
        icon: "✈", hex: p.hex, title: esc(planeTitle(p)),
        sub: p.landing ? `Landing at ${esc(p.landing.ident)} before reaching you`
          : `${fmtInt(p.agl)} ft above you · ${fmtInt(p.gs)} kt`,
        side: `${p.dist_mi.toFixed(1)} mi`,
      })).join("")
    : empty("No low planes nearby.");

  const icons = { overhead: "✈", takeoff: "🛫", in_range: "★" };
  $("#alerts").innerHTML = state.alerts.length
    ? state.alerts.map((a) => item({
        cls: a.kind === "overhead" ? "overhead" : "fav", icon: icons[a.kind] || "•", hex: a.hex,
        title: esc(a.title.replace(/^\S+\s/, "")), sub: esc(a.body),
        side: `${fmtTime(a.t)}<small>${fmtAgo(a.t)}</small>`,
      })).join("")
    : empty("No alerts yet. They'll show up here as they happen.");

  const alertBadge = state.alerts.filter((a) => nowSec() - a.t < 600).length;
  $('[data-tab="alerts"]').innerHTML = "Alerts" + (alertBadge ? `<span class="badge">${alertBadge}</span>` : "");

  const live = new Map(state.aircraft.filter((p) => p.favorite).map((p) => [p.favorite, p]));
  const kinds = { reg: "Tail number", flight: "Flight", hex: "ICAO" };
  $("#favorites").innerHTML = state.favorites.length
    ? state.favorites.map((f) => {
        const p = live.get(f.id);
        const seen = p ? (p.on_ground ? "On the ground nearby" : `In the air, ${p.dist_mi.toFixed(0)} mi away`)
          : f.last_seen ? `Last seen ${fmtAgo(f.last_seen.t)}` : "Not seen yet";
        return item({
          cls: "fav", icon: p ? "★" : "☆", hex: p?.hex,
          title: esc(f.label || f.value) + (f.type ? ` · ${esc(f.type)}` : ""),
          sub: `${kinds[f.kind]} ${esc(f.value)} · ${seen}`,
          extra: `<button class="remove" data-remove="${esc(f.id)}" title="Remove favorite" aria-label="Remove favorite">×</button>`,
        });
      }).join("")
    : empty("You haven't added any favorites.");
  tickCountdowns();
}

function renderDetail() {
  const card = $("#detail");
  const p = byHex(selectedHex);
  if (!p) {
    trailLine.setLatLngs([]); futureLine.setLatLngs([]); cpaMarker.remove(); airportMarker.remove();
    if (selectedFromAlert) {
      const a = state.alerts.find((x) => x.hex === selectedFromAlert.hex) || selectedFromAlert;
      if (state.trail?.hex === a.hex) trailLine.setLatLngs(state.trail.points.map((t) => [t[1], t[2]]));
      card.innerHTML = `<header><div><h3>${esc(a.name)}</h3><div class="meta">${esc([a.reg, a.type].filter(Boolean).join(" · "))}</div></div>
        <div class="actions"><button data-close title="Close" aria-label="Close">×</button></div></header>
        <div class="pass"><strong>No longer in range</strong>${esc(a.body)}<br><small>${fmtTime(a.t)}</small></div>
        <div class="links"><a href="https://globe.adsb.lol/?icao=${esc(a.hex)}" target="_blank">Find it on adsb.lol</a></div>`;
      card.hidden = false;
    } else {
      card.hidden = true;
    }
    return;
  }
  const s = state.settings;
  const fav = state.favorites.find((f) => f.id === p.favorite);
  const pass = p.pass;
  const land = p.landing;
  const landNote = land ? `${esc(land.name)}, ${(land.dist_nm * 1.15078).toFixed(1)} mi ahead of it` : "";
  let passHtml;
  if (p.on_ground) passHtml = `<div class="pass"><strong>On the ground</strong>${p.dist_mi.toFixed(1)} mi from you</div>`;
  else if (p.candidate) passHtml = `<div class="pass overhead"><strong>Overhead in <span data-eta="${state.ts + pass.t}">${fmtClock(pass.t)}</span></strong>
      About ${fmtInt(pass.agl)} ft above you, passing within ${pass.miss_mi.toFixed(2)} mi
      ${land ? `<br><small>Looks like it may land at ${esc(land.ident)} first (${landNote})</small>` : ""}</div>`;
  else if (land) passHtml = `<div class="pass landing"><strong>Landing at ${esc(land.ident)} before it reaches you</strong>
      ${landNote}. No alert for this one.</div>`;
  else if (pass && pass.t > 5) passHtml = `<div class="pass"><strong>Closest approach ${pass.miss_mi.toFixed(1)} mi</strong>
      in ${fmtClock(pass.t)} at about ${fmtInt(Math.max(0, pass.agl))} ft above you</div>`;
  else passHtml = `<div class="pass"><strong>Moving away from you</strong>${p.dist_mi.toFixed(1)} mi away now</div>`;

  const rate = p.rate > 100 ? `↑ ${fmtInt(p.rate)}` : p.rate < -100 ? `↓ ${fmtInt(-p.rate)}` : "Level";
  const flightFav = p.flight && state.favorites.some((f) => f.kind === "flight" && f.value === p.flight.toUpperCase());
  card.innerHTML = `
    <header>
      <div><h3>${esc(p.name)}</h3>
        <div class="type-name">${esc(p.type_name || (p.type ? `Type ${p.type}` : "Unknown aircraft type"))}</div>
        <div class="meta">${esc([p.reg !== p.name && p.reg, p.type, p.operator].filter(Boolean).join(" · ") || p.hex.toUpperCase())}</div></div>
      <div class="actions">
        <button data-fav="${fav ? "remove" : "hex"}" class="${fav ? "on" : ""}" title="${fav ? "Remove from favorites" : "Favorite this aircraft"}" aria-label="Favorite">${fav ? "★" : "☆"}</button>
        <button data-follow class="${followSelected ? "on" : ""}" title="Keep map centered on this plane" aria-label="Follow">⌖</button>
        <button data-close title="Close" aria-label="Close">×</button>
      </div>
    </header>
    ${passHtml}
    <dl>
      <div><dt>Altitude</dt><dd>${p.on_ground ? "Ground" : fmtInt(p.alt) + " ft"}</dd></div>
      <div><dt>Above you</dt><dd>${p.agl == null ? "—" : fmtInt(Math.max(0, p.agl)) + " ft"}</dd></div>
      <div><dt>Speed</dt><dd>${fmtInt(p.gs)} kt · ${fmtInt(p.gs * 1.15078)} mph</dd></div>
      <div><dt>Heading</dt><dd>${p.track == null ? "—" : fmtInt(p.track) + "°"}</dd></div>
      <div><dt>Vertical</dt><dd>${rate}${rate === "Level" ? "" : " ft/min"}</dd></div>
      <div><dt>Distance</dt><dd>${p.dist_mi.toFixed(1)} mi</dd></div>
      <div><dt>Squawk</dt><dd>${esc(p.squawk || "—")}</dd></div>
      <div><dt>ICAO</dt><dd>${esc(p.hex.toUpperCase())}</dd></div>
      <div class="wide"><dt>Aircraft</dt><dd>${esc(p.type_name || "Unknown")}${p.type ? ` (${esc(p.type)})` : ""} · ${SHAPE_NAMES[p.shape] || "Aircraft"}</dd></div>
    </dl>
    ${p.flight && !flightFav ? `<button class="fav-flight" data-fav="flight">☆ Favorite flight ${esc(p.flight)} (any aircraft)</button>` : ""}
    <div class="links">
      <a href="https://globe.adsb.lol/?icao=${esc(p.hex)}" target="_blank">adsb.lol</a>
      ${p.flight ? `<a href="https://www.flightaware.com/live/flight/${esc(p.flight)}" target="_blank">FlightAware</a>` : ""}
      ${p.reg ? `<a href="https://www.jetphotos.com/registration/${esc(p.reg)}" target="_blank">Photos</a>` : ""}
    </div>`;
  card.hidden = false;
  card.dataset.hex = p.hex;
  drawPaths(p);
}

function drawPaths(p) {
  const now = livePos(p);
  const pts = (state.trail?.hex === p.hex ? state.trail.points : []).map((t) => [t[1], t[2]]);
  trailLine.setLatLngs([...pts, now]);
  if (p.on_ground || p.track == null) { futureLine.setLatLngs([]); cpaMarker.remove(); return; }
  const ahead = Math.max(state.settings.lead_time_sec, (p.pass?.t || 0) + 60, 180);
  futureLine.setLatLngs([now, livePos(p, ahead)]);
  if (p.landing) {
    airportMarker.setLatLng([p.landing.lat, p.landing.lon]).bindTooltip(esc(`${p.landing.ident} · ${p.landing.name}`)).addTo(map);
  } else {
    airportMarker.remove();
  }
  if (p.pass && p.pass.t > 5 && p.pass.miss_mi < 10) {
    cpaMarker.setLatLng([p.pass.lat, p.pass.lon]).addTo(map);
  } else {
    cpaMarker.remove();
  }
}

// ---------- selection ----------

function select(hex, pan = true) {
  selectedHex = hex;
  selectedFromAlert = null;
  followSelected = false;
  if (hex) {
    if (!byHex(hex)) selectedFromAlert = state?.alerts.find((a) => a.hex === hex) || { hex, name: hex.toUpperCase(), body: "", t: nowSec() };
    history.replaceState(null, "", `#plane=${hex}`);
    const p = byHex(hex);
    if (p && pan) map.setView(livePos(p), Math.max(map.getZoom(), 10));
  } else {
    history.replaceState(null, "", location.pathname);
  }
  if (state) { renderMarkers(); renderLists(); renderDetail(); }
  refresh();
}

function selectFromHash() {
  const m = location.hash.match(/plane=([0-9a-z~]+)/i);
  if (m) { centeredOnce = true; pendingPan = !state; select(m[1].toLowerCase()); }
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-remove],[data-fav],[data-close],[data-follow],li.item[data-hex],[role=tab]");
  if (!t) return;
  if (t.dataset.remove) {
    e.stopPropagation();
    await api("/api/favorites/remove", { id: t.dataset.remove });
    return refresh();
  }
  if (t.dataset.fav) {
    const p = byHex(selectedHex);
    if (!p) return;
    if (t.dataset.fav === "remove") await api("/api/favorites/remove", { id: p.favorite });
    else if (t.dataset.fav === "flight") await api("/api/favorites", { kind: "flight", value: p.flight, type: "" });
    else await api("/api/favorites", { kind: "hex", value: p.hex, label: p.reg || p.flight, type: p.type });
    return refresh();
  }
  if (t.hasAttribute("data-close")) return select(null);
  if (t.hasAttribute("data-follow")) {
    followSelected = !followSelected;
    t.classList.toggle("on", followSelected);
    return;
  }
  if (t.getAttribute("role") === "tab") return showTab(t.dataset.tab);
  if (t.dataset.hex) select(t.dataset.hex);
});

function showTab(name) {
  document.querySelectorAll("[role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== `tab-${name}`; });
}

// ---------- favorites form ----------

$("#fav-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/favorites", { kind: f.get("kind"), value: f.get("value"), label: f.get("label") });
    e.target.reset();
    refresh();
  } catch (err) {
    alertInline(err.message);
  }
});
function alertInline(msg) {
  const hint = $("#fav-form .hint");
  const old = hint.innerHTML;
  hint.textContent = msg;
  setTimeout(() => { hint.innerHTML = old; }, 4000);
}

// ---------- settings ----------

const form = $("#settings");
const outputs = {
  pass_distance_mi: (v) => Number(v).toFixed(2).replace(/0$/, ""),
  max_altitude_agl_ft: (v) => fmtInt(v),
  lead_time_sec: (v) => fmtDuration(Number(v)),
  favorite_radius_mi: (v) => v,
  poll_interval_sec: (v) => v,
};

function fillSettings(s) {
  for (const el of form.elements) {
    if (!el.name || !(el.name in s)) continue;
    if (el.type === "checkbox") el.checked = !!s[el.name];
    else el.value = s[el.name] ?? "";
  }
  updateOutputs();
}
function updateOutputs() {
  form.querySelectorAll("output[data-for]").forEach((o) => {
    o.textContent = outputs[o.dataset.for](form.elements[o.dataset.for].value);
  });
}
function readSettings() {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.type === "number" || el.type === "range") out[el.name] = el.value === "" ? null : Number(el.value);
    else out[el.name] = el.value;
  }
  return out;
}

let saveTimer;
form.addEventListener("input", () => {
  updateOutputs();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveSettings, 400);
});
async function saveSettings() {
  const s = readSettings();
  if (s.latitude == null || s.longitude == null) return;
  await api("/api/settings", s);
  $("#saved").textContent = "Saved ✓";
  setTimeout(() => { $("#saved").textContent = ""; }, 2000);
  const moved = state && (state.settings.latitude !== s.latitude || state.settings.longitude !== s.longitude);
  if (moved) { centeredOnce = false; }
  setTimeout(refresh, 1500);
}

$("#pick-location").addEventListener("click", () => {
  picking = true;
  document.body.classList.add("picking");
  $("#pick-banner").hidden = false;
});
$("#pick-cancel").addEventListener("click", endPick);
function endPick() {
  picking = false;
  document.body.classList.remove("picking");
  $("#pick-banner").hidden = true;
}
async function finishPick(latlng) {
  endPick();
  form.elements.latitude.value = latlng.lat.toFixed(5);
  form.elements.longitude.value = latlng.lng.toFixed(5);
  try {
    const r = await fetch(`https://api.open-meteo.com/v1/elevation?latitude=${latlng.lat}&longitude=${latlng.lng}`);
    const meters = (await r.json()).elevation?.[0];
    if (meters != null) form.elements.elevation_ft.value = Math.round(meters * 3.28084);
  } catch { /* elevation stays as typed */ }
  centeredOnce = true;
  map.setView(latlng, Math.max(map.getZoom(), 11));
  saveSettings();
}

$("#test-notification").addEventListener("click", () => api("/api/test-notification", {}));

// ---------- app controls ----------

$("#autostart").addEventListener("change", (e) => api("/api/autostart", { enabled: e.target.checked }));
$("#quit").addEventListener("click", async () => {
  await api("/api/quit", {}).catch(() => {});
  document.body.innerHTML = '<p style="padding:24px">Plane Overhead has quit. You can close this window.</p>';
  window.close();
});
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-pause]");
  if (!b) return;
  await api("/api/pause", { minutes: Number(b.dataset.pause) });
  refresh();
});

// Called by the app when you click a notification while this window is open.
window.openPlane = (hex) => { centeredOnce = true; select(hex); };

window.addEventListener("hashchange", selectFromHash);
selectFromHash();
refresh();
setInterval(refresh, 3000);
setInterval(animate, 1000);
