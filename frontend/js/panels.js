/* Right-hand rail: statistics, the ranked zone list, and the action card.
 *
 * Rendering is string-based rather than node-diffing, which is the right
 * trade at this scale — the whole rail is at most a few hundred nodes and
 * rebuilding it is imperceptible, while a hand-rolled diff would be a source
 * of bugs for no gain. Everything user-supplied is escaped on the way in.
 */

import { fmt, depthTone, HORIZON_TONE } from "./api.js";

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* --- states -------------------------------------------------------------- */

export function skeleton(rows = 6) {
  return Array.from({ length: rows },
    () => `<div class="skel skel-row"></div>`).join("");
}

export function empty(title, body) {
  return `<div class="empty"><strong>${esc(title)}</strong><p>${esc(body)}</p></div>`;
}

export function errorBox(err) {
  const hint = err.status === 0
    ? "The server is not responding. Check that it is running, then retry."
    : "Try another district, or reload the page.";
  return `<div class="err"><strong>${esc(err.message)}</strong>${esc(hint)}</div>`;
}

/* --- district statistics ------------------------------------------------- */

export function renderStats(el, summary, impact) {
  const pctTone = impact.population_affected_pct >= 25 ? "severe"
                : impact.population_affected_pct >= 10 ? "alert" : "";
  el.innerHTML = `
    <div class="stat">
      <div class="stat-k">People affected</div>
      <div class="stat-v" ${pctTone ? `data-tone="${pctTone}"` : ""}>
        ${fmt.compact(impact.population_affected)}
        <small>${fmt.pct(impact.population_affected_pct)}</small>
      </div>
    </div>
    <div class="stat">
      <div class="stat-k">Area flooded</div>
      <div class="stat-v">${fmt.num(impact.area_flooded_km2, 0)}<small>km²</small></div>
    </div>
    <div class="stat">
      <div class="stat-k">Settlements cut off</div>
      <div class="stat-v" ${impact.settlements_cut_off ? 'data-tone="severe"' : ""}>
        ${impact.settlements_cut_off}
      </div>
    </div>
    <div class="stat">
      <div class="stat-k">Max depth</div>
      <div class="stat-v">${fmt.num(summary.flood.max_depth_m, 1)}<small>m</small></div>
    </div>`;
}

/* --- ranked zone list ---------------------------------------------------- */

export function renderZones(el, zones, selectedId) {
  if (!zones.length) {
    el.innerHTML = empty("No flooding modelled",
      "This district shows no inundation above 15 cm for the selected severity.");
    return;
  }
  el.innerHTML = zones.map((z) => {
    const tone = z.is_cut_off ? "severe" : depthTone(z.max_depth_m);
    return `
    <button class="zone" data-zone="${esc(z.id)}"
            aria-current="${z.id === selectedId}">
      <span class="zone-rank">${z.rank}</span>
      <span class="zone-main">
        <span class="zone-label">${esc(z.label)}</span>
        <span class="zone-meta">
          ${fmt.int(z.population_affected)} affected · ${fmt.m(z.max_depth_m)} · ${fmt.hours(z.duration_hours)}
        </span>
      </span>
      <span class="zone-score">
        ${fmt.num(z.score, 0)}
        ${z.is_cut_off ? `<span class="pill" data-tone="severe">cut off</span>` : ""}
      </span>
    </button>`;
  }).join("");
}

/* --- action card --------------------------------------------------------- */

