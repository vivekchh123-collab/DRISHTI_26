/* Map: Leaflet with our own relief basemap.
 *
 * The base image is rendered server-side from the DEM we already compute, so
 * the map draws with no tile server and no internet. An external tile layer can
 * be switched on when a connection exists, but nothing depends on it.
 */

import { api, fmt, depthTone, HORIZON_TONE } from "./api.js";

const TONE = { severe: "#DC5B4B", alert: "#E08A3C", watch: "#D8B33C", ok: "#3FA86B" };

export class FloodMap {
  constructor(el) {
    this.map = L.map(el, {
      zoomControl: true,
      attributionControl: false,
      zoomSnap: 0.25,
      minZoom: 3,
    });
    L.control.zoom({ position: "bottomleft" }).addTo(this.map);
    this.base = null;
    this.overlay = null;
    this.vectors = L.layerGroup().addTo(this.map);
    this.markers = new Map();      // zone id -> circle marker
    this.opacity = 0.85;
    this.onZoneClick = null;
    this._tiles = null;
  }

  /* --- base relief ------------------------------------------------------- */

  setDistrict(code, severity, bounds, live = false) {
    this.code = code;
    this.severity = severity;
    this.live = live;          // basemap is terrain-only and unaffected by it
    this.bounds = L.latLngBounds(bounds);

    if (this.base) this.base.remove();
    this.base = L.imageOverlay(api.layerUrl(code, "basemap", severity), this.bounds,
                               { opacity: 1, className: "base-relief" });
    this.base.addTo(this.map);
    this.base.bringToBack();
    this.map.fitBounds(this.bounds, { padding: [12, 12] });
  }

  /* --- data overlay ------------------------------------------------------ */

  setOverlay(name, hour) {
    if (!this.code) return;
    const url = api.layerUrl(this.code, name, this.severity, hour, this.live);
    // Swap by loading the new image before removing the old one, so the map
    // never flashes empty while the time slider is being dragged.
    //
    // `prev` is captured up front rather than read inside the load handler. The
    // handler used to remove `this.overlay`, which on the very first call was
    // the image being loaded — so the layer removed itself the moment it
    // arrived and the map showed only the basemap.
    const prev = this.overlay;
    const next = L.imageOverlay(url, this.bounds, { opacity: 0 });
    this.overlay = next;
    next.addTo(this.map);
    next.once("load", () => {
      next.setOpacity(this.opacity);
      if (prev && prev !== next) prev.remove();
      this._raiseVectors();
    });
    next.once("error", () => {
      next.remove();
      if (this.overlay === next) this.overlay = prev;
    });
  }

  setOpacity(v) {
    this.opacity = v;
    if (this.overlay) this.overlay.setOpacity(v);
  }

  clearOverlay() {
    if (this.overlay) { this.overlay.remove(); this.overlay = null; }
  }

  _raiseVectors() {
    this.vectors.eachLayer((l) => l.bringToFront && l.bringToFront());
  }

  /* --- vectors ----------------------------------------------------------- */

  setBoundary(geojson) {
    this._boundary = L.geoJSON(geojson, {
      style: {
        color: "#7DC3EC", weight: 1.5, opacity: 0.85,
        fill: false, dashArray: "4 3",
      },
      interactive: false,
    }).addTo(this.vectors);
  }

  setZones(zones) {
    this.markers.clear();
    zones.forEach((z) => {
      const tone = z.is_cut_off ? "severe" : depthTone(z.max_depth_m);
      const m = L.circleMarker([z.lat, z.lon], {
        radius: 6 + Math.min(9, Math.sqrt(z.population_affected) / 130),
        color: "#fff", weight: 1.5, opacity: 0.9,
        fillColor: TONE[tone], fillOpacity: 0.85,
      });
      m.bindTooltip(
        `<strong>#${z.rank} ${z.label}</strong><br>` +
        `${fmt.int(z.population_affected)} affected · ${fmt.m(z.max_depth_m)}` +
        (z.is_cut_off ? " · <span style='color:#DC5B4B'>ROAD CUT</span>" : ""),
        { direction: "top", opacity: 0.96 });
      m.on("click", () => this.onZoneClick && this.onZoneClick(z.id));
      m.addTo(this.vectors);
      this.markers.set(z.id, m);
    });
  }

