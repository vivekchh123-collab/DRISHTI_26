/* Fetch layer.
 *
 * Every call returns data or throws a typed error the UI can render. Silent
 * failures are the enemy here: an operations screen that quietly shows stale
 * numbers during a flood is worse than one that says plainly it could not
 * reach the server.
 */

const cache = new Map();

export class ApiError extends Error {
  constructor(message, status, url) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

async function get(url, { useCache = true } = {}) {
  if (useCache && cache.has(url)) return cache.get(url);
  let res;
  try {
    res = await fetch(url, { headers: { Accept: "application/json" } });
  } catch (cause) {
    throw new ApiError("Cannot reach the DRISHTI server.", 0, url);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* keep status text */ }
    throw new ApiError(detail, res.status, url);
  }
  const data = await res.json();
  if (useCache) cache.set(url, data);
  return data;
}

const q = (severity) => `?severity=${encodeURIComponent(severity)}`;

// Every endpoint the district screen touches has to agree on whether it is
// describing the demo event or the live one — a map on the synthetic storm
// next to a ranked-zone list claiming to be live would be a worse failure
// than either being wrong alone, because it reads as two different answers to
// the same question. This is the one place that query string is built.
const qLive = (sev, live) =>
  `?severity=${encodeURIComponent(sev)}${live ? "&live=true" : ""}`;

export const api = {
  districts:    (sev)       => get(`/api/districts${q(sev)}`),
  district:     (code, sev, live) => get(`/api/districts/${code}${qLive(sev, live)}`,
                                         { useCache: !live }),
  assessment:   (code, sev, live) => get(`/api/districts/${code}/assessment${qLive(sev, live)}`,
                                         { useCache: !live }),
  timeline:     (code, sev, live) => get(`/api/districts/${code}/timeline${qLive(sev, live)}`,
                                         { useCache: !live }),
  waterlogging: (code, sev) => get(`/api/districts/${code}/waterlogging${q(sev)}`),
  layers:       (code, sev, live) => get(`/api/districts/${code}/layers${qLive(sev, live)}`,
                                         { useCache: !live }),
  methods:      ()          => get(`/api/methods`),
  national:     (size=0.75) => get(`/api/national?size_deg=${size}`),
  nationalDistricts: () => get(`/api/national/districts`),
  watch:        (date, force = false) => {
    const params = [];
    if (date) params.push(`date=${date}`);
    if (force) params.push(`force=true&_=${Date.now()}`);   // real re-read, never a cache hit
    const url = `/api/watch${params.length ? `?${params.join("&")}` : ""}`;
    return get(url, { useCache: !force });
  },
  liveDistrict: (code)      => get(`/api/live/${code}`),
  ml:           ()          => get(`/api/ml`),
  deforestation:(code, c=0.3) => get(`/api/districts/${code}/deforestation?clearance=${c}`),
  scenes:       (code, sev) => get(`/api/districts/${code}/scenes${q(sev)}`),

  // Red-zone endpoints take no severity: a red zone is a property of the
  // place across events, not the outcome of one storm.
  redzones:     (code) => get(`/api/districts/${code}/redzones`),
  // Click-to-justify: why this exact point is, or is not, a Red Zone. Shared
  // by the map click handler and the relocation habitation detail — one
  // justification engine, not two competing explanations of the same ground.
  explainCell:  (code, lat, lon) =>
                  get(`/api/districts/${code}/redzones/explain?lat=${lat}&lon=${lon}`),
  habitations:  (code) => get(`/api/districts/${code}/habitations`),
  relocation:   (code) => get(`/api/districts/${code}/relocation`),
  assessmentLayers: (code) => get(`/api/districts/${code}/assessment-layers`),
  mitigation:   (code) => get(`/api/districts/${code}/mitigation`),
  encroachment: (code) => get(`/api/districts/${code}/encroachment`),
  states:       ()     => get(`/api/states`),
  state:        (name) => get(`/api/states/${encodeURIComponent(name)}`),
  exportUrl(scope) {
    const p = new URLSearchParams(scope);
    return `/api/export/habitations.csv?${p}`;
  },

  // Observed history. Cyclone tracks are live from IBTrACS; flood and landslide
  // rows are curated with a citation each.
  history:      (from="1990-01-01", to="2099-12-31") =>
                  get(`/api/history?start=${from}&end=${to}`),
  historyValidation: (code) => code
                  ? get(`/api/history/validation/${code}`)
                  : get(`/api/history/validation`),

  assessmentLayerUrl(code, name) {
    return `/api/districts/${code}/assessment-layers/${name}.png`;
  },

  layerUrl(code, name, sev, hour, live) {
    const p = new URLSearchParams({ severity: sev });
    if (hour != null) p.set("hour", String(hour));
    if (live) p.set("live", "true");
    return `/api/districts/${code}/layers/${name}.png?${p}`;
  },
};

/* Formatting helpers. Indian digit grouping throughout — a District Magistrate
 * reads 19,77,106, not 1,977,106. */
export const fmt = {
  int: (n) => Number(n || 0).toLocaleString("en-IN"),
  num: (n, d = 1) => Number(n || 0).toLocaleString("en-IN",
        { minimumFractionDigits: d, maximumFractionDigits: d }),
  km2: (n) => `${fmt.num(n, 1)} km²`,
  m:   (n) => `${fmt.num(n, 2)} m`,
  pct: (n) => `${fmt.num(n, 1)}%`,
  hours(h) {
    h = Math.round(h || 0);
    if (h < 48) return `${h} h`;
    return `${fmt.num(h / 24, 1)} days`;
  },
  compact(n) {
    n = Number(n || 0);
    if (n >= 1e7) return `${fmt.num(n / 1e7, 2)} Cr`;
    if (n >= 1e5) return `${fmt.num(n / 1e5, 2)} L`;
    if (n >= 1e3) return `${fmt.num(n / 1e3, 1)} K`;
    return String(Math.round(n));
  },
};

/* Relocation horizon -> semantic tone. One place, so the pill in the list and
 * the colour on the map can never disagree. */
export const HORIZON_TONE = {
  "immediate": "severe",
  "short-term": "alert",
  "medium-term": "watch",
  "monitor": "ok",
};

/* Severity tone from water depth. One place, so the pill in the list and the
 * colour on the map can never disagree. */
export function depthTone(m) {
  if (m >= 2.0) return "severe";
  if (m >= 1.0) return "alert";
  if (m >= 0.3) return "watch";
  return "ok";
}