export function renderActionCard(card, zone) {
  if (!card) return empty("No action card", "Select a zone from the ranked list.");

  const shelters = card.shelters.length
    ? `<table class="tbl">
         <thead><tr><th>Relief centre</th><th class="r">km</th><th class="r">Places</th></tr></thead>
         <tbody>${card.shelters.map((s) => `
           <tr><td>${esc(s.shelter_name)}</td>
               <td class="r">${fmt.num(s.distance_km, 1)}</td>
               <td class="r">${fmt.int(s.assigned)}</td></tr>`).join("")}
         </tbody>
       </table>
       <p class="card-sub" style="margin-top:8px">
         ${card.shelters_used} centre${card.shelters_used === 1 ? "" : "s"} allocated
         of the district's dry capacity.
       </p>`
    : `<div class="note">No dry relief centre has spare capacity within reach of
        this zone.</div>`;

  const escalation = card.escalation.length
    ? `<div class="escal">${card.escalation.map((e) => `<p>${esc(e)}</p>`).join("")}</div>`
    : "";

  const actions = card.actions.map((a) => `
    <div class="act">
      <span class="act-dot" data-p="${esc(a.priority)}"></span>
      <span>
        <span class="act-role">${esc(a.role)}</span>
        <div class="act-text">${esc(a.action)}</div>
        <div class="act-src">${esc(a.source)}</div>
      </span>
    </div>`).join("");

  const supplies = card.resources.slice(0, 6).map((r) => `
    <tr><td>${esc(r.label)}</td>
        <td class="r">${fmt.int(Math.round(r.quantity))}</td>
        <td>${esc(r.unit)}</td></tr>`).join("");

  return `
  <div class="card">
    <h3>${esc(zone.label)}</h3>
    <div class="card-sub">
      RANK ${zone.rank} · SCORE ${fmt.num(zone.score, 0)} ·
      ${esc(zone.depth_band).toUpperCase()} · ${esc(zone.road_status).toUpperCase()}
    </div>

    <div class="kv">
      <div><div class="kv-k">Affected</div><div class="kv-v">${fmt.compact(card.population_affected)}</div></div>
      <div><div class="kv-k">Displaced</div><div class="kv-v">${fmt.compact(card.displaced_estimate)}</div></div>
      <div><div class="kv-k">Boats</div><div class="kv-v">${fmt.int(card.boats_required)}</div></div>
      <div><div class="kv-k">Med teams</div><div class="kv-v">${fmt.int(card.medical_teams)}</div></div>
    </div>

    ${escalation}

    <div class="sect">
      <h4>Why this rank</h4>
      <table class="tbl"><tbody>
        ${Object.entries(zone.score_parts).map(([k, v]) => `
          <tr><td style="text-transform:capitalize">${esc(k)}</td>
              <td class="r">${fmt.num(v * 100, 0)}</td></tr>`).join("")}
      </tbody></table>
    </div>

    <div class="sect">
      <h4>Actions (${card.actions.length})</h4>
      ${actions || empty("No actions triggered", "Conditions are below every SOP threshold.")}
    </div>

    <div class="sect">
      <h4>Shelter allocation</h4>
      ${shelters}
    </div>

    <div class="sect">
      <h4>Access</h4>
      <div class="note">${esc(card.route_note)}</div>
    </div>

    <div class="sect">
      <h4>Relief supplies · 3 days</h4>
      <table class="tbl">
        <thead><tr><th>Item</th><th class="r">Qty</th><th>Unit</th></tr></thead>
        <tbody>${supplies}</tbody>
      </table>
    </div>

    <div class="sect">
      <h4>Draft alert · ${card.alert_text.length} characters</h4>
      <div class="alert-draft">${esc(card.alert_text)}</div>
    </div>
  </div>`;
}

/* --- waterlogging (planning screen) -------------------------------------- */

export function renderWaterlogging(el, data) {
  if (!data.hotspots.length) {
    el.innerHTML = empty("No waterlogging hotspots",
      "No cell in this district combines low ground, converging drainage and standing water.");
    return;
  }
  el.innerHTML = `
    <div class="card">
      <h3>Waterlogging hotspots</h3>
      <div class="card-sub">
        RANKED BY PEOPLE × DAYS OF STANDING WATER
      </div>
      <p class="note" style="margin-top:12px">
        Waterlogging is not river flooding. It is rain that lands where there is
        nowhere to drain to, and it can persist for weeks after the river is back
        in bank — which is what keeps relief camps open. This map is computable
        before it rains.
      </p>
    </div>
    ${data.hotspots.map((h, i) => `
      <div class="card">
        <h3 style="font-size:15px">${i + 1}. ${esc(h.label)}</h3>
        <div class="kv" style="grid-template-columns:repeat(3,1fr)">
          <div><div class="kv-k">People</div><div class="kv-v">${fmt.compact(h.population)}</div></div>
          <div><div class="kv-k">Drains in</div><div class="kv-v">${fmt.num(h.drain_down_days, 1)}<small style="font-size:10px"> d</small></div></div>
          <div><div class="kv-k">Depth</div><div class="kv-v">${fmt.num(h.standing_depth_m, 2)}<small style="font-size:10px"> m</small></div></div>
        </div>
        <div class="sect">
          <h4>Why here</h4>
          ${h.drivers.map((d) => `<div class="act-text">· ${esc(d)}</div>`).join("")}
        </div>
      </div>`).join("")}`;
}

/* --- national table ------------------------------------------------------ */

export function renderNational(el, rows) {
  const sorted = [...rows].sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0));
  const tone = (r) => r >= 70 ? "severe" : r >= 55 ? "alert" : r >= 40 ? "watch" : "ok";
  const colour = { severe: "#DC5B4B", alert: "#E08A3C", watch: "#D8B33C", ok: "#3FA86B" };

  el.innerHTML = `
    <table class="nat">
      <thead><tr>
        <th>District</th><th>Driver</th><th class="r">Population</th><th>Risk</th>
      </tr></thead>
      <tbody>
        ${sorted.map((d) => `
          <tr data-code="${esc(d.code)}">
            <td>
              <div style="font-weight:500">${esc(d.name)}</div>
              <div style="font-size:11px;color:var(--text-3)">${esc(d.state)}</div>
            </td>
            <td><span class="pill" data-tone="${tone(d.risk_score)}">${esc(d.flood_driver)}</span></td>
            <td class="r">${fmt.compact(d.population_2025_estimate)}</td>
            <td>
              <div class="riskbar">
                <i style="width:${Math.min(100, d.risk_score)}%;background:${colour[tone(d.risk_score)]}"></i>
              </div>
              <div style="font-size:10px;color:var(--text-3);margin-top:2px"
                   class="mono">${fmt.num(d.risk_score, 0)}${d.modelled ? " · modelled" : " · estimate"}</div>
            </td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}


