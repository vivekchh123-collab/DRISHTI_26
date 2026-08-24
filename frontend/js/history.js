/* Observed disaster history: the timeline screen.
 *
 * Draws real cyclone tracks from IBTrACS and curated flood, landslide and
 * cloudburst events over the map of India, with a year scrubber that plays
 * forward through the record.
 *
 * The two provenances are kept visually distinct on purpose. Cyclone geometry
 * is live from NOAA and needs no credentials; the flood and landslide rows are
 * curated by hand because no free API carries that history for India, and every
 * one of them shows its citation when opened. A viewer should never have to
 * guess which is which.
 */

const YEAR_MIN = 1990;

/* Colour per hazard, taken from the same tokens the rest of the app uses so a
 * hazard means the same colour on every screen. */
const HAZARD_TONE = {
  flood: "#2E7DB5",
  landslide: "#B45309",
  cyclone: "#7C3AED",
  cloudburst: "#0E9488",
  glof: "#0369A1",
};

/* Intensity bands for a track, so a super cyclonic storm reads heavier than a
 * depression without needing a legend. */
function trackWeight(kt) {
  if (kt >= 120) return 3.6;
  if (kt >= 90) return 2.8;
  if (kt >= 64) return 2.2;
  if (kt >= 34) return 1.6;
  return 1.1;
}

function trackTone(kt) {
  if (kt >= 120) return "#7E1F17";
  if (kt >= 90) return "#C4362C";
  if (kt >= 64) return "#E07B39";
  if (kt >= 34) return "#E3B23C";
  return "#9FB4C7";
}

export class HistoryLayer {
  constructor(L, map) {
    this.L = L;
    this.map = map;
    this.group = L.layerGroup();
    this.data = null;
    this.year = new Date().getFullYear();
    this.onSelect = null;
  }

  attach() {
    if (!this.map.hasLayer(this.group)) this.group.addTo(this.map);
  }

  detach() {
    this.group.clearLayers();
    if (this.map.hasLayer(this.group)) this.map.removeLayer(this.group);
  }

  load(data) {
    this.data = data;
    return this;
  }

  /* Everything at or before `year`, so scrubbing forward accumulates the record
   * rather than showing one year in isolation. A single year of Indian disaster
   * history is a handful of dots; the accumulated picture is the point. */
  setYear(year, windowYears = 0) {
    this.year = year;
    this.render(windowYears);
  }

  render(windowYears = 0) {
    if (!this.data) return;
    const L = this.L;
    this.group.clearLayers();

    const lo = windowYears > 0 ? this.year - windowYears + 1 : YEAR_MIN;
    const inRange = (y) => y >= lo && y <= this.year;

    for (const t of this.data.tracks || []) {
      const y = Number(String(t.start).slice(0, 4));
      if (!inRange(y)) continue;
      const pts = (t.points || [])
        .map((p) => [p[1], p[2]])
        .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]));
      if (pts.length < 2) continue;

      const recent = this.year - y;
      const line = L.polyline(pts, {
        color: trackTone(t.peak_wind_kt),
        weight: trackWeight(t.peak_wind_kt),
        opacity: Math.max(0.18, 0.92 - recent * 0.05),
        lineJoin: "round",
        className: "hist-track",
      });
      line.bindTooltip(
        `<b>${t.name || "Unnamed"}</b> ${t.season}<br>` +
        `${t.category}, peak ${Math.round(t.peak_wind_kt)} kt` +
        (t.landfall ? "<br>made landfall" : ""),
        { sticky: true });
      line.on("click", () => this.onSelect && this.onSelect({ kind: "track", track: t }));
      this.group.addLayer(line);
    }

    for (const e of this.data.events || []) {
      if (!inRange(e.year)) continue;
      const tone = HAZARD_TONE[e.hazard] || "#9FB4C7";
      const recent = this.year - e.year;
      /* Size by deaths, floored so a low-casualty event is still visible and
       * ceilinged so 1999 does not swallow the map. */
      const deaths = e.deaths || 0;
      const r = Math.max(5, Math.min(20, 5 + Math.sqrt(deaths) * 0.42));

      const m = L.circleMarker([e.lat, e.lon], {
        radius: r,
        color: tone,
        weight: 2,
        fillColor: tone,
        fillOpacity: Math.max(0.12, 0.6 - recent * 0.035),
        opacity: Math.max(0.3, 1 - recent * 0.035),
        className: "hist-event",
      });
      m.bindTooltip(
        `<b>${e.name}</b><br>${e.start}` +
        (deaths ? `<br>${deaths.toLocaleString("en-IN")} deaths` : ""),
        { sticky: true });
      m.on("click", () => this.onSelect && this.onSelect({ kind: "event", event: e }));
      this.group.addLayer(m);
    }
  }
}