  setFacilities(facilities) {
    facilities.filter((f) => f.kind === "hospital").slice(0, 40).forEach((f) => {
      L.circleMarker([f.lat, f.lon], {
        radius: 3, color: "#3FA86B", weight: 1.2, opacity: 0.9,
        fillColor: "#3FA86B", fillOpacity: 0.5, interactive: true,
      }).bindTooltip(`${f.name} · ${f.capacity} beds`, { direction: "top" })
        .addTo(this.vectors);
    });
  }

  setHotspots(spots) {
    spots.forEach((h) => {
      L.circleMarker([h.lat, h.lon], {
        radius: 5 + Math.min(10, h.drain_down_hours / 30),
        color: "#fff", weight: 1.4, opacity: 0.9,
        fillColor: "#2A72A8", fillOpacity: 0.8,
      }).bindTooltip(
        `<strong>${h.label}</strong><br>drains in ${fmt.hours(h.drain_down_hours)} · ` +
        `${fmt.int(h.population)} people`, { direction: "top" })
        .addTo(this.vectors);
    });
  }

  highlight(zoneId) {
    this.markers.forEach((m, id) => {
      m.setStyle({ weight: id === zoneId ? 3.5 : 1.5,
                   color: id === zoneId ? "#7DC3EC" : "#fff" });
      if (id === zoneId) m.bringToFront();
    });
  }

  flyToZone(zoneId) {
    const m = this.markers.get(zoneId);
    if (m) this.map.flyTo(m.getLatLng(), Math.max(this.map.getZoom(), 11),
                          { duration: 0.6 });
  }

  clearVectors() {
    this.vectors.clearLayers();
    this.markers.clear();
  }

  /* --- red zones and relocation ------------------------------------------ */

  setAssessmentOverlay(name) {
    if (!this.code) return;
    const prev = this.overlay;
    const next = L.imageOverlay(api.assessmentLayerUrl(this.code, name),
                                this.bounds, { opacity: 0 });
    this.overlay = next;
    next.addTo(this.map);
    next.once("load", () => {
      next.setOpacity(this.opacity);
      if (prev && prev !== next) prev.remove();
      this._raiseVectors();
    });
    next.once("error", () => {
      next.remove();
      if (this.overlay === next) this.overlay = prev;
    });
  }

  setHabitations(habs) {
    this.markers.clear();
    habs.filter((h) => h.relocation_horizon !== "monitor").forEach((h) => {
      const tone = HORIZON_TONE[h.relocation_horizon] || "ok";
      const m = L.circleMarker([h.lat, h.lon], {
        radius: 6 + Math.min(10, Math.sqrt(h.population_in_red_zone) / 90),
        color: "#fff", weight: 1.6, opacity: 0.92,
        fillColor: TONE[tone], fillOpacity: 0.85,
      });
      m.bindTooltip(
        `<strong>#${h.rank} ${h.label}</strong><br>` +
        `${fmt.int(h.population_in_red_zone)} of ${fmt.int(h.population)} in red zone<br>` +
        `recurs every ${h.return_period_years ?? "—"} yr · ` +
        `<strong>${h.relocation_horizon}</strong>`,
        { direction: "top", opacity: 0.96 });
      m.on("click", () => this.onZoneClick && this.onZoneClick(h.id));
      m.addTo(this.vectors);
      this.markers.set(h.id, m);
    });
  }

  setSites(sites) {
    sites.forEach((s) => {
      // Square markers, deliberately: destinations must be visually distinct
      // from the round hazard markers they receive people from.
      const px = 9 + Math.min(11, Math.sqrt(s.capacity) / 26);
      L.marker([s.lat, s.lon], {
        icon: L.divIcon({
          className: "",
          iconSize: [px, px],
          iconAnchor: [px / 2, px / 2],
          html: `<div style="width:${px}px;height:${px}px;background:#3FA86B;
                   border:1.6px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.5)"></div>`,
        }),
      }).bindTooltip(
        `<strong>${s.label}</strong><br>relocation site · capacity ` +
        `${fmt.int(s.capacity)}<br>${fmt.num(s.area_km2, 1)} km² · ` +
        `road ${fmt.num(s.road_distance_km, 1)} km`,
        { direction: "top" }).addTo(this.vectors);
    });
  }

