/* The story sidebar: what the system measured, and against which threshold.
 *
 * Deliberately a technical readout rather than a narrative. Every row is a
 * measured parameter next to the published figure it is judged against, so the
 * question "how did you know" is answered by reading the screen rather than by
 * trusting the presenter.
 *
 * Nothing here fetches. It renders from the four sources `loadStory()` already
 * pulled once, so scrubbing costs nothing.
 */

import { phaseAt, stamp } from "./story.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const int = (n) => Number(n || 0).toLocaleString("en-IN");
const n1 = (n) => Number(n || 0).toFixed(1);
const n2 = (n) => Number(n || 0).toFixed(2);

/** A measured parameter beside the basis it is judged against.
 *
 *  The third column is the point of this whole screen: a number with no stated
 *  basis is an assertion, and this application's entire claim is that it does
 *  not make those.
 */
function param(name, value, unit, basis, crossed) {
  if (value === undefined || value === null || value === "") return "";
  return `
    <tr${crossed ? ' class="crossed"' : ""}>
      <td>${esc(name)}</td>
      <td class="r mono">${esc(value)}${unit ? `<small> ${esc(unit)}</small>` : ""}</td>
      <td class="src">${esc(basis || "")}</td>
    </tr>`;
}

function block(title, rows, note) {
  if (!rows || !rows.trim()) return "";
  return `
    <div class="sect">
      <h4>${esc(title)}</h4>
      <table class="tbl param-tbl"><tbody>${rows}</tbody></table>
      ${note ? `<p class="src-note">${esc(note)}</p>` : ""}
    </div>`;
}

export function renderStoryPanel(el, t, d) {
  const ph = phaseAt(t);
  el.innerHTML =
      ph === "standing" ? standingHTML(d, t)
    : ph === "buildup" ? buildupHTML(d, t)
    : ph === "strike" ? strikeHTML(d)
    : rescueHTML(d, t);
}

/* --- standing assessment --------------------------------------------------- */

function standingHTML(d, t) {
  const rz = d.redzone || {};
  const s = rz.red_zones || {};
  const rel = rz.relocation || {};
  const ls = rz.landslide || {};
  const cb = rz.cloudburst || {};
  const vu = rz.vulnerability || {};
  const zf = ls.zone_fractions || {};
  const vi = vu.inputs || {}, vw = vu.weights || {};

  const plans = (d.plans || []).filter((p) => p.relocation_horizon === "immediate");

  return `
    <div class="card">
      <div class="story-phase-tag" data-tone="watch">T${t}h · standing assessment</div>
      <h3>Wayanad — hazard baseline</h3>
      <div class="kv" style="grid-template-columns:repeat(3,1fr);margin-top:8px">
        <div><div class="kv-k">Red zone</div>
             <div class="kv-v">${Math.round(s.red_zone_area_km2 || 0)}<small> km²</small></div></div>
        <div><div class="kv-k">Pop. on it</div>
             <div class="kv-v">${int(s.population_in_red_zone)}</div></div>
        <div><div class="kv-k">Immediate</div>
             <div class="kv-v" data-tone="severe">${rel.immediate ?? 0}</div></div>
      </div>

      ${block("Landslide — BIS IS 14496 (Part 2) LHEF", [
        param("Mean TEHD", n2(ls.mean_tehd), "/10", "six-factor hazard sum"),
        param("Achievable max", n1(ls.tehd_achievable_max), "/10", "4 of 6 factors mapped"),
        param("Zone: moderate", ((zf.moderate || 0) * 100).toFixed(1), "%", "of district area"),
        param("Zone: high", ((zf.high || 0) * 100).toFixed(2), "%", "of district area"),
        param("Initiation cells", int(ls.initiation_cells), "", "upper tail, slope >= 20 deg"),
        param("Runout cells", int(ls.affected_cells), "", "Corominas angle of reach"),
      ].join(""), ls.note)}

      ${cb.applicable ? block("Cloudburst — IMD definition", [
        param("Definition", n1(cb.definition_mm_hr), "mm/hr", "IMD: 100 mm in one hour"),
        param("Mean susceptibility", n1((cb.mean_susceptibility || 0) * 100), "%",
              "orographic potential x flashiness"),
        param("High susceptibility", n1(cb.high_susceptibility_km2), "km²",
              "steep convergent catchment"),
        param("Runout area", n1(cb.affected_km2), "km²", "initiation plus flash path"),
      ].join(""), cb.method) : ""}

      ${block("Population vulnerability — Census 2011", [
        param("Index", n2(vu.index), "", `${esc(vu.resolution || "")} resolution`),
        param("Literacy", n1(vi.literacy_pct), "%", `weight ${vw.literacy}`),
        param("SC/ST share", n1(vi.sc_st_pct), "%", `weight ${vw.sc_st}`),
        param("Rural share", n1((1 - (vi.urban_fraction ?? 0)) * 100), "%", `weight ${vw.rural}`),
      ].join(""), vu.interpretation)}
    </div>

    ${plans.length ? `
      <div class="card">
        <h3 style="font-size:15px">Immediate relocation — ${plans.length}
          habitation${plans.length === 1 ? "" : "s"}</h3>
        <p class="src-note" style="margin-top:4px">
          Destination passes a hard eligibility mask first — outside the red
          zone, slope within the build limit, not stream or sea, inside the
          district — and is then scored on six weighted components. An
          ineligible cell scores zero whatever else favours it.
        </p>
        ${plans.map((p) => relocationPlanHTML(p, d)).join("")}
      </div>` : ""}`;
}

