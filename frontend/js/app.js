/* Application controller: state, routing between screens, and wiring.
 *
 * A single state object drives every render, so there is one place to look when
 * the screen disagrees with the data.
 */

import { api, ApiError, fmt } from "./api.js";
import { FloodMap } from "./map.js";
import { Timeline } from "./timeline.js";
import * as P from "./panels.js";
import { HistoryLayer, renderHistoryPanel, renderEventDetail, renderTrackDetail,
        renderHistoricalContext, isPointTestable }
  from "./history.js";
import { StoryScrubber, renderStoryPanel, phaseAt } from "./story.js";

const $ = (id) => document.getElementById(id);

/* Three groups, and the screens inside each. The six screens all survive —
   this only stops the header asking a visitor to understand the whole
   application before pressing anything. Relief, inundation and relocation are
   named explicitly rather than hidden behind the word "Event". */
const GROUPS = {
  story: [["story", "The story"]],
  now: [["watch", "Live board"]],
  district: [["redzone", "Red zones"],
             ["district", "Inundation & relief"],
             ["relocation", "Relocation"],
             ["mitigation", "Mitigation"]],
  india: [["national", "All districts"],
          ["state", "State rollup"],
          ["history", "History"]],
};

const GROUP_OF = {};
Object.entries(GROUPS).forEach(([g, rows]) =>
  rows.forEach(([sc]) => { GROUP_OF[sc] = g; }));

const state = {
  screen: "watch",
  group: "now",
  watch: null,
  watchDate: "",
  code: "BR-DAR",
  severity: "severe",
  liveRainfall: false,
  layer: "depth",
  hour: null,          // null = show the event peak
  assessment: null,
  detail: null,
  districts: [],
  selectedZone: null,
  redzone: null,
  habitations: null,
  relocationData: null,
  assessmentLayer: "redzone",
  history: null,
  historyYear: new Date().getFullYear(),
  stateName: null,
  nationalView: "grid",   // "grid" = 27 km squares, "districts" = all 735
  nationalDistricts: null,
  stateData: null,
  storyData: null,
};

const el = {
  main: $("main"), stats: $("stats"), rightBody: $("right-body"),
  rightTitle: $("right-title"), rightCount: $("right-count"),
  layerList: $("layer-list"), legend: $("legend"), legendBar: $("legend-bar"),
  legendScale: $("legend-scale"), legendTitle: $("legend-title"),
  badge: $("map-badge"), badgeName: $("badge-name"), badgeSub: $("badge-sub"),
  prov: $("prov"), paneLeft: $("pane-left"),
};

const map = new FloodMap($("map"));
const timeline = new Timeline({
  root: $("timeline"), slider: $("hour"), canvas: $("spark"),
  hourEl: $("tl-hour"), subEl: $("tl-sub"),
  playBtn: $("play"), playIcon: $("play-icon"),
});

/* ---------------------------------------------------------------- helpers */

function setProvenance(prov) {
  // "Live satellite" would be a lie here. The live feed is a numerical weather
  // model — rainfall, convective energy, soil moisture — not imagery. The
  // badge has to name what the data actually is, because it is the one thing
  // on screen a reviewer is entitled to take at face value.
  const mode = prov?.data_mode === "live" ? "live" : "demo";
  el.prov.dataset.mode = mode;
  el.prov.textContent = mode === "live" ? "Live forecast data" : "Demo data";
  el.prov.title = prov?.note || "";
}

function showError(err) {
  el.rightBody.innerHTML = P.errorBox(err);
  console.error(err);
}

/* ------------------------------------------------------------ layer panel */

const LAYER_SWATCH = {
  depth: "linear-gradient(90deg,var(--d2),var(--d5))",
  duration: "linear-gradient(90deg,var(--d1),var(--d6))",
  hand: "linear-gradient(90deg,#8C2D18,#2F5D46)",
  wetness: "linear-gradient(90deg,#D8D2A8,#1B3F5C)",
  population: "linear-gradient(90deg,#2E8B57,#C4362C)",
  basemap: "linear-gradient(90deg,#36474A,#A8AC9E)",
};

function renderLayers(layers) {
  el.layerList.innerHTML = layers
    .filter((l) => l.name !== "basemap")
    .map((l) => `
      <button class="layer" data-layer="${l.name}"
              aria-pressed="${l.name === state.layer}">
        <span class="layer-swatch" style="background:${LAYER_SWATCH[l.name] || "var(--surface-3)"}"></span>
        <span class="layer-name">${P.esc(l.title)}</span>
      </button>`).join("");
  el.layerList.querySelectorAll(".layer").forEach((b) => {
    b.addEventListener("click", () => {
      state.layer = b.dataset.layer;
      el.layerList.querySelectorAll(".layer").forEach(
        (x) => x.setAttribute("aria-pressed", String(x === b)));
      map.setOverlay(state.layer, state.layer === "depth" ? state.hour : null);
      updateLegend(layers);
    });
  });
  updateLegend(layers);
}

function updateLegend(layers) {
  const l = layers.find((x) => x.name === state.layer);
  if (!l || !l.legend) { el.legend.hidden = true; return; }
  el.legend.hidden = false;
  el.legendTitle.textContent = `${l.title}${l.unit ? ` (${l.unit})` : ""}`;
  el.legendBar.innerHTML = l.legend
    .map((s) => `<i style="background:${s.color}"></i>`).join("");
  el.legendScale.innerHTML =
    `<span>${fmt.num(l.legend[0].value, 1)}</span>` +
    `<span>${fmt.num(l.legend[l.legend.length - 1].value, 1)}</span>`;
}

/* ------------------------------------------------------- district screen */