/* ==========================================================================
   Red zones and relocation  (SIH26191)
   ========================================================================== */

const HAZARD_LABEL = {
  flood: "Flood", landslide: "Landslide",
  waterlogging: "Waterlogging", erosion: "Coastal erosion",
};

export function renderRedZoneStats(el, summary) {
  const rz = summary.red_zones, rel = summary.relocation;
  const frac = rz.red_zone_fraction * 100;
  el.innerHTML = `
    <div class="stat">
      <div class="stat-k">Red zone</div>
      <div class="stat-v" ${frac >= 25 ? 'data-tone="severe"' : frac >= 10 ? 'data-tone="alert"' : ""}>
        ${fmt.num(rz.red_zone_area_km2, 0)}<small>km²</small>
      </div>
    </div>
    <div class="stat">
      <div class="stat-k">Of district</div>
      <div class="stat-v">${fmt.num(frac, 1)}<small>%</small></div>
    </div>
    <div class="stat">
      <div class="stat-k">People in red zone</div>
      <div class="stat-v" data-tone="alert">${fmt.compact(rel.population_in_red_zone)}</div>
    </div>
    <div class="stat">
      <div class="stat-k">Immediate relocation</div>
      <div class="stat-v" ${rel.immediate ? 'data-tone="severe"' : ""}>
        ${rel.immediate}<small>habitations</small>
      </div>
    </div>`;
}

export function renderHabitations(el, habs, selectedId) {
  const listed = habs.filter((h) => h.relocation_horizon !== "monitor");
  if (!listed.length) {
    el.innerHTML = empty("No habitation in a red zone",
      "No settlement has a material share of its population on land rendered uninhabitable within a century.");
    return;
  }
  el.innerHTML = listed.map((h) => `
    <button class="zone" data-hab="${esc(h.id)}"
            aria-current="${h.id === selectedId}">
      <span class="zone-rank">${h.rank}</span>
      <span class="zone-main">
        <span class="zone-label">${esc(h.label)}</span>
        <span class="zone-meta">
          ${fmt.int(h.population_in_red_zone)} of ${fmt.int(h.population)} exposed ·
          every ${h.return_period_years ?? "—"} yr ·
          ${esc(HAZARD_LABEL[h.dominant_hazard] || "—").toLowerCase()}
        </span>
      </span>
      <span class="zone-score">
        ${fmt.num(h.priority_score, 0)}
        <span class="pill" data-tone="${HORIZON_TONE[h.relocation_horizon] || "ok"}">
          ${esc(h.relocation_horizon)}
        </span>
      </span>
    </button>`).join("");
}

/** The click-to-justify popup: why this exact point on the map is, or is
 *  not, a Red Zone. Deliberately compact - a popup, not a takeover - but
 *  every number in it is the same one the map overlay itself was painted
 *  from, so it can never disagree with what is on screen.
 */