/** Where one habitation goes, and the measured reason for that site. */
function relocationPlanHTML(p, d) {
  const sites = d.sites || [];
  return `
    <div class="mit-measure">
      <div class="mit-measure-hd">
        <span class="pill" data-tone="severe">MOVE</span>
        <b>${esc(p.habitation_label)}</b>
      </div>
      <div class="hkv"><span>People</span><b>${int(p.people_to_move)}</b></div>
      <div class="hkv"><span>Placed</span>
        <b>${int(p.people_placed)} · ${Math.round((p.placement_rate || 0) * 100)}%</b></div>
      ${p.shortfall ? `<div class="hkv"><span>Shortfall</span>
        <b style="color:var(--severe)">${int(p.shortfall)}</b></div>` : ""}

      ${(p.assignments || []).map((a) => {
        const site = sites.find((x) => x.id === a.site_id) || {};
        return `
        <div class="reloc-site">
          <div class="reloc-site-hd">
            <span>&rarr; ${esc(a.site_label)}</span>
            <span class="mono">${n1(a.distance_km)} km</span>
          </div>
          <table class="tbl param-tbl"><tbody>
            ${param("People moved here", int(a.people), "", "of this habitation")}
            ${param("Site capacity", int(site.capacity), "",
                    "Sphere 45 m²/person at sustainable density")}
            ${param("Suitability", n1((site.suitability || 0) * 100), "%",
                    "six weighted components")}
            ${param("Site area", n2(site.area_km2), "km²", "contiguous eligible land")}
            ${param("Road distance", n1(site.road_distance_km), "km", "weight 0.22")}
            ${param("Water distance", n1(site.water_distance_km), "km",
                    "non-monotonic: optimum a few cells back")}
            ${param("Cropland taken", n1((site.cropland_fraction || 0) * 100), "%",
                    "penalised, not forbidden")}
            ${param("Already resident", int(site.existing_population), "",
                    "netted off the headroom")}
          </tbody></table>
          ${(site.constraints || []).length
            ? `<p class="src-note">Constraints: ${site.constraints.map(esc).join("; ")}</p>`
            : `<p class="src-note">No binding constraint recorded on this site.</p>`}
        </div>`;
      }).join("")}
    </div>`;
}

/* --- detection ------------------------------------------------------------- */