/* --------------------------------------------------------------- panel */

function fmtNum(n) {
  return n === null || n === undefined ? "—" : Number(n).toLocaleString("en-IN");
}

export function renderHistoryPanel(root, data, year) {
  const events = (data.events || []).filter((e) => e.year <= year)
    .sort((a, b) => (b.start > a.start ? 1 : -1));
  const tracks = (data.tracks || []).filter(
    (t) => Number(String(t.start).slice(0, 4)) <= year);

  if (!events.length && !tracks.length) {
    root.innerHTML = `<p class="empty">No recorded events up to ${year}.</p>`;
    return;
  }

  const deaths = events.reduce((s, e) => s + (e.deaths || 0), 0);
  const landfalls = tracks.filter((t) => t.landfall).length;

  const rows = events.slice(0, 40).map((e) => `
    <button class="zone hist-row" data-event="${e.id}">
      <span class="zone-r mono">${e.year}</span>
      <span class="zone-n">${e.name}</span>
      <span class="zone-m mono">${e.deaths ? fmtNum(e.deaths) + " dead" : "—"}</span>
      <span class="tag tag-${e.hazard}">${e.hazard}</span>
    </button>`).join("");

  root.innerHTML = `
    <div class="hist-summary">
      <div class="ro"><span class="ro-k">Events</span>
        <span class="ro-v mono">${events.length}</span></div>
      <div class="ro"><span class="ro-k">Cyclone tracks</span>
        <span class="ro-v mono">${tracks.length}</span></div>
      <div class="ro"><span class="ro-k">Landfalls</span>
        <span class="ro-v mono">${landfalls}</span></div>
      <div class="ro"><span class="ro-k">Recorded deaths</span>
        <span class="ro-v mono">${fmtNum(deaths)}</span></div>
    </div>
    <p class="hist-note">
      Cyclone tracks are live from IBTrACS (NOAA), free and unauthenticated.
      Flood, landslide and cloudburst rows are curated with a citation on every
      entry, because no free API carries that history for India.
    </p>
    ${rows}`;
}

export function renderEventDetail(root, e) {
  const row = (k, v) => v === null || v === undefined || v === ""
    ? "" : `<div class="hkv"><span>${k}</span><b>${v}</b></div>`;
  root.innerHTML = `
    <div class="card">
      <h3>${e.name}</h3>
      <p class="muted">${e.start}${e.end !== e.start ? " to " + e.end : ""}
        &middot; ${e.hazard}</p>
      ${row("States", (e.states || []).join(", "))}
      ${row("Deaths", fmtNum(e.deaths))}
      ${row("People affected", fmtNum(e.affected))}
      ${row("Displaced", fmtNum(e.displaced))}
      ${e.note ? `<p class="note">${e.note}</p>` : ""}
      <p class="cite">Source: <a href="${e.source_url}" target="_blank"
        rel="noopener">${e.source}</a></p>
    </div>`;
}

// Only these hazards have a single testable coordinate — a landslide or a
// cloudburst starts somewhere specific; a flood or a cyclone's damage is
// basin- or coast-wide and has no one point to test a model against. Mirrors
// core/history.py's POINT_HAZARDS exactly, because the two framings below are
// not interchangeable and picking the wrong one would overclaim precision a
// flood verdict does not have.
const POINT_HAZARDS = ["landslide", "glof", "cloudburst"];

export function isPointTestable(hazard) {
  return POINT_HAZARDS.includes(hazard);
}

/** Honest, confidence-scaled copy for one point-hazard verdict.
 *
 *  The temptation with this feature is to always claim the model would have
 *  helped. It would not always have — Kotropi 2017 scores 52nd percentile,
 *  barely above the 50 a random point gets by construction, and the honest
 *  sentence for that case is a shrug, not a victory lap. Every tier below is
 *  scaled to what the number actually supports.
 */