  drawMoves(plans, habs) {
    // Draw the actual displacement: origin to destination, weighted by people.
    const byId = new Map(habs.map((h) => [h.id, h]));
    plans.forEach((p) => {
      const h = byId.get(p.habitation_id);
      if (!h) return;
      p.assignments.forEach((a) => {
        L.polyline([[h.lat, h.lon], [a.lat, a.lon]], {
          color: "#7DC3EC", weight: 1 + Math.min(4, a.people / 6000),
          opacity: 0.55, dashArray: "5 4", interactive: false,
        }).addTo(this.vectors);
      });
    });
  }

  /* --- state rollup ------------------------------------------------------- */

  /** Districts of one state as proportional markers, sized by caseload. */
  showStateDistricts(rows, allDistricts, onPick) {
    this.clearVectors();
    this.clearOverlay();
    if (this.base) { this.base.remove(); this.base = null; }
    this.code = null;

    const byCode = new Map((allDistricts || []).map((d) => [d.code, d]));
    const pts = [];
    rows.forEach((r, i) => {
      const d = byCode.get(r.code);
      if (!d) return;
      const lat = d.centroid ? d.centroid.lat : d.lat;
      const lon = d.centroid ? d.centroid.lon : d.lon;
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      pts.push([lat, lon]);

      // Area-proportional: a district with four times the caseload draws twice
      // the radius, because the eye reads area rather than radius and scaling
      // radius directly would overstate the comparison fourfold.
      const r0 = 9 + Math.sqrt(r.population_in_red_zone / 1000) * 1.5;
      const tone = i === 0 ? "#C4362C" : i < 3 ? "#E07B39" : "#5E8C6A";
      const m = L.circleMarker([lat, lon], {
        radius: Math.min(r0, 38),
        color: tone, weight: 2, fillColor: tone, fillOpacity: 0.32,
      });
      m.bindTooltip(
        `<b>${r.name}</b><br>${r.population_in_red_zone.toLocaleString("en-IN")}`
        + ` in red zone<br>${Math.round(r.red_zone_km2)} km²`
        + `<br>${r.by_horizon.immediate} immediate`,
        { sticky: true });
      m.on("click", () => onPick && onPick(r.code));
      this.vectors.addLayer(m);
    });
    if (pts.length) this.map.fitBounds(L.latLngBounds(pts).pad(0.35));
  }

  /* --- observed history --------------------------------------------------- */

  /** Clear the map down to a bare view of India, for the history screen. */
  showIndia() {
    this.clearVectors();
    this.clearOverlay();
    if (this.base) { this.base.remove(); this.base = null; }
    this.code = null;
    this.map.fitBounds([[6.5, 67.0], [36.5, 97.5]], { padding: [12, 12] });
  }

  /** Centre on a point without disturbing any layer group already attached. */
  flyTo(lat, lon, zoom = 8) {
    this.map.flyTo([lat, lon], Math.max(this.map.getZoom(), zoom),
                   { duration: 0.6 });
  }

  /* --- the live watch board ---------------------------------------------- */

  /** Districts needing attention, sized by urgency and coloured by action.
   *
   *  Only districts actually on the board are drawn. Plotting all 22 with the
   *  quiet ones in a calm colour sounds more complete, but it buries the three
   *  that matter in a field of dots — and the entire purpose of this screen is
   *  that a duty officer can see, without reading, where to look first.
   */
  showWatchBoard(rows, onPick) {
    this.clearVectors();
    this.clearOverlay();
    if (this.base) { this.base.remove(); this.base = null; }
    this.code = null;

    const TONE = { ACT: "#C4362C", PREPARE: "#E07B39", WATCH: "#D8B33C" };
    const pts = [];
    (rows || []).forEach((r) => {
      const tone = TONE[r.action];
      if (!tone) return;                       // ROUTINE districts stay off
      if (!Number.isFinite(r.lat) || !Number.isFinite(r.lon)) return;
      pts.push([r.lat, r.lon]);

      const m = L.circleMarker([r.lat, r.lon], {
        radius: Math.min(10 + r.urgency * 0.32, 34),
        color: tone, weight: 2, fillColor: tone, fillOpacity: 0.3,
      });
      const pop = (r.exposure && r.exposure.population_in_red_zone) || 0;
      m.bindTooltip(
        `<b>${r.name}</b> &middot; ${r.action}<br>`
        + `urgency ${Math.round(r.urgency)}<br>`
        + `cloud: ${r.cloud.state}<br>`
        + (pop ? `${pop.toLocaleString("en-IN")} in red zone` : "")
        , { sticky: true });
      m.on("click", () => onPick && onPick(r.code));
      this.vectors.addLayer(m);
    });

    if (pts.length) this.map.fitBounds(L.latLngBounds(pts).pad(0.4));
    else this.map.fitBounds([[6.5, 67.0], [36.5, 97.5]], { padding: [12, 12] });
  }