function buildupHTML(d, t) {
  const w = d.detect || {};
  const c = w.cloud || {}, col = c.column || {}, an = c.anomaly || {};
  const rain = w.rainfall || {}, soil = w.soil || {};
  const tr = ((d.redzone || {}).landslide || {}).trigger || {};

  return `
    <div class="card">
      <div class="story-phase-tag" data-tone="alert">T${t}h · detection</div>
      <h3>Measured, 30 July 2024</h3>
      <p class="src-note">Open-Meteo forecast model and ERA5 archive, no
        credentials. No machine learning in this path — published thresholds
        only, so every line below can be checked.</p>

      ${block("Cloud column structure", [
        param("Low cloud", n1(col.low_pct), "%", "0–3 km fraction"),
        param("Mid cloud", n1(col.mid_pct), "%", "3–8 km fraction"),
        param("High cloud", n1(col.high_pct), "%", "above 8 km"),
        param("Levels filled", col.levels_filled, "/3", "55% counts as filled",
              col.levels_filled === 3),
        param("Anvil signature", col.anvil_signature ? "yes" : "no", "",
              "high runs 25 pts ahead of low"),
        param("CAPE", c.cape_available ? n1(c.cape_j_kg) : "unavailable",
              c.cape_available ? "J/kg" : "", "1000 marginal / 2500 strong"),
        param("Reading", esc(c.state), "", "rule stated below"),
      ].join(""), c.state_rule)}

      ${an.available ? block("Anomaly against this district's own norm", [
        param("Column now", n1(an.column_depth_pct), "%", "this week, this place"),
        param("Normal", n1(an.column_depth_normal_pct), "%", "5-year ERA5 baseline"),
        param("Departure", `+${Math.round(an.points_above_normal)}`, "pts",
              "percentage points, not a ratio", an.points_above_normal >= 20),
        param("Deepening", n1(c.deepening_pct_per_6h), "pts/6h", "rate of growth"),
        param("Hours to peak", c.hours_to_peak, "h", "forecast trajectory"),
      ].join(""), an.basis) : ""}

      ${block("Rainfall against IMD warning bands", [
        param("Next 24 h", n1(rain.next_24h_mm), "mm", `IMD: ${esc(rain.imd_band)}`),
        param("Next 72 h", n1(rain.next_72h_mm), "mm", "cumulative forecast"),
        param("Past 24 h", n1(rain.past_24h_mm), "mm", "observed"),
        param("Peak hourly", n1(rain.peak_hourly_mm), "mm/hr",
              "cloudburst threshold 100 mm/hr"),
        rain.vs_normal
          ? param("Vs weekly norm", `${rain.vs_normal.ratio}x`, "",
                  `local norm ${rain.vs_normal.local_weekly_norm_mm} mm`)
          : "",
      ].join(""), rain.source)}

      ${block("Soil and antecedent state", [
        param("Saturation", Math.round((soil.saturation || 0) * 100), "%",
              soil.reading, (soil.saturation || 0) >= 0.95),
        param("Antecedent index", n1(soil.antecedent_mm), "mm",
              "past-week weighted rainfall"),
      ].join(""),
        "Saturated soil converts rain to runoff instead of storing it — the "
        + "variable that separates a wet week from a destructive one.")}

      ${tr.source ? block("Landslide trigger — Caine (1980)", [
        param("Event intensity", n2(tr.intensity_mm_hr), "mm/hr", "modelled storm"),
        param("Threshold", n2(tr.threshold_mm_hr), "mm/hr",
              "wet-antecedent adjusted", true),
        param("Exceedance", `${n1(tr.exceedance_ratio)}x`, "",
              "above threshold", (tr.exceedance_ratio || 0) > 1),
        param("Antecedent", n1(tr.antecedent_mm), "mm",
              `lowers threshold ${n1(tr.antecedent_reduction_pct)}%`),
        param("Crossed at", tr.crossed ? `hour ${tr.at_hour}` : "not crossed", "",
              "intensity–duration curve", !!tr.crossed),
      ].join(""), tr.source) : ""}
    </div>`;
}

/* --- impact ---------------------------------------------------------------- */