async function loadDistrict() {
  state.selectedZone = null;
  el.rightTitle.textContent = "Worst affected";
  el.rightCount.textContent = "";
  el.rightBody.innerHTML = P.skeleton(7);
  el.stats.innerHTML = "";

  const live = state.liveRainfall;
  try {
    const [detail, assess, series, layers] = await Promise.all([
      api.district(state.code, state.severity, live),
      api.assessment(state.code, state.severity, live),
      api.timeline(state.code, state.severity, live),
      api.layers(state.code, state.severity, live),
    ]);
    state.detail = detail;
    state.assessment = assess;

    // A live request that could not reach the weather feed falls back to the
    // design storm on every one of the four calls above — consistently, since
    // they all share the same fallback rule now — but the toggle itself has
    // to say so rather than sit lit up over data it did not actually get.
    const fellBack = live && !!(detail.live_fallback || assess.live_fallback);
    const liveBtn = $("live-toggle");
    if (liveBtn) {
      liveBtn.classList.remove("is-loading");
      liveBtn.classList.toggle("is-live", live && !fellBack);
      liveBtn.classList.toggle("fell-back", fellBack);
      liveBtn.querySelector(".live-toggle-label").textContent = "Live rainfall";
      liveBtn.title = fellBack
        ? "Live feed unreachable — showing the design storm instead"
        : (live ? "Showing real observed and forecast rainfall — click for the design storm"
                : "Showing a synthetic design storm — click for real live rainfall");
    }

    setProvenance(detail.provenance);

    map.clearVectors();
    map.setDistrict(state.code, state.severity, layers.bounds, live && !fellBack);
    map.setBoundary(detail.boundary);
    map.setZones(assess.impact.zones);
    map.setFacilities(detail.facilities);
    map.onZoneClick = selectZone;

    el.badge.hidden = false;
    el.badgeName.textContent = `${detail.district.name}, ${detail.district.state}`;
    el.badgeSub.textContent =
      `${detail.district.flood_driver.toUpperCase()} · ${detail.event.severity.toUpperCase()} ` +
      `· ${fmt.num(detail.event.total_mm, 0)} MM · REF: ${detail.district.reference_event}`;

    renderStats(assess);
    renderLayers(layers.layers);
    showLastRadarPass();

    timeline.load(series);
    timeline.onChange = (h) => {
      state.hour = h;
      if (state.layer === "depth") map.setOverlay("depth", h);
    };
    state.hour = series.peak_hour;
    map.setOverlay(state.layer, state.layer === "depth" ? state.hour : null);

    renderZoneList();
  } catch (err) {
    const liveBtn = $("live-toggle");
    if (liveBtn) {
      liveBtn.classList.remove("is-loading");
      liveBtn.querySelector(".live-toggle-label").textContent = "Live rainfall";
    }
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

/* The real Sentinel-1 acquisition history over this district, queried live from
 * the Copernicus catalogue. Loaded after the main render and allowed to fail
 * silently: it is genuinely useful context, but the dashboard must not wait on
 * a network call that has no reason to succeed in a field operations centre. */
async function showLastRadarPass() {
  const code = state.code;
  try {
    const s = await api.scenes(code, state.severity);
    if (state.code !== code) return;          // user moved on while we waited
    if (!s.scenes.length) return;
    const age = s.latest_age_hours;
    const label = age < 48 ? `${Math.round(age)} h ago`
                           : `${fmt.num(age / 24, 1)} days ago`;
    el.badgeSub.textContent +=
      ` · LAST SENTINEL-1 PASS: ${label.toUpperCase()}`;
    el.badgeSub.title =
      `${s.scenes.length} real Sentinel-1 granules over this district in the ` +
      `last ${s.window_days} days, from the Copernicus catalogue. ` +
      `Demo mode models the backscatter rather than downloading them.`;
  } catch {
    /* offline: the dashboard is unaffected */
  }
}

function renderStats(assess) {
  P.renderStats(el.stats, assess.summary, assess.impact);
}

function renderZoneList() {
  const zones = state.assessment.impact.zones;
  el.rightTitle.textContent = "Worst affected";
  el.rightCount.textContent = `${zones.length} zones`;
  P.renderZones(el.rightBody, zones, state.selectedZone);
  el.rightBody.querySelectorAll(".zone").forEach((b) => {
    b.addEventListener("click", () => selectZone(b.dataset.zone));
    b.addEventListener("mouseenter", () => map.highlight(b.dataset.zone));
    b.addEventListener("mouseleave", () => map.highlight(state.selectedZone));
  });
}

function selectZone(zoneId) {
  const zones = state.assessment.impact.zones;
  const zone = zones.find((z) => z.id === zoneId);
  if (!zone) return;
  state.selectedZone = zoneId;
  const card = state.assessment.action_cards.find((c) => c.zone_id === zoneId);

  el.rightTitle.textContent = `Zone ${zone.rank}`;
  el.rightCount.textContent = "";
  el.rightBody.innerHTML =
    `<div style="padding:12px 16px;border-bottom:1px solid var(--line)">
       <button class="ctl" id="back-to-list">&larr; All zones</button>
     </div>` + P.renderActionCard(card, zone);
  el.rightBody.scrollTop = 0;
  $("back-to-list").addEventListener("click", () => {
    state.selectedZone = null;
    renderZoneList();
    map.highlight(null);
  });

  map.highlight(zoneId);
  map.flyToZone(zoneId);
}

/* ------------------------------------------------------- red zone screen */

const ASSESSMENT_SWATCH = {
  redzone: "linear-gradient(90deg,#E3B23C,#7E1F17)",
  recurrence: "linear-gradient(90deg,#8C2D18,#2F5D46)",
  landslide: "linear-gradient(90deg,#9FBF3B,#C4362C)",
  erosion: "linear-gradient(90deg,#E3B23C,#7E1F17)",
  suitability: "linear-gradient(90deg,#D8D2A8,#1B3F5C)",
};

function renderAssessmentLayers(layers) {
  el.layerList.innerHTML = layers.map((l) => `
    <button class="layer" data-alayer="${l.name}"
            aria-pressed="${l.name === state.assessmentLayer}">
      <span class="layer-swatch" style="background:${ASSESSMENT_SWATCH[l.name] || "var(--surface-3)"}"></span>
      <span class="layer-name">${P.esc(l.title)}</span>
    </button>`).join("");
  el.layerList.querySelectorAll(".layer").forEach((b) => {
    b.addEventListener("click", () => {
      state.assessmentLayer = b.dataset.alayer;
      el.layerList.querySelectorAll(".layer").forEach(
        (x) => x.setAttribute("aria-pressed", String(x === b)));
      map.setAssessmentOverlay(state.assessmentLayer);
      updateAssessmentLegend(layers);
    });
  });
  updateAssessmentLegend(layers);
}

function updateAssessmentLegend(layers) {
  const l = layers.find((x) => x.name === state.assessmentLayer);
  if (!l) { el.legend.hidden = true; return; }
  el.legend.hidden = false;
  el.legendTitle.textContent = `${l.title}${l.unit ? ` (${l.unit})` : ""}`;
  el.legendBar.innerHTML = l.legend
    .map((s) => `<i style="background:${s.color}"></i>`).join("");
  el.legendScale.innerHTML =
    `<span>${fmt.num(l.legend[0].value, 1)}</span>` +
    `<span>${fmt.num(l.legend[l.legend.length - 1].value, 1)}</span>`;
}

async function loadRedZones() {
  state.selectedZone = null;
  el.rightTitle.textContent = "Vulnerable habitations";
  el.rightCount.textContent = "";
  el.rightBody.innerHTML = P.skeleton(7);
  el.stats.innerHTML = "";
  timeline.clear();

  try {
    const [rz, habs, layers] = await Promise.all([
      api.redzones(state.code),
      api.habitations(state.code),
      api.assessmentLayers(state.code),
    ]);
    state.redzone = rz;
    state.habitations = habs;
    setProvenance(rz.provenance);

    map.clearVectors();
    map.setDistrict(state.code, state.severity, layers.bounds);
    map.setBoundary(rz.boundary);
    map.setHabitations(habs.habitations);
    map.onZoneClick = selectHabitation;
    map.enableExplainClick((lat, lon) =>
      api.explainCell(state.code, lat, lon)
        .then((x) => P.renderExplainPopup(x))
        .catch(() => '<div class="explain-pop"><p class="card-sub">'
          + 'Could not read this point.</p></div>'));

    el.badge.hidden = false;
    el.badgeName.textContent = `${rz.district.name}, ${rz.district.state}`;
    const rzs = rz.red_zones;
    el.badgeSub.textContent =
      `MULTI-HAZARD RED ZONES \u00b7 ${fmt.num(rzs.red_zone_area_km2, 0)} KM\u00b2 ` +
      `(${fmt.num(rzs.red_zone_fraction * 100, 1)}%) \u00b7 ` +
      `RECURRENCE ACROSS 5 / 25 / 100-YEAR EVENTS`;

    P.renderRedZoneStats(el.stats, rz);
    state.assessmentLayer = "redzone";
    renderAssessmentLayers(layers.layers);
    map.setAssessmentOverlay("redzone");

    renderHabitationList();
  } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

function renderHabitationList() {
  const habs = state.habitations.habitations;
  const listed = habs.filter((h) => h.relocation_horizon !== "monitor");
  el.rightTitle.textContent = "Vulnerable habitations";
  el.rightCount.textContent = `${listed.length} needing action`;
  P.renderHabitations(el.rightBody, habs, state.selectedZone);
  el.rightBody.querySelectorAll(".zone").forEach((b) => {
    b.addEventListener("click", () => selectHabitation(b.dataset.hab));
    b.addEventListener("mouseenter", () => map.highlight(b.dataset.hab));
    b.addEventListener("mouseleave", () => map.highlight(state.selectedZone));
  });
}

async function selectHabitation(habId) {
  const hab = state.habitations.habitations.find((h) => h.id === habId);
  if (!hab) return;
  state.selectedZone = habId;

  if (!state.relocationData) {
    try { state.relocationData = await api.relocation(state.code); }
    catch { state.relocationData = { plans: [] }; }
  }
  const plan = state.relocationData.plans.find((p) => p.habitation_id === habId);

  el.rightTitle.textContent = `Habitation ${hab.rank}`;
  el.rightCount.textContent = "";
  const backRow = `<div style="padding:12px 16px;border-bottom:1px solid var(--line)">
       <button class="ctl" id="back-to-list">&larr; All habitations</button>
     </div>`;
  el.rightBody.innerHTML = backRow
    + P.renderHabitationDetail(hab, plan, state.habitations.horizons);
  el.rightBody.scrollTop = 0;
  $("back-to-list").addEventListener("click", () => {
    state.selectedZone = null;
    renderHabitationList();
    map.highlight(null);
  });
  map.highlight(habId);
  map.flyToZone(habId);

  // The concrete threat read is a second network round trip - the card above
  // (population, hazard pills, allocation) renders immediately without
  // waiting on it, and this fills in the "why" once it lands.
  try {
    const explain = await api.explainCell(state.code, hab.lat, hab.lon);
    if (state.selectedZone !== habId) return;   // moved on before this landed
    el.rightBody.innerHTML = backRow
      + P.renderHabitationDetail(hab, plan, state.habitations.horizons, explain);
    $("back-to-list").addEventListener("click", () => {
      state.selectedZone = null;
      renderHabitationList();
      map.highlight(null);
    });
  } catch { /* the base card above already stands on its own */ }
}

/* ----------------------------------------------------- relocation screen */

async function loadRelocation() {
  el.rightTitle.textContent = "Relocation sites";
  el.rightCount.textContent = "";
  el.rightBody.innerHTML = P.skeleton(6);
  el.stats.innerHTML = "";
  timeline.clear();

  try {
    const [rz, habs, reloc, layers] = await Promise.all([
      api.redzones(state.code),
      api.habitations(state.code),
      api.relocation(state.code),
      api.assessmentLayers(state.code),
    ]);
    state.redzone = rz;
    state.habitations = habs;
    state.relocationData = reloc;
    setProvenance(rz.provenance);

    map.clearVectors();
    map.setDistrict(state.code, state.severity, layers.bounds);
    map.setBoundary(rz.boundary);
    map.setHabitations(habs.habitations);
    map.setSites(reloc.sites);
    map.drawMoves(reloc.plans, habs.habitations);
    map.onZoneClick = null;

    el.badge.hidden = false;
    el.badgeName.textContent = `${rz.district.name} \u2014 relocation`;
    el.badgeSub.textContent =
      `${reloc.sites_identified} SITES \u00b7 CAPACITY ${fmt.compact(reloc.total_site_capacity)} \u00b7 ` +
      `${fmt.compact(reloc.people_to_relocate)} TO MOVE \u00b7 ` +
      `${fmt.num(reloc.placement_rate * 100, 0)}% PLACEABLE`;

    P.renderRedZoneStats(el.stats, rz);
    state.assessmentLayer = "suitability";
    renderAssessmentLayers(layers.layers);
    map.setAssessmentOverlay("suitability");

    P.renderRelocation(el.rightBody, reloc);
    el.rightCount.textContent = `${reloc.sites_identified} sites`;
  } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

/* ---------------------------------------------------------- story screen */

const STORY_CODE = "KL-WAY";
const STORY_DATE = "2024-07-30";
const STORY_LAT = 11.47, STORY_LON = 76.13;

/** The demonstration: Wayanad on the map, one draggable pointer, the sidebar
 *  showing whatever the system is doing at that hour.
 *
 *  Everything is loaded once here so that dragging is instant. A scrubber that
 *  fetched per step would be unusable in front of an audience.
 */
async function loadStory() {
  el.rightTitle.textContent = "Wayanad, July 2024";
  el.rightCount.textContent = "";
  el.rightBody.innerHTML = P.skeleton(5);
  el.stats.innerHTML = "";
  timeline.clear();

  try {
    const [rz, habs, layers, board, strike, assess, series] = await Promise.all([
      api.redzones(STORY_CODE),
      api.habitations(STORY_CODE),
      api.assessmentLayers(STORY_CODE),
      api.watch(STORY_DATE),
      api.explainCell(STORY_CODE, STORY_LAT, STORY_LON),
      api.assessment(STORY_CODE, state.severity),
      api.timeline(STORY_CODE, state.severity),
    ]);

    state.storyData = {
      redzone: rz,
      detect: (board.districts || []).find((d) => d.code === STORY_CODE) || {},
      strike,
      flood: series,
      cards: assess.action_cards || [],
    };
    setProvenance(rz.provenance);

    // The map holds Wayanad and its risk rating for the whole story. It never
    // moves screen, because the argument is that this one place was known
    // ground before, during and after — and cutting away would break that.
    map.clearVectors();
    map.setDistrict(STORY_CODE, state.severity, layers.bounds);
    map.setBoundary(rz.boundary);
    map.setHabitations(habs.habitations);
    map.onZoneClick = null;
    state.assessmentLayer = "redzone";
    renderAssessmentLayers(layers.layers);
    map.setAssessmentOverlay("redzone");

    el.badge.hidden = false;
    el.badgeName.textContent = "Wayanad, Kerala";
    el.badgeSub.textContent =
      `${fmt.num(rz.red_zones.red_zone_area_km2, 0)} KM² RED ZONE · ` +
      `${fmt.compact(rz.red_zones.population_in_red_zone)} ON IT · ` +
      `30 JULY 2024`;

    P.renderRedZoneStats(el.stats, rz);

    const bar = $("story-bar");
    bar.hidden = false;
    if (!storyScrubber) {
      storyScrubber = new StoryScrubber({
        root: bar,
        onScrub: (t) => {
          renderStoryPanel(el.rightBody, t, state.storyData);
          el.rightCount.textContent = phaseAt(t);
        },
      });
    }
    storyScrubber.set(-72);
  } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

/* ----------------------------------------------------- mitigation screen */

/** Who is accountable for fixing each Red Zone habitation, and under what law.
 *
 *  The screen that answers "and then what". Detection and even relocation
 *  planning still leave the question of who actually signs the order — this
 *  names the authority and the statute for every measure, which is what turns
 *  an assessment into something a District Magistrate can act on.
 */
async function loadMitigation() {
  el.rightTitle.textContent = "Mitigation";
  el.rightCount.textContent = "";
  el.rightBody.innerHTML = P.skeleton(6);
  el.stats.innerHTML = "";
  timeline.clear();

  try {
    const [rz, habs, mit, layers] = await Promise.all([
      api.redzones(state.code),
      api.habitations(state.code),
      api.mitigation(state.code),
      api.assessmentLayers(state.code),
    ]);
    state.redzone = rz;
    state.habitations = habs;
    setProvenance(rz.provenance);

    map.clearVectors();
    map.setDistrict(state.code, state.severity, layers.bounds);
    map.setBoundary(rz.boundary);
    map.setHabitations(habs.habitations);
    map.onZoneClick = null;

    el.badge.hidden = false;
    el.badgeName.textContent = `${rz.district.name} — mitigation`;
    const s = mit.summary;
    const cost = s.indicative_cost || {};
    el.badgeSub.textContent =
      `${s.habitations_planned} HABITATIONS PLANNED · ` +
      `${s.relocation_families || 0} FAMILIES TO RELOCATE` +
      (cost.low_crore != null
        ? ` · ₹${fmt.num(cost.low_crore, 0)}–${fmt.num(cost.high_crore, 0)} CR`
        : "");

    P.renderRedZoneStats(el.stats, rz);
    state.assessmentLayer = "redzone";
    renderAssessmentLayers(layers.layers);
    map.setAssessmentOverlay("redzone");

    P.renderMitigation(el.rightBody, mit);
    el.rightCount.textContent = `${s.habitations_planned} planned`;
  } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

/* ------------------------------------------------------ planning screen */

async function loadPlanning() {
  el.rightTitle.textContent = "Waterlogging";
  el.rightCount.textContent = "";
  el.rightBody.innerHTML = P.skeleton(5);
  try {
    const [detail, wlog, layers] = await Promise.all([
      api.district(state.code, state.severity),
      api.waterlogging(state.code, state.severity),
      api.layers(state.code, state.severity),
    ]);
    setProvenance(detail.provenance);
    map.clearVectors();
    map.setDistrict(state.code, state.severity, layers.bounds);
    map.setBoundary(detail.boundary);
    map.setHotspots(wlog.hotspots);
    state.layer = "wetness";
    renderLayers(layers.layers);
    map.setOverlay("wetness");
    el.badge.hidden = false;
    el.badgeName.textContent = `${detail.district.name} — planning`;
    el.badgeSub.textContent = "WATERLOGGING SUSCEPTIBILITY · COMPUTED BEFORE RAINFALL";
    el.stats.innerHTML = "";
    timeline.clear();
    P.renderWaterlogging(el.rightBody, wlog);
  } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

/* ------------------------------------------------------------ NOW board */

// Two timers, both scoped to however long the board stays on screen. The
// clock is cosmetic — it just ages the "read Xs ago" string so the page
// visibly moves between refreshes. The auto-refresh is the substantive one:
// a quiet re-read every few minutes, applied without disturbing whatever the
// presenter is doing, so "this genuinely updates itself" is something a judge
// can catch happening rather than something they are told.
let watchClockTimer = null;
let watchAutoRefresh = null;
const WATCH_AUTO_REFRESH_MS = 4 * 60 * 1000;

function stopWatchTimers() {
  if (watchClockTimer) { clearInterval(watchClockTimer); watchClockTimer = null; }
  if (watchAutoRefresh) { clearInterval(watchAutoRefresh); watchAutoRefresh = null; }
}

function tickWatchClock() {
  const node = $("watch-updated");
  if (!node) { stopWatchTimers(); return; }
  const generated = node.dataset.generated;
  if (generated) node.textContent = P.relTime(generated);
}

/** Which districts need attention right now, and what is driving it.
 *
 *  ``force`` bypasses both the browser and server caches for a genuine live
 *  re-read — the deliberate "watch me press this, live" moment. It keeps the
 *  current board on screen while the new one is fetched rather than wiping to
 *  a skeleton, because a presenter mid-sentence should not have the evidence
 *  vanish out from under them for the twenty-odd seconds a real refresh takes.
 */
async function loadWatch(force = false) {
  const btn = $("watch-refresh");
  if (force && btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> Reading live weather…';
  }
  if (!force) {
    el.rightTitle.textContent = "Needs attention";
    el.rightCount.textContent = "";
    el.rightBody.innerHTML = P.skeleton(6);
    el.stats.innerHTML = "";
    el.badge.hidden = true;
    el.legend.hidden = true;
    timeline.clear();
    stopWatchTimers();
  }

  try {
    const before = state.watch && state.watch.generated_at;
    const data = await api.watch(state.watchDate, force);
    state.watch = data;

    const c = data.counts;
    el.rightCount.textContent = data.live
      ? `${c.needing_attention} of ${c.assessed}` : "offline";
    P.renderWatchStats(el.stats, data);
    P.renderWatchBoard(el.rightBody, data);
    map.showWatchBoard(data.districts, pickDistrict);

    // A forced refresh that genuinely returned a newer read gets a visible
    // "this just changed" sweep across every card — proof the numbers moved,
    // not just that a request completed.
    if (force && before && data.generated_at !== before) {
      el.rightBody.querySelectorAll(".watch").forEach((c) =>
        c.classList.add("just-updated"));
    }

    el.rightBody.querySelectorAll("[data-pick]").forEach((b) =>
      b.addEventListener("click", () => pickDistrict(b.dataset.pick)));

    // The offer shown on a quiet day, so a blank board is not a dead end.
    el.rightBody.querySelectorAll("[data-replay]").forEach((b) =>
      b.addEventListener("click", () => {
        state.watchDate = b.dataset.replay;
        const sel = $("watch-date");
        if (sel) sel.value = state.watchDate;
        loadWatch();
      }));

    const refreshBtn = $("watch-refresh");
    if (refreshBtn) {
      refreshBtn.disabled = false;
      refreshBtn.innerHTML = "&#8635; Refresh live";
      refreshBtn.addEventListener("click", () => loadWatch(true));
    }

    // The ticking "read Xs ago" clock, and a quiet self-refresh every few
    // minutes — both only make sense on the live board, never on a replay
    // (a replayed day has nothing new to tick towards).
    //
    // stopWatchTimers() runs unconditionally here, including on a forced
    // refresh: every successful load (auto or manual) reaches this line and
    // re-arms both intervals, and without clearing the previous pair first
    // each refresh would stack another ticking clock and another 4-minute
    // auto-refresh on top of the ones already running — a compounding leak
    // where auto-refresh eventually triggers itself several times over.
    stopWatchTimers();
    if (data.mode !== "replay" && data.live) {
      tickWatchClock();
      watchClockTimer = setInterval(tickWatchClock, 1000);
      watchAutoRefresh = setInterval(() => {
        if (state.screen === "watch" && state.watchDate === "") loadWatch(true);
      }, WATCH_AUTO_REFRESH_MS);
    }

    // A replay must never read as live, however convenient that would be.
    setProvenance(
      data.mode === "replay"
        ? { data_mode: "demo",
            note: `Replay of ${data.replay_date} from the ERA5 archive — `
                  + "identical pipeline, historical inputs. Not live." }
        : data.live
          ? { data_mode: "live",
              note: "Cloud structure, rainfall, CAPE and soil moisture are "
                    + "live forecast-model output read minutes ago." }
          : { data_mode: "demo",
              note: "No live connection; the board reports nothing rather "
                    + "than scoring every district zero." });
  } catch (err) {
    const refreshBtn = $("watch-refresh");
    if (force && refreshBtn) {
      // A failed forced refresh must not tear down a board that was working —
      // showError() replaces the whole panel, which is exactly the wrong
      // behaviour here. The button reports the failure on itself instead and
      // the working board underneath stays exactly as it was.
      console.error(err);
      refreshBtn.disabled = false;
      refreshBtn.textContent = "Refresh failed — try again";
      setTimeout(() => {
        if (refreshBtn.isConnected) refreshBtn.innerHTML = "&#8635; Refresh live";
      }, 3000);
      return;
    }
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

/* ------------------------------------------------------ national screen */

async function loadNational() {
  el.rightTitle.textContent = state.nationalView === "districts"
    ? "Every district in India" : "Live national screen";
  el.rightCount.textContent = "";
  el.rightBody.innerHTML = P.skeleton(10);
  el.stats.innerHTML = "";
  el.badge.hidden = true;
  el.legend.hidden = true;
  timeline.clear();

  if (state.nationalView === "districts") { await loadNationalDistricts(); return; }

  try {
    const data = await api.national(0.75);
    el.rightCount.textContent = `${data.watchlist_size} firing`;
    map.showNationalGrid(data.cells, pickDistrict);
    P.renderNationalStats(el.stats, data);
    P.renderNationalLive(el.rightBody, data);
    el.rightBody.querySelectorAll("[data-cellpick]").forEach((b) => {
      const code = b.dataset.cellpick;
      if (code) b.addEventListener("click", () => pickDistrict(code));
    });
    // The badge is a fact the server reports, not a claim we make: the national
    // screen genuinely runs on live forecast data when the network allows it.
    setProvenance(data.live
      ? { data_mode: "live",
          note: data.source + " — rainfall, convective energy and soil "
                + "moisture are real forecast model output." }
      : { data_mode: "demo",
          note: "No live connection; squares scored from modelled values." });
  } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

/** All 735 districts, ranked, with the two tiers kept visibly apart. */
async function loadNationalDistricts() {
  try {
    const data = state.nationalDistricts || await api.nationalDistricts();
    state.nationalDistricts = data;

    if (data.available) {
      el.rightCount.textContent = `${data.counts.modelled} modelled`;
      P.renderNationalStats(el.stats, {
        cell_count: data.counts.total, cell_size_km: "district",
        live: data.live,
        watchlist_size: data.counts.modelled,
        max_score: data.districts.length ? (data.districts[0].score || 0) : 0,
      });
    }
    P.renderNationalDistricts(el.rightBody, data);

    el.rightBody.querySelectorAll("[data-cellpick]").forEach((b) => {
      const code = b.dataset.cellpick;
      if (code) b.addEventListener("click", () => pickDistrict(code));
    });
    el.rightBody.querySelectorAll("[data-flyto]").forEach((b) => {
      if (b.dataset.cellpick) return;      // modelled rows open instead
      const [lat, lon] = b.dataset.flyto.split(",").map(Number);
      b.addEventListener("click", () => map.flyTo(lat, lon, 8));
    });

    // Screening runs on live weather; when that connection is down the tier
    // has nothing to say, and the badge must not imply otherwise.
    setProvenance(data.screening_available
      ? { data_mode: "live",
          note: "Screening scores from live Open-Meteo forecast; modelled "
                + "districts from baked Copernicus elevation." }
      : { data_mode: "demo",
          note: "No live connection - screening districts are unscored. "
                + "Modelled districts still carry full physics." });
  } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

/* ------------------------------------------------------------ state view */

async function loadState() {
  const sel = $("state-select");
  const name = state.stateName || (sel && sel.value);
  if (!name) return;

  el.rightTitle.textContent = "Districts by need";
  el.rightBody.innerHTML = P.skeleton(8);
  el.stats.innerHTML = "";
  el.badge.hidden = true;
  el.legend.hidden = true;
  timeline.clear();

  try {
    const data = await api.state(name);
    state.stateData = data;
    const t = data.totals;

    el.rightCount.textContent = `${data.coverage.districts_modelled} modelled`;

    const cr = (v) => `₹${fmt.num(v, 0)} Cr`;
    el.stats.innerHTML = `
      <div class="kv">
        <div><div class="kv-k">Red zone</div>
             <div class="kv-v">${fmt.num(t.red_zone_km2, 0)} km²</div></div>
        <div><div class="kv-k">In red zone</div>
             <div class="kv-v">${fmt.compact(t.population_in_red_zone)}</div></div>
        <div><div class="kv-k">Families</div>
             <div class="kv-v">${fmt.compact(t.relocation_families)}</div></div>
        <div><div class="kv-k">Indicative cost</div>
             <div class="kv-v">${cr(t.indicative_cost_crore.low)}</div></div>
      </div>
      <div class="note">
        <b>${t.immediate}</b> habitations need immediate relocation,
        <b>${t.short_term}</b> short-term, <b>${t.medium_term}</b> medium-term.
        Indicative cost ${cr(t.indicative_cost_crore.low)}–${cr(t.indicative_cost_crore.high)}.
      </div>
      <p class="hist-note">${data.coverage.note}</p>
      <div class="export-row">
        <a class="ctl" href="${api.exportUrl({ state: name })}" download>
          Download CSV</a>
        <button class="ctl" id="print-state">Print summary</button>
      </div>`;

    el.rightBody.innerHTML = data.districts.map((d, i) => `
      <button class="zone" data-statepick="${d.code}">
        <span class="zone-r mono">${i + 1}</span>
        <span class="zone-n">${P.esc(d.name)}</span>
        <span class="zone-m mono">${fmt.compact(d.population_in_red_zone)} ·
          ${fmt.num(d.red_zone_km2, 0)} km²</span>
        <span class="tag tag-${d.dominant_hazard || "none"}">${
          d.by_horizon.immediate} imm</span>
      </button>`).join("");

    el.rightBody.querySelectorAll("[data-statepick]").forEach((b) =>
      b.addEventListener("click", () => pickDistrict(b.dataset.statepick)));
    const pr = $("print-state");
    if (pr) pr.addEventListener("click", () => window.print());

    map.showStateDistricts(data.districts, state.districts, pickDistrict);
    setProvenance({
      data_mode: "demo",
      note: `State rollup across ${data.coverage.districts_modelled} modelled `
          + `districts. ${data.how_ranked}`,
    });
  } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

/* ------------------------------------------------------- observed history */

let storyScrubber = null;
let historyLayer = null;
let historyTimer = null;

function historyBar(show) {
  const bar = $("history-bar");
  if (bar) bar.hidden = !show;
}

function stopHistoryPlay() {
  if (historyTimer) { clearInterval(historyTimer); historyTimer = null; }
  const icon = $("hist-play-icon");
  if (icon) icon.setAttribute("d", "M2 1l9 5-9 5z");
}

function drawHistorySpark(data) {
  const cv = $("hist-spark");
  if (!cv || !data) return;
  const counts = data.counts_per_year || [];
  if (!counts.length) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = cv.clientWidth || 600, h = 34;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const min = 1990, max = 2026;
  const peak = Math.max(...counts.map((c) => c.count), 1);
  const css = getComputedStyle(document.documentElement);
  ctx.fillStyle = css.getPropertyValue("--accent").trim() || "#4A88A8";
  for (const c of counts) {
    const x = ((c.year - min) / (max - min)) * (w - 3);
    const bh = Math.max(2, (c.count / peak) * (h - 6));
    ctx.globalAlpha = c.year <= state.historyYear ? 0.85 : 0.22;
    ctx.fillRect(x, h - bh, 2.5, bh);
  }
  ctx.globalAlpha = 1;
}

function setHistoryYear(year) {
  state.historyYear = year;
  const lbl = $("hist-year-label");
  if (lbl) lbl.textContent = String(year);
  if (historyLayer) historyLayer.setYear(year);
  if (state.history) {
    renderHistoryPanel(el.rightBody, state.history, year);
    bindHistoryRows();
    const n = (state.history.events || []).filter((e) => e.year <= year).length;
    el.rightCount.textContent = `${n} events`;
    const sub = $("hist-sub");
    if (sub) sub.textContent = `observed record to ${year}`;
  }
  drawHistorySpark(state.history);
}

function bindHistoryRows() {
  el.rightBody.querySelectorAll("[data-event]").forEach((b) => {
    b.addEventListener("click", () => {
      const e = (state.history.events || []).find((x) => x.id === b.dataset.event);
      if (e) {
        showEventDetail(e);
        map.flyTo(e.lat, e.lon, 8);
      }
    });
  });
}

/** Which of our modelled districts this event actually happened in, and
 *  whether that district's hazard has a single point a model can be tested
 *  against. Returns null when the event has no modelled district at all —
 *  in that case there is nothing honest to say, so nothing is shown.
 */
async function loadHistoricalContext(e) {
  if (!e.districts || !e.districts.length) return null;
  const code = e.districts[0];
  try {
    if (isPointTestable(e.hazard)) {
      const v = await api.historyValidation(code);
      const verdict = (v.verdicts || []).find((x) => x.event_id === e.id);
      return { kind: "point", verdict };
    }
    const rz = await api.redzones(code);
    return { kind: "area", districtName: rz.district.name, redZone: rz.red_zones };
  } catch {
    return null;
  }
}

/** Event detail, then — once it lands — the honest "would this have helped"
 *  read. Two separate renders on purpose: the citation and death toll should
 *  never wait on a second network round trip to appear.
 */
async function showEventDetail(e) {
  renderEventDetail(el.stats, e);
  const holder = document.createElement("div");
  holder.className = "hist-context-holder";
  el.stats.appendChild(holder);
  const ctx = await loadHistoricalContext(e);
  renderHistoricalContext(holder, ctx);
}

async function loadHistory() {
  el.rightTitle.textContent = "Observed record";
  el.rightBody.innerHTML = P.skeleton(10);
  el.stats.innerHTML = "";
  el.badge.hidden = true;
  el.legend.hidden = true;
  timeline.clear();
  historyBar(true);

  try {
    if (!state.history) state.history = await api.history();
    map.showIndia();
    if (!historyLayer) {
      historyLayer = new HistoryLayer(window.L, map.map);
      historyLayer.onSelect = (sel) => {
        if (sel.kind === "event") showEventDetail(sel.event);
        else renderTrackDetail(el.stats, sel.track);
      };
    }
    historyLayer.attach();
    historyLayer.load(state.history);

    const slider = $("hist-year");
    if (slider) slider.value = String(state.historyYear);
    setHistoryYear(state.historyYear);

    // The badge tells the truth about a screen with two provenances on it.
    const nTracks = (state.history.tracks || []).length;
    setProvenance({
      data_mode: "live",
      note: `${nTracks} observed cyclone tracks live from IBTrACS (NOAA). `
          + `Flood and landslide events are curated with a citation each — no `
          + `free API carries that history for India.`,
    });
  } catch (err) {
    if (err instanceof ApiError) showError(err); else throw err;
  }
}

function pickDistrict(code) {
  state.code = code;
  $("district-select").value = code;
  state.relocationData = null;
  state.redzone = null;
  state.habitations = null;
  setScreen("redzone");
}

/* ------------------------------------------------------------- routing */

/** Render the second row of navigation for whichever group is active. */
function renderSubtabs() {
  const bar = $("subtabs");
  if (!bar) return;
  const rows = GROUPS[state.group] || [];
  // A single-screen group has nothing to choose between, so the row is hidden
  // rather than shown with one dead button in it.
  bar.hidden = rows.length < 2;
  bar.innerHTML = rows.map(([sc, label]) =>
    `<button class="subtab" role="tab" data-screen="${sc}"
             aria-selected="${sc === state.screen}">${P.esc(label)}</button>`).join("");
  bar.querySelectorAll(".subtab").forEach((b) =>
    b.addEventListener("click", () => setScreen(b.dataset.screen)));
}

function setGroup(group) {
  state.group = group;
  const rows = GROUPS[group] || [];
  // Returning to a group lands on whichever screen was last open in it.
  const keep = rows.some(([sc]) => sc === state.screen);
  setScreen(keep ? state.screen : rows[0][0]);
}

function setScreen(screen) {
  state.screen = screen;
  state.group = GROUP_OF[screen] || state.group;
  el.main.dataset.screen = screen;
  document.querySelectorAll("#screen-tabs .tab").forEach((t) =>
    t.setAttribute("aria-selected", String(t.dataset.group === state.group)));
  renderSubtabs();

  el.paneLeft.hidden = screen === "national" || screen === "history"
                       || screen === "state" || screen === "watch";
  // Every control belongs to one screen. Showing all of them everywhere is what
  // made the header unreadable.
  const dsel = $("district-select"), ssel = $("state-select");
  const sev = $("severity-select"), wdate = $("watch-date");
  const liveT = $("live-toggle");
  if (dsel) dsel.hidden = state.group !== "district";
  if (ssel) ssel.hidden = screen !== "state";
  if (sev) sev.hidden = screen !== "district";
  if (liveT) liveT.hidden = screen !== "district";
  if (wdate) wdate.hidden = screen !== "watch";
  const nview = $("national-view");
  if (nview) nview.hidden = screen !== "national";

  if (screen !== "history") {
    stopHistoryPlay();
    historyBar(false);
    if (historyLayer) historyLayer.detach();
  }
  if (screen !== "watch") stopWatchTimers();
  if (screen !== "story") {
    const sb = $("story-bar");
    if (sb) sb.hidden = true;
    if (storyScrubber) storyScrubber.stop();
  }
  if (screen !== "redzone") map.disableExplainClick();
  map.invalidate();
  if (screen === "story") loadStory();
  else if (screen === "watch") loadWatch();
  else if (screen === "national") loadNational();
  else if (screen === "state") loadState();
  else if (screen === "history") loadHistory();
  else if (screen === "redzone") loadRedZones();
  else if (screen === "relocation") loadRelocation();
  else if (screen === "mitigation") loadMitigation();
  else if (screen === "planning") loadPlanning();
  else { state.layer = "depth"; loadDistrict(); }
}

/* ---------------------------------------------------------------- boot */

async function boot() {
  try {
    const data = await api.districts(state.severity);
    state.districts = data.districts;
    $("district-select").innerHTML = data.districts
      .slice()
      .sort((a, b) => a.state.localeCompare(b.state) || a.name.localeCompare(b.name))
      .map((d) => `<option value="${d.code}"${d.code === state.code ? " selected" : ""}>
                     ${P.esc(d.name)} — ${P.esc(d.state)}</option>`).join("");
  } catch (err) {
    showError(err);
    return;
  }

  try {
    const st = await api.states();
    const cur = state.districts.find((d) => d.code === state.code);
    state.stateName = (cur && cur.state) || (st.states[0] && st.states[0].state);
    $("state-select").innerHTML = st.states.map((s) =>
      `<option value="${P.esc(s.state)}"${s.state === state.stateName ? " selected" : ""}>
         ${P.esc(s.state)} — ${s.districts_modelled} modelled</option>`).join("");
    $("state-select").addEventListener("change", (e) => {
      state.stateName = e.target.value;
      loadState();
    });
  } catch { /* the state screen simply stays empty if this fails */ }

  $("district-select").addEventListener("change", (e) => {
    state.code = e.target.value;
    setScreen(state.screen === "national" ? "district" : state.screen);
  });
  // Switching district invalidates every cached assessment payload.
  $("district-select").addEventListener("change", () => {
    state.relocationData = null;
    state.redzone = null;
    state.habitations = null;
  });
  $("severity-select").addEventListener("change", (e) => {
    state.severity = e.target.value;
    setScreen(state.screen);
  });
  $("live-toggle").addEventListener("click", (e) => {
    state.liveRainfall = !state.liveRainfall;
    const btn = e.currentTarget;
    btn.setAttribute("aria-pressed", String(state.liveRainfall));
    // Real physics on real rainfall takes a few seconds the first time a
    // district is asked for; the button says so rather than looking stuck.
    // loadDistrict() clears this itself once the real data (or the honest
    // fallback) actually arrives — not this handler, which would otherwise
    // clear it the instant the fetch merely *starts*.
    btn.classList.toggle("is-loading", state.liveRainfall);
    if (state.liveRainfall) {
      btn.querySelector(".live-toggle-label").textContent = "Reading live rain…";
    }
    loadDistrict();
  });
  document.querySelectorAll("#national-view .tab").forEach((t) =>
    t.addEventListener("click", () => {
      state.nationalView = t.dataset.nview;
      document.querySelectorAll("#national-view .tab").forEach((o) =>
        o.setAttribute("aria-selected", String(o === t)));
      loadNational();
    }));

  document.querySelectorAll("#screen-tabs .tab").forEach((t) =>
    t.addEventListener("click", () => setGroup(t.dataset.group)));

  const wdate = $("watch-date");
  if (wdate) wdate.addEventListener("change", (e) => {
    state.watchDate = e.target.value;
    loadWatch();
  });

  $("opacity").addEventListener("input", (e) => {
    const v = Number(e.target.value);
    $("opacity-val").textContent = `${v}%`;
    map.setOpacity(v / 100);
  });

  // History scrubber. Steps in years and accumulates, because one year of
  // Indian disaster history is a handful of dots and the built-up record is
  // the thing worth looking at.
  const histSlider = $("hist-year");
  if (histSlider) {
    histSlider.max = String(new Date().getFullYear());
    histSlider.addEventListener("input", (e) => {
      stopHistoryPlay();
      setHistoryYear(Number(e.target.value));
    });
  }
  const histPlay = $("hist-play");
  if (histPlay) {
    histPlay.addEventListener("click", () => {
      if (historyTimer) { stopHistoryPlay(); return; }
      const max = Number(histSlider.max);
      if (state.historyYear >= max) setHistoryYear(1990);
      $("hist-play-icon").setAttribute("d", "M2 1h3v10H2zM7 1h3v10H7z");
      historyTimer = setInterval(() => {
        const next = state.historyYear + 1;
        if (next > max) { stopHistoryPlay(); return; }
        histSlider.value = String(next);
        setHistoryYear(next);
      }, 420);
    });
  }

  $("theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const light = root.getAttribute("data-theme") === "light";
    root.setAttribute("data-theme", light ? "dark" : "light");
    $("theme-toggle").textContent = light ? "Light" : "Dark";
    timeline.draw();
  });

  // Space plays the timeline; left/right step an hour. Keyboard control of the
  // time slider is what makes a live demonstration feel controlled.
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    if (e.code === "Space") { e.preventDefault(); timeline.toggle(); }
    if (e.code === "ArrowLeft") timeline.setHour(timeline.hour - 1);
    if (e.code === "ArrowRight") timeline.setHour(timeline.hour + 1);
  });


  // The board opens the application. Anyone arriving cold should be looking at
  // what needs attention today, not at a district they have not chosen yet.
  setScreen("story");
}

boot();