  /* --- national live grid ------------------------------------------------ */

  showNationalGrid(cells, onPick) {
    this.clearVectors();
    this.clearOverlay();
    if (this.base) { this.base.remove(); this.base = null; }
    this.code = null;

    // Squares, not dots. The screen tessellates the country and scores every
    // tile, so drawing it as tiles is what the data actually is — and a filled
    // grid reads as coverage in a way scattered markers never do.
    cells.forEach((c) => {
      const s = c.score;
      const fill = s >= 60 ? "#C4362C" : s >= 45 ? "#E07B39"
                 : s >= 30 ? "#D8B33C" : s >= 18 ? "#5E8C6A" : "#24485F";
      const rect = L.rectangle(c.bounds, {
        color: c.modelled ? "#7DC3EC" : "rgba(255,255,255,.10)",
        weight: c.modelled ? 1.4 : 0.4,
        fillColor: fill,
        fillOpacity: 0.18 + 0.55 * Math.min(1, s / 70),
      });
      const drivers = c.drivers.length
        ? "<br>" + c.drivers.map((d) => "&bull; " + d).join("<br>")
        : "";
      rect.bindTooltip(
        `<strong>${c.id}</strong> &nbsp; score ${c.score.toFixed(0)}<br>` +
        `IMD band: <strong>${c.band}</strong><br>` +
        `rain 24h ${c.rain_next_24h_mm} mm &middot; 72h ${c.rain_next_72h_mm} mm<br>` +
        `CAPE ${c.cape} J/kg (${c.cape_band}) &middot; soil ${(c.soil_saturation * 100).toFixed(0)}%` +
        (c.districts.length ? `<br><em>modelled: ${c.districts.join(", ")}</em>` : "") +
        drivers,
        { direction: "top", opacity: 0.97 });
      if (c.districts.length && onPick) {
        rect.on("click", () => onPick(c.districts[0]));
      }
      rect.addTo(this.vectors);
    });
    this.map.setView([22.5, 81.0], 4.6);
  }

  /* --- national view ----------------------------------------------------- */

  showNational(rows, onPick) {
    this.clearVectors();
    this.clearOverlay();
    if (this.base) { this.base.remove(); this.base = null; }
    this.code = null;

    rows.forEach((d) => {
      const risk = d.risk_score || 0;
      const tone = risk >= 70 ? "severe" : risk >= 55 ? "alert"
                 : risk >= 40 ? "watch" : "ok";
      L.circleMarker([d.lat, d.lon], {
        radius: 5 + Math.sqrt(d.population_2025_estimate) / 700,
        color: d.modelled ? "#fff" : "rgba(255,255,255,.45)",
        weight: d.modelled ? 1.8 : 1,
        fillColor: TONE[tone],
        fillOpacity: d.modelled ? 0.9 : 0.55,
      }).bindTooltip(
        `<strong>${d.name}</strong>, ${d.state}<br>` +
        `${fmt.compact(d.population_2025_estimate)} people · ${d.flood_driver} flood<br>` +
        (d.modelled
          ? `<span style="color:#E08A3C">modelled: ${fmt.int(d.population_affected)} affected</span>`
          : `<span style="color:#677C8F">standing exposure estimate</span>`),
        { direction: "top" })
        .on("click", () => onPick(d.code))
        .addTo(this.vectors);
    });
    this.map.setView([21.5, 81.0], 4.4);
  }

  invalidate() { setTimeout(() => this.map.invalidateSize(), 60); }
}