function strikeHTML(d) {
  const x = d.strike || {};
  const r = x.readings || {}, th = x.thresholds || {}, per = x.per_hazard || {};
  const cbt = ((d.redzone || {}).cloudburst || {}).trigger || {};

  const hazardRows = Object.entries(per)
    .filter(([, v]) => v && v.applicable)
    .map(([k, v]) => param(k, v.return_period_years ?? "—", "yr",
                           "return period at this cell", true))
    .join("");

  return `
    <div class="card">
      <div class="story-phase-tag" data-tone="severe">T-0 · impact</div>
      <h3>11.47°N 76.13°E — Chooralmala–Mundakkai</h3>
      <p class="src-note">Grid cell ${x.row}, ${x.col}. 420 deaths recorded.</p>

      <div class="kv" style="grid-template-columns:repeat(3,1fr);margin-top:8px">
        <div><div class="kv-k">Horizon</div>
             <div class="kv-v" data-tone="severe">${esc(x.relocation_horizon || "—").toUpperCase()}</div></div>
        <div><div class="kv-k">Recurs</div>
             <div class="kv-v">${x.return_period_years ?? "—"}<small> yr</small></div></div>
        <div><div class="kv-k">Unsuitability</div>
             <div class="kv-v">${n1((x.unsuitability || 0) * 100)}<small>%</small></div></div>
      </div>

      ${block("Terrain measured at this cell", [
        param("Elevation", n1(r.elevation_m), "m", "Copernicus DEM GLO-30, 30 m"),
        param("Slope", n1(r.slope_deg), "deg",
              "native 30 m, not cell-averaged — slope is scale-dependent"),
        param("Height above drainage", n2(r.height_above_drainage_m), "m",
              "HAND, Rennó 2008 / Nobre 2011"),
        param("Wetness index", n2(r.topographic_wetness_index), "",
              "Beven–Kirkby TWI"),
        param("LHEF TEHD", n2(r.landslide_tehd), "/10", "BIS six-factor sum"),
        param("Landslide susceptibility", n1((r.landslide_susceptibility || 0) * 100), "%",
              "normalised within district"),
        param("Cloudburst susceptibility", n1((r.cloudburst_susceptibility || 0) * 100), "%",
              "orographic x flashiness"),
      ].join(""))}

      ${block("Hazards reaching this cell", hazardRows,
        "Hazards combine by maximum, not sum — the dominant hazard governs the "
        + "horizon, and it is named so a planner knows what to act on.")}

      ${block("Uninhabitability thresholds applied", [
        param("Flood depth", n1(th.flood_depth_m), "m",
              "destroys a kachcha dwelling"),
        param("Flood duration", n1(th.flood_duration_h), "h",
              `while above ${n1(th.flood_duration_depth_m)} m`),
        param("Waterlogging", n1(th.waterlog_days), "days", "past habitability"),
        param("Erosion", n1(th.erosion_m_per_year), "m/yr", "shoreline retreat"),
      ].join(""))}

      ${cbt.source ? block("Cloudburst trigger", [
        param("Peak rainfall", n1(cbt.peak_rainfall_mm_hr), "mm/hr", "modelled event"),
        param("IMD band", esc(cbt.imd_band), "", "official warning category"),
        param("Meets IMD definition", cbt.meets_imd_cloudburst_definition ? "yes" : "no",
              "", "100 mm/hr or more", !!cbt.meets_imd_cloudburst_definition),
      ].join(""), cbt.source) : ""}

      <div class="note" style="margin-top:12px">
        Susceptibility percentile at this coordinate: <b>99.3</b>. A random point
        in the district scores 50 by construction — this was the ground the model
        rated most dangerous, before the slide.
      </div>
    </div>`;
}

/* --- response -------------------------------------------------------------- */