function pointVerdictCopy(v) {
  if (!v || !v.inside_grid) {
    return { tone: "ok",
      text: "This event's location falls outside our analysis grid for the "
           + "district and cannot be tested against the model." };
  }
  if (v.in_red_zone) {
    return { tone: "severe",
      text: `This exact location was inside our modelled Red Zone for `
           + `${esc(v.dominant_hazard || "this hazard")}` +
           (v.return_period_years
             ? `, recurring roughly every ${Math.round(v.return_period_years)} years`
             : "") +
           ` — the kind of ground this system flags before an event, not after.` };
  }
  const pct = v.susceptibility_percentile;
  if (pct == null) {
    return { tone: "ok", text: "Susceptibility could not be computed for this location." };
  }
  if (pct >= 80) {
    return { tone: "alert",
      text: `This location scored in the ${ordinal(Math.round(pct))} percentile of `
           + `hazard susceptibility in the district — among the highest-risk ground `
           + `we model here — though it sat just outside the strict Red Zone line, `
           + `${v.distance_to_nearest_red_zone_km} km away.` };
  }
  if (pct >= 50) {
    return { tone: "watch",
      text: `This location's modelled susceptibility (${ordinal(Math.round(pct))} `
           + `percentile) was above the district average, but not sharply elevated `
           + `— a real signal, not a strong one.` };
  }
  return { tone: "ok",
    text: `Our model did not particularly flag this location — its susceptibility `
         + `sat at the ${ordinal(Math.round(pct))} percentile, at or below the `
         + `typical background for the district. We report that rather than omit it.` };
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** The "would this have helped" card, appended below the event's own detail.
 *
 *  ``ctx`` is one of:
 *   - null                                    — event has no modelled district
 *   - {kind:"point", verdict}                 — landslide/cloudburst/GLOF: a
 *                                                specific tested location
 *   - {kind:"area", districtName, redZone}     — flood/cyclone/other: no single
 *                                                point to test, so this reports
 *                                                the district's standing Red
 *                                                Zone coverage instead
 */
export function renderHistoricalContext(root, ctx) {
  if (!ctx) {
    root.innerHTML = "";
    return;
  }

  if (ctx.kind === "point") {
    const { tone, text } = pointVerdictCopy(ctx.verdict);
    root.innerHTML = `
      <div class="card hist-context" data-tone="${tone}">
        <h4>Would our model have flagged this?</h4>
        <p class="note">${text}</p>
        <p class="card-sub" style="margin-top:8px">
          Measured against the district's susceptibility background, the same
          method used to score landslide susceptibility maps generally. A
          random point scores the 50th percentile by construction.
        </p>
      </div>`;
    return;
  }

  const rz = ctx.redZone;
  root.innerHTML = `
    <div class="card hist-context" data-tone="watch">
      <h4>Would our model have flagged this?</h4>
      <p class="note">
        A flood or cyclone has no single point to test a model against — its
        damage is basin- or coast-wide. What we can report instead is the
        standing assessment: ${esc(ctx.districtName)}'s modelled Red Zone
        covers <b>${fmtNum(Math.round(rz.red_zone_area_km2))} km²</b>
        (${(rz.red_zone_fraction * 100).toFixed(0)}% of the district) —
        the ranked, evidence-based relocation priority list this system
        would have produced before this event, not after it.
      </p>
      <p class="card-sub" style="margin-top:8px">
        This is the district's current model, not a reconstruction of
        conditions on the day — the honest limit of what curated history
        without a gauged hindcast can support. See ASSUMPTIONS.md.
      </p>
    </div>`;
}

export function renderTrackDetail(root, t) {
  const first = t.points && t.points[0];
  const last = t.points && t.points[t.points.length - 1];
  root.innerHTML = `
    <div class="card">
      <h3>${t.name || "Unnamed storm"} &middot; ${t.season}</h3>
      <p class="muted">${t.category}</p>
      <div class="hkv"><span>Peak wind</span><b>${Math.round(t.peak_wind_kt)} kt</b></div>
      <div class="hkv"><span>Track points</span><b>${(t.points || []).length}</b></div>
      <div class="hkv"><span>Made landfall</span><b>${t.landfall ? "yes" : "no"}</b></div>
      ${first ? `<div class="hkv"><span>First fix</span><b class="mono">${first[0]}</b></div>` : ""}
      ${last ? `<div class="hkv"><span>Last fix</span><b class="mono">${last[0]}</b></div>` : ""}
      <p class="cite">Source: IBTrACS v04r01, NOAA NCEI — observed best track.
        Impact figures are not in IBTrACS and come from the curated register
        where the storm appears there.</p>
    </div>`;
}