export function renderExplainPopup(x) {
  if (!x.in_district) {
    return `<div class="explain-pop">
      <p class="card-sub">Outside the district boundary.</p></div>`;
  }
  if (!x.is_red_zone) {
    return `<div class="explain-pop" data-tone="ok">
      <h4>Not a Red Zone</h4>
      <p class="note" style="margin:0">${esc(x.explanation)}</p>
    </div>`;
  }
  const tone = HORIZON_TONE[x.relocation_horizon] || "severe";
  const rows = [];
  if (x.readings.slope_deg != null && x.dominant_hazard === "landslide") {
    rows.push(["Slope", `${x.readings.slope_deg}°`]);
  }
  if (x.dominant_hazard === "flood") {
    rows.push(["Height above drainage", `${x.readings.height_above_drainage_m} m`]);
  }
  if (x.population_in_cell) {
    rows.push(["People here", fmt.int(x.population_in_cell)]);
  }
  return `<div class="explain-pop" data-tone="${tone}">
    <h4>
      <span class="pill" data-tone="${tone}">${esc(x.relocation_horizon).toUpperCase()}</span>
      ${esc(HAZARD_LABEL[x.dominant_hazard] || x.dominant_hazard || "Red Zone")}
    </h4>
    <p class="note" style="margin:8px 0">${esc(x.explanation)}</p>
    ${rows.length ? `<div class="explain-rows">${rows.map(([k, v]) =>
      `<div class="hkv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>` : ""}
  </div>`;
}

export function renderHabitationDetail(hab, plan, horizons, explain) {
  const advice = (horizons.find((x) => x.name === hab.relocation_horizon) || {}).advice || "";
  const hazards = hab.hazards_present.length
    ? hab.hazards_present.map((h) =>
        `<span class="pill" data-tone="watch">${esc(HAZARD_LABEL[h] || h)}</span>`).join(" ")
    : `<span class="card-sub">none</span>`;

  // The concrete, checkable version of "why this horizon" — the same
  // click-to-justify read the map uses at this exact point, so a presenter
  // never has to explain the reasoning twice in two different ways.
  const threat = explain && explain.explanation
    ? `<div class="threat-box">
         <p class="note" style="margin:0">${esc(explain.explanation)}</p>
         ${explain.population_in_cell
           ? `<p class="card-sub" style="margin-top:6px">
                ~${fmt.int(explain.population_in_cell)} people are estimated to
                live in this specific grid cell, at this exact location.</p>`
           : ""}
       </div>`
    : "";

  const assignments = plan && plan.assignments.length
    ? `<table class="tbl">
         <thead><tr><th>Site</th><th class="r">km</th><th class="r">People</th></tr></thead>
         <tbody>${plan.assignments.map((a) => `
           <tr><td>${esc(a.site_label)}</td>
               <td class="r">${fmt.num(a.distance_km, 1)}</td>
               <td class="r">${fmt.int(a.people)}</td></tr>`).join("")}
         </tbody></table>`
    : `<div class="note">No eligible site with capacity was found within reach.</div>`;

  const notes = plan && plan.notes.length
    ? `<div class="escal">${plan.notes.map((n) => `<p>${esc(n)}</p>`).join("")}</div>`
    : "";

  return `
  <div class="card">
    <h3>${esc(hab.label)}</h3>
    <div class="card-sub">
      RANK ${hab.rank} · SCORE ${fmt.num(hab.priority_score, 0)} ·
      ${esc(hab.relocation_horizon).toUpperCase()}
    </div>

    <div class="kv">
      <div><div class="kv-k">Population</div><div class="kv-v">${fmt.compact(hab.population)}</div></div>
      <div><div class="kv-k">In red zone</div><div class="kv-v">${fmt.compact(hab.population_in_red_zone)}</div></div>
      <div><div class="kv-k">Recurs every</div><div class="kv-v">${hab.return_period_years ?? "—"}<small style="font-size:10px"> yr</small></div></div>
      <div><div class="kv-k">Exposed</div><div class="kv-v">${fmt.num(hab.fraction_in_red_zone * 100, 0)}<small style="font-size:10px">%</small></div></div>
    </div>

    <div class="sect">
      <h4>The threat this population faces</h4>
      ${threat || `<div class="note">${esc(advice)}</div>`}
      <p class="card-sub" style="margin-top:8px">
        Earliest hazard anywhere in the settlement: every
        ${hab.earliest_hazard_return_period_years ?? "—"} years. The horizon uses
        the return period at which ${fmt.num(hab.material_exposure_threshold * 100, 0)}%
        of residents are affected, not the first cell to be touched.
      </p>
    </div>

    <div class="sect">
      <h4>Hazards present</h4>
      <div>${hazards}</div>
    </div>

    ${notes}

    <div class="sect">
      <h4>Relocation allocation</h4>
      ${assignments}
      ${plan ? `<p class="card-sub" style="margin-top:8px">
        ${fmt.int(plan.people_placed)} of ${fmt.int(plan.people_to_move)} placed
        (${fmt.num(plan.placement_rate * 100, 0)}%), mean move
        ${fmt.num(plan.mean_distance_km, 1)} km.</p>` : ""}
    </div>
  </div>`;
}

export function renderRelocation(el, data) {
  const s = data;
  el.innerHTML = `
    <div class="card">
      <h3>Relocation capacity</h3>
      <div class="card-sub">SAFE · BUILDABLE · WITH ROOM</div>
      <div class="kv" style="grid-template-columns:repeat(3,1fr)">
        <div><div class="kv-k">To relocate</div><div class="kv-v">${fmt.compact(s.people_to_relocate)}</div></div>
        <div><div class="kv-k">Placeable</div><div class="kv-v">${fmt.compact(s.people_placeable)}</div></div>
        <div><div class="kv-k">Unplaced</div><div class="kv-v" style="color:${s.unplaced ? "var(--severe)" : "inherit"}">${fmt.compact(s.unplaced)}</div></div>
      </div>
      <div class="sect">
        <h4>Binding constraint</h4>
        <div class="note">${esc(s.binding_constraint)}</div>
        <p class="card-sub" style="margin-top:8px">
          ${fmt.num(s.eligible_area_km2, 0)} km² of safe, buildable land ·
          district headroom ${fmt.compact(s.district_headroom)} at
          ${fmt.int(s.sustainable_density_per_km2)}/km² ·
          ${s.sites_identified} sites assembled totalling
          ${fmt.compact(s.total_site_capacity)}.
        </p>
      </div>
    </div>
    ${s.sites.map((site) => `
      <div class="card">
        <h3 style="font-size:15px">${esc(site.label)}</h3>
        <div class="kv" style="grid-template-columns:repeat(3,1fr)">
          <div><div class="kv-k">Capacity</div><div class="kv-v">${fmt.compact(site.capacity)}</div></div>
          <div><div class="kv-k">Area</div><div class="kv-v">${fmt.num(site.area_km2, 1)}<small style="font-size:10px"> km²</small></div></div>
          <div><div class="kv-k">Road</div><div class="kv-v">${fmt.num(site.road_distance_km, 1)}<small style="font-size:10px"> km</small></div></div>
        </div>
        ${site.constraints.length ? `<div class="sect"><h4>Constraints</h4>
          ${site.constraints.map((c) => `<div class="act-text">· ${esc(c)}</div>`).join("")}
        </div>` : ""}
      </div>`).join("")}`;
}


/* --- mitigation: who is accountable, under what law ---------------------- */

const STRATEGY_TONE = { relocate: "severe", protect: "alert", monitor: "watch" };
const FAMILY_LABEL = {
  structural: "Structural", regulatory: "Regulatory",
  relocation: "Relocation", preparedness: "Preparedness",
};

/** One measure: what to do, why, who leads it, and the statute behind it.
 *
 *  The rationale and the legal basis are shown inline rather than tucked
 *  behind a click, because they are the whole point of this screen — a
 *  measure with no named owner and no statute is a suggestion, not a plan.
 */
function measureRow(m) {
  const lead = m.lead_authority || {};
  const support = (m.supporting_authorities || [])
    .map((a) => a.name).filter(Boolean);
  return `
    <div class="mit-measure">
      <div class="mit-measure-hd">
        <span class="pill" data-tone="watch">${esc(FAMILY_LABEL[m.family] || m.family)}</span>
        <b>${esc(m.measure)}</b>
      </div>
      ${m.rationale ? `<p class="mit-why">${esc(m.rationale)}</p>` : ""}
      <div class="hkv"><span>Lead</span><b>${esc(lead.name || "—")}${
        lead.level ? ` <small>(${esc(lead.level)})</small>` : ""}</b></div>
      ${support.length ? `<div class="hkv"><span>Supporting</span>
        <b>${esc(support.join(", "))}</b></div>` : ""}
      ${m.legal_basis ? `<p class="mit-law">${esc(m.legal_basis)}</p>` : ""}
    </div>`;
}

export function renderMitigation(el, d) {
  const s = d.summary || {};
  const byStrat = s.by_strategy || {};
  const cost = s.indicative_cost || {};

  el.innerHTML = `
    <div class="card">
      <h3>What is done, and by whom</h3>
      <div class="card-sub">ACCOUNTABLE AUTHORITY AND STATUTE FOR EVERY MEASURE</div>
      <div class="kv" style="grid-template-columns:repeat(3,1fr)">
        <div><div class="kv-k">Relocate</div><div class="kv-v">${byStrat.relocate || 0}</div></div>
        <div><div class="kv-k">Protect</div><div class="kv-v">${byStrat.protect || 0}</div></div>
        <div><div class="kv-k">Monitor</div><div class="kv-v">${byStrat.monitor || 0}</div></div>
      </div>
      ${cost.low_crore != null ? `<p class="card-sub" style="margin-top:10px">
        Indicative cost <b>₹${fmt.num(cost.low_crore, 0)}–${fmt.num(cost.high_crore, 0)} crore</b>
        across ${s.habitations_planned} habitations.
        ${esc(cost.precision || "")}</p>` : ""}
      ${s.doctrine ? `<div class="note" style="margin-top:10px">${esc(s.doctrine)}</div>` : ""}
    </div>

    ${(d.plans || []).map((p) => `
      <div class="card">
        <h3 style="font-size:15px">${esc(p.label)}</h3>
        <div class="mit-strat">
          <span class="pill" data-tone="${STRATEGY_TONE[p.strategy] || "watch"}">
            ${esc(p.strategy).toUpperCase()}</span>
          <span class="card-sub">${fmt.int(p.population_in_red_zone)} in red zone ·
            ${fmt.int(p.families)} families · ${esc(p.horizon)}</span>
        </div>
        ${p.strategy_reason
          ? `<div class="threat-box" style="margin-top:10px">
               <p class="note" style="margin:0">${esc(p.strategy_reason)}</p></div>`
          : ""}
        ${p.cost_estimate ? `<p class="card-sub" style="margin-top:8px">
          ₹${fmt.num(p.cost_estimate.low_crore, 1)}–${fmt.num(p.cost_estimate.high_crore, 1)} crore
          &middot; ${esc(p.cost_estimate.basis || "")}</p>` : ""}
        <div class="sect">
          <h4>${p.measure_count} measure${p.measure_count === 1 ? "" : "s"}</h4>
          ${Object.entries(p.measures_by_family || {}).map(([, ms]) =>
            ms.map(measureRow).join("")).join("")}
        </div>
      </div>`).join("")}`;
}


/* --- national live screen ------------------------------------------------ */

export function renderNationalStats(el, d) {
  const live = d.live;
  el.innerHTML = `
    <div class="stat">
      <div class="stat-k">Squares scored</div>
      <div class="stat-v">${fmt.int(d.cell_count)}<small>@ ${d.cell_size_km} km</small></div>
    </div>
    <div class="stat">
      <div class="stat-k">Data</div>
      <div class="stat-v" ${live ? "" : 'data-tone="alert"'}
           style="font-size:19px">${live ? "LIVE" : "MODELLED"}</div>
    </div>
    <div class="stat">
      <div class="stat-k">On watchlist</div>
      <div class="stat-v" ${d.watchlist_size ? 'data-tone="severe"' : ""}>
        ${d.watchlist_size}
      </div>
    </div>
    <div class="stat">
      <div class="stat-k">Highest score</div>
      <div class="stat-v">${fmt.num(d.max_score, 0)}</div>
    </div>`;
}

export function renderNationalLive(el, d) {
  const band = (b) => ({ "extremely heavy": "severe", "very heavy": "alert",
                         "heavy": "watch" }[b] || "ok");
  const rows = d.watchlist.length
    ? d.watchlist.map((c, i) => `
        <button class="zone" data-cellpick="${esc(c.districts[0] || "")}">
          <span class="zone-rank">${i + 1}</span>
          <span class="zone-main">
            <span class="zone-label">${esc(c.id)} &nbsp;
              <span style="color:var(--text-3);font-weight:400">
                ${c.lat.toFixed(1)}&deg;N ${c.lon.toFixed(1)}&deg;E</span></span>
            <span class="zone-meta">
              ${c.rain_next_24h_mm} mm/24h &middot; CAPE ${c.cape} &middot;
              soil ${(c.soil_saturation * 100).toFixed(0)}%
              ${c.districts.length ? " &middot; " + esc(c.districts.join(", ")) : ""}
            </span>
          </span>
          <span class="zone-score">
            ${fmt.num(c.score, 0)}
            <span class="pill" data-tone="${band(c.band)}">${esc(c.band)}</span>
          </span>
        </button>`).join("")
    : empty("Nothing on the watchlist",
            "No square in the country currently crosses the alert threshold. That is the normal state, and it is what makes the exceptions worth looking at.");

  el.innerHTML = `
    <div class="card">
      <h3>National screen</h3>
      <div class="card-sub">${esc(d.source).toUpperCase()}</div>
      <p class="note" style="margin-top:12px">
        India tessellated into ${fmt.int(d.cell_count)} squares of
        ${d.cell_size_km} km, each scored on live rainfall, convective energy
        and soil saturation. The full physics costs seconds per district and
        cannot run continuously for the whole country &mdash; this decides where
        it is worth spending.
      </p>
      <p class="card-sub" style="margin-top:8px">
        Generated ${esc(d.generated_at)} in ${d.build_seconds}s.
        ${esc(d.coverage_note)}
      </p>
    </div>
    ${rows}
    ${d.watchlist.length ? `<div class="card">
      <h4 style="margin:0 0 8px;font-family:var(--font-mono);font-size:11px;
                 letter-spacing:.09em;text-transform:uppercase;color:var(--text-3)">
        Why these fired</h4>
      ${d.watchlist.slice(0, 4).map((c) => `
        <div class="sect" style="margin-top:10px">
          <h4>${esc(c.id)}</h4>
          ${c.drivers.map((x) => `<div class="act-text">&bull; ${esc(x)}</div>`).join("")
            || `<div class="act-text" style="color:var(--text-3)">below every named threshold</div>`}
        </div>`).join("")}
    </div>` : ""}
    <div class="card">
      <h4 style="margin:0 0 8px;font-family:var(--font-mono);font-size:11px;
                 letter-spacing:.09em;text-transform:uppercase;color:var(--text-3)">
        IMD rainfall warning bands</h4>
      <table class="tbl">
        <thead><tr><th>mm / 24h</th><th>Category</th></tr></thead>
        <tbody>${d.imd_bands.map((b) => `
          <tr><td class="mono">${b.threshold_mm > 0 ? "&ge; " + b.threshold_mm : "&lt; 64.5"}</td>
              <td><span class="pill" data-tone="${band(b.label)}">${esc(b.label)}</span></td>
          </tr>`).join("")}</tbody>
      </table>
      <p class="card-sub" style="margin-top:8px">
        Official IMD categories, not an invented index. A District Magistrate
        already has standing orders attached to these words.
      </p>
    </div>`;
}

/* ------------------------------------- national coverage, by district */

/** All 735 districts, at two tiers that must never look alike.
 *
 *  A screening score and a modelled score are different kinds of statement.
 *  One is "the weather here is worth a look"; the other is "we ran the terrain
 *  and this many people stand inside a red zone". Rendering them identically
 *  would be the single most misleading thing this interface could do, so the
 *  tier chip is on every row and the screening rows carry no exposure figures
 *  at all — there are none to carry.
 */
export function renderNationalDistricts(el, d) {
  if (!d.available) {
    el.innerHTML = empty("District coverage not baked",
      esc(d.reason || "Run scripts/fetch_districts.py."));
    return;
  }

  const c = d.counts;
  const row = (r, i) => {
    const modelled = r.tier === "modelled";
    const score = r.score === null || r.score === undefined
      ? '<span style="color:var(--text-3);font-weight:400">n/a</span>'
      : fmt.num(r.score, 0);
    const meta = modelled
      ? `${fmt.compact(r.population_in_red_zone)} in red zone &middot; `
        + `${fmt.num(r.red_zone_km2, 0)} km&sup2; &middot; `
        + `${r.immediate_habitations} immediate`
      : (r.drivers && r.drivers.length
          ? r.drivers.map((x) => esc(x.slice(0, 60))).join(" &middot; ")
          : "no live signal");
    return `
      <button class="zone" ${modelled ? `data-cellpick="${esc(r.code)}"` : ""}
              data-flyto="${r.lat},${r.lon}">
        <span class="zone-rank">${i + 1}</span>
        <span class="zone-main">
          <span class="zone-label">${esc(r.name)}</span>
          <span class="zone-meta">${meta}</span>
        </span>
        <span class="zone-score">
          ${score}
          <span class="pill" data-tone="${modelled ? "ok" : "watch"}">
            ${modelled ? "modelled" : "screening"}</span>
        </span>
      </button>`;
  };

  el.innerHTML = `
    <div class="card">
      <h3>Every district in India</h3>
      <div class="card-sub">${esc(d.boundaries.source || "geoBoundaries ADM2")}</div>
      <p class="note" style="margin-top:12px">
        <b>${fmt.int(c.modelled)} modelled</b> &mdash; full hazard model: HAND
        inundation, D8 routing, the BIS landslide rating, recurrence across
        three return periods, exposure and relocation caseload.<br>
        <b>${fmt.int(c.screening)} screening</b> &mdash; live weather only, with
        no terrain analysis behind the number. Screening says where to look. It
        is not a hazard assessment, and nothing here should be read as one.
      </p>
      ${d.screening_available ? "" : `<p class="note" data-tone="alert"
        style="margin-top:8px"><b>Screening is offline.</b>
        ${esc(d.screening_note)}</p>`}
      <p class="card-sub" style="margin-top:8px">${esc(d.promotion)}</p>
    </div>
    ${d.districts.slice(0, 120).map(row).join("")}
    <div class="card"><p class="card-sub">
      Showing the first 120 of ${fmt.int(c.total)} districts, ranked by score.
      Modelled districts open; screening districts centre the map.
    </p></div>`;
}

/* ------------------------------------------- the live watch board (NOW) */

const ACTION_TONE = { ACT: "severe", PREPARE: "alert", WATCH: "watch",
                      ROUTINE: "ok" };

/** "3s ago" / "4m 12s ago" from an ISO timestamp — ticked client-side so the
 *  page visibly ages between refreshes instead of freezing on one string. */
export function relTime(iso) {
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return `${m}m ${r}s ago`;
  return `${Math.floor(m / 60)}h ${m % 60}m ago`;
}

export function renderWatchStats(el, d) {
  const c = d.counts;
  const replay = d.mode === "replay";
  el.innerHTML = `
    <div class="stat">
      <div class="stat-k">${replay ? "Replay of" : "Data"}</div>
      <div class="stat-v" ${replay ? 'data-tone="alert"' : ""}
           style="font-size:${replay ? 15 : 19}px">
        ${!replay && d.live ? '<span class="live-dot"></span>' : ""}
        ${replay ? esc(d.replay_date) : (d.live ? "LIVE" : "OFFLINE")}</div>
    </div>
    <div class="stat">
      <div class="stat-k">Act today</div>
      <div class="stat-v" ${c.act ? 'data-tone="severe"' : ""}>${c.act}</div>
    </div>
    <div class="stat">
      <div class="stat-k">Prepare</div>
      <div class="stat-v" ${c.prepare ? 'data-tone="alert"' : ""}>${c.prepare}</div>
    </div>
    <div class="stat">
      <div class="stat-k">People at stake</div>
      <div class="stat-v">${fmt.compact(d.population_at_stake || 0)}</div>
    </div>`;
}

/** One district's live signals, each with the number that produced it. */
function watchRow(r, i) {
  const cl = r.cloud, an = cl.anomaly || {};
  const rain = r.rainfall, riv = r.river || {};
  const pop = (r.exposure && r.exposure.population_in_red_zone) || 0;

  const sig = (label, value, note) => `
    <div class="sig">
      <div class="sig-k">${label}</div>
      <div class="sig-v">${value}</div>
      ${note ? `<div class="sig-n">${note}</div>` : ""}
    </div>`;

  const anomalyNote = an.available
    ? `${an.points_above_normal > 0 ? "+" : ""}${an.points_above_normal} pts vs `
      + `${an.column_depth_normal_pct}% normal here`
    : "no local baseline";

  const riverCell = riv.available
    ? sig("River (modelled)",
          `${riv.peak_stage_m} m`,
          `bankfull ${riv.bankfull_m} m &middot; ${esc(riv.reading)}`)
    : "";

  return `
    <article class="watch" data-pick="${esc(r.code)}">
      <header class="watch-hd">
        <span class="watch-rank">${i + 1}</span>
        <span class="watch-name">${esc(r.name)}
          <span class="watch-state">${esc(r.state)}</span></span>
        <span class="pill" data-tone="${ACTION_TONE[r.action]}">${esc(r.action)}</span>
        <span class="watch-urg">${Math.round(r.urgency)}</span>
      </header>

      <div class="sigs">
        ${sig("Cloud column", esc(cl.state),
              `${cl.column.low_pct}/${cl.column.mid_pct}/${cl.column.high_pct}%
               low/mid/high &middot; ${anomalyNote}`)}
        ${sig("Rainfall", `${rain.next_24h_mm} mm/24h`,
              `IMD ${esc(rain.imd_band)} &middot; ${rain.next_72h_mm} mm over 72h`)}
        ${sig("Soil", `${Math.round(r.soil.saturation * 100)}%`,
              esc(r.soil.reading))}
        ${riverCell}
      </div>

      ${pop ? `<p class="watch-pop"><b>${pop.toLocaleString("en-IN")}</b>
         people in this district already live inside a red zone</p>` : ""}

      <ul class="watch-why">
        ${(r.why || []).slice(0, 4).map((w) => `<li>${esc(w)}</li>`).join("")}
      </ul>
      <p class="watch-lead">${esc(r.lead_time_note || "")} &middot;
         ${esc(r.action_meaning)}</p>
    </article>`;
}

/** What to show when the live board is genuinely quiet.
 *
 *  Most days are quiet, and saying so is the honest answer — a board that
 *  invents a storm to look busy is one nobody could trust on a real morning.
 *  But a blank screen is also a dead end, so it offers the replay: the same
 *  pipeline on a day something did happen, one click away and clearly labelled.
 */
function quietBoard(d) {
  return `
    ${empty("Nothing on the board right now",
            "No modelled district is showing a live signal worth acting on. "
            + "That is the normal state, and it is what makes the exceptions "
            + "worth reading.")}
    <div class="card">
      <h4 style="margin:0 0 6px">See the board on a day it mattered</h4>
      <p class="card-sub" style="margin-bottom:10px">
        The same pipeline, run against the archive. Labelled as a replay
        throughout — it is never presented as live.
      </p>
      <button class="ctl" data-replay="2024-07-30">
        Wayanad landslide &middot; 30 Jul 2024</button>
    </div>`;
}

export function renderWatchBoard(el, d) {
  if (!d.live) {
    el.innerHTML = empty("No live weather connection", esc(d.reason || ""));
    return;
  }

  const board = d.districts.filter((r) => r.action !== "ROUTINE");
  const quiet = d.districts.length - board.length;

  const isReplay = d.mode === "replay";
  el.innerHTML = `
    <div class="card">
      <div class="watch-hd-row">
        <h3>${isReplay ? "Replay — not live" : "Live now"}</h3>
        ${isReplay ? "" : `<button class="refresh-btn" id="watch-refresh"
             title="Re-read live weather right now, bypassing the cache">
             &#8635; Refresh live</button>`}
      </div>
      <div class="card-sub">${esc(d.source).toUpperCase()}</div>
      <p class="note" style="margin-top:12px">${esc(d.headline)}</p>
      <p class="card-sub" style="margin-top:8px">
        ${isReplay
          ? `Pre-computed for this replay date, from the ERA5 archive
             (${d.build_seconds}s to compute) — not a live read, so there is
             nothing to count up "ago" from.`
          : `Read <span class="watch-updated" id="watch-updated"
                      data-generated="${esc(d.generated_at)}"
                      >${relTime(d.generated_at)}</span>
             (${d.build_seconds}s to compute).
             ${d.counts.river_simulations} river simulation${d.counts.river_simulations === 1 ? "" : "s"} run.`}
      </p>
    </div>

    ${board.length ? board.map(watchRow).join("") : quietBoard(d)}

    <div class="card"><p class="card-sub">
      ${quiet} of ${d.counts.assessed} modelled districts are quiet and are not
      listed. ${esc(d.method)}
    </p></div>`;
}