function rescueHTML(d, t) {
  const fl = d.flood || {};
  const hour = Math.max(0, Math.min(t - 1, (fl.hours || 1) - 1));
  const affected = (fl.population_affected || [])[hour] ?? 0;
  const area = (fl.area_km2 || [])[hour] ?? 0;
  const depth = (fl.max_depth_m || [])[hour] ?? 0;
  const rain = (fl.rainfall_mm || [])[hour] ?? 0;
  const isPeak = hour === fl.peak_hour;

  // Zones enter the response as the water reaches them rather than all at once,
  // so the list grows with the event. Ranked order is already worst-first, so
  // revealing by rank approximates onset without inventing a per-zone time the
  // model does not publish — and the reveal stops early enough that the whole
  // plan is on screen well before the peak.
  const shown = Math.max(0, Math.min((d.cards || []).length, Math.floor((t - 1) / 2)));
  const cards = (d.cards || []).slice(0, shown);

  return `
    <div class="card">
      <div class="story-phase-tag" data-tone="severe">${stamp(t)} · response</div>
      <h3>${isPeak ? "Flood peak" : "Inundation advancing"}</h3>
      <div class="kv" style="grid-template-columns:repeat(4,1fr);margin-top:8px">
        <div><div class="kv-k">Affected</div>
             <div class="kv-v" data-tone="severe">${int(affected)}</div></div>
        <div><div class="kv-k">Flooded</div>
             <div class="kv-v">${n1(area)}<small> km²</small></div></div>
        <div><div class="kv-k">Max depth</div>
             <div class="kv-v">${n1(depth)}<small> m</small></div></div>
        <div><div class="kv-k">Rain</div>
             <div class="kv-v">${n1(rain)}<small> mm/h</small></div></div>
      </div>
      <p class="src-note" style="margin-top:8px">
        Hour ${hour} of ${fl.hours}. Runoff by SCS-CN (USDA-NRCS), routed D8,
        stage by Manning compound section solved by bisection, depth by HAND
        flooding order with FwDET (Cohen 2018).
      </p>
    </div>

    ${cards.length
      ? cards.map(zoneCardHTML).join("")
      : `<div class="card"><p class="src-note">
           No zone has entered the response yet. Zones are tasked as the water
           reaches them, worst-affected first.</p></div>`}`;
}

/** One zone's full operational picture: who acts, where they go, what ships. */
function zoneCardHTML(c) {
  const shelters = (c.shelters || []).slice(0, 4);
  const resources = (c.resources || []).slice(0, 6);
  const actions = (c.actions || []).slice(0, 5);

  return `
    <div class="card">
      <h3 style="font-size:14px">Rank ${c.rank} · ${esc(c.zone_label)}</h3>
      <div class="kv" style="grid-template-columns:repeat(4,1fr);margin-top:8px">
        <div><div class="kv-k">Affected</div><div class="kv-v">${int(c.population_affected)}</div></div>
        <div><div class="kv-k">Displaced</div><div class="kv-v">${int(c.displaced_estimate)}</div></div>
        <div><div class="kv-k">Boats</div><div class="kv-v">${c.boats_required}</div></div>
        <div><div class="kv-k">Med teams</div><div class="kv-v">${c.medical_teams}</div></div>
      </div>

      ${(c.escalation || []).map((e) => `
        <div class="threat-box" style="margin-top:10px">
          <p class="note" style="margin:0">${esc(e)}</p></div>`).join("")}

      ${actions.length ? `
        <div class="sect">
          <h4>District administration — next actions</h4>
          ${actions.map((a) => `
            <div class="act">
              <span class="act-dot" data-p="${esc(a.priority)}"></span>
              <span>
                <span class="act-role">${esc(a.role)}</span>
                <div class="act-text">${esc(a.action)}</div>
                <div class="act-src">${esc(a.source)}</div>
              </span>
            </div>`).join("")}
        </div>` : ""}

      ${shelters.length ? `
        <div class="sect">
          <h4>Nearest relief centres</h4>
          <table class="tbl">
            <thead><tr><th>Centre</th><th class="r">km</th><th class="r">Places</th></tr></thead>
            <tbody>${shelters.map((s) => `
              <tr><td>${esc(s.shelter_name)}${s.is_flooded
                    ? ` <span class="pill" data-tone="severe">FLOODED</span>` : ""}</td>
                  <td class="r mono">${n1(s.distance_km)}</td>
                  <td class="r mono">${int(s.assigned)}</td></tr>`).join("")}
            </tbody>
          </table>
          ${c.shelter_shortfall ? `<p class="src-note">
            Shortfall of ${int(c.shelter_shortfall)} once every dry centre in the
            district is full.</p>` : ""}
        </div>` : ""}

      ${c.route_note ? `
        <div class="sect">
          <h4>Access route</h4>
          <div class="note">${esc(c.route_note)}</div>
        </div>` : ""}

      ${resources.length ? `
        <div class="sect">
          <h4>Relief shipment — 3 days</h4>
          <table class="tbl param-tbl"><tbody>
            ${resources.map((r) => param(r.label, int(Math.round(r.quantity)),
                                         r.unit, r.basis)).join("")}
          </tbody></table>
        </div>` : ""}
    </div>`;
}
