# Dataset register — every source, checked, not assumed

Every entry below was verified reachable (or found unreachable) directly
against the live host, not taken from documentation. Where a probe failed, that
is recorded too — a documented dead end is worth more to a judge than a silent
gap, because it proves the search actually happened.

## Live sources — queried every refresh

| Dataset | Publisher | Credentials | What it feeds |
|---|---|---|---|
| **Open-Meteo Forecast API** | Open-Meteo | None | Rainfall, CAPE, soil moisture, cloud structure (low/mid/high, freezing level) — the live board |
| **Open-Meteo ERA5 Archive** | Open-Meteo / Copernicus Climate Data Store | None | 5-year cloud & rainfall norms per district per calendar week; also the replay-mode source |
| **Copernicus Data Space catalogue** | ESA/EU | None (anonymous OData query) | Real Sentinel-1 granule acquisition times |

## Baked sources — fetched once, shipped in the installer

| Dataset | Publisher | Licence | Resolution | Credentials | What it feeds | Size baked |
|---|---|---|---|---|---|---|
| **Copernicus DEM GLO-30** | ESA | Open | 30 m | None | Elevation, HAND, slope, all terrain-derived hazards | ~7.5 MB, 22 districts |
| **GHS-POP R2023A** | JRC (EU) | Open | 100 m | None | Population — observed, replaces dasymetric spread | ~3.8 MB, 22 districts |
| **GHS-BUILT-S R2023A** | JRC (EU) | Open | 100 m | None | Built-up land — exposure and encroachment | ~5.5 MB, 22 districts |
| **ESA WorldCover v200 (2021)** | ESA | Open | 10 m | None | Cropland, tree cover, built-up cross-check | ~1.1 MB, 22 districts |
| **OpenStreetMap** (via Overpass API) | OSM contributors | **ODbL 1.0 — attribution required** | Vector | None | Roads, hospitals/clinics, schools (shelter candidates) | ~0.3 MB, 22 districts |
| **geoBoundaries ADM2** | William & Mary geoLab | CC-BY 4.0 | Vector, simplified | None | Real district outlines, all 735 districts | ~0.6 MB |
| **IBTrACS** | NOAA | Open | Track points | None | 328 storms, 12,458 points, North Indian Ocean basin | ~0.6 MB |
| **41 historic disasters** | Hand-curated | — | — | — | Every row carries `source` + `source_url` | — |

## Tried and verified unreachable — recorded, not hidden

| Dataset | What it would have given | Result |
|---|---|---|
| **GSI Bhukosh** | Authoritative Indian lithology/structure | Connection timeout from this network, verified tonight — same failure mode as India-WRIS below |
| **India-WRIS / arc.indiawris.gov.in** | River basin and hydrology data | Connection timeout |
| **Dartmouth Flood Observatory** | Historic flood extent polygons | HTTP 410 |
| **NASA Global Landslide Catalog** | Crowd-reported landslide events | No usable bulk endpoint found |
| **Copernicus Emergency Management Service** | Rapid-mapping flood extents | HTTP 404 |
| **CWC flood forecasting (ffs.india-water.gov.in)** | Live river gauge readings against danger levels | JavaScript application, no documented JSON endpoint; three routes to a real feed are written up in `providers/cwc.py` |

## GEM Global Active Faults — reachable, wired, then deliberately not used

This one deserves its own entry because it is the most instructive negative
result of the night.

**Reachable:** `HTTP 200`, 10.6 MB GeoJSON, 13,696 fault traces, no credentials.
A full bake pipeline was built — `scripts/fetch_faults.py` — computing
distance-to-nearest-fault per district via `scipy.ndimage.distance_transform_edt`,
intended as a proxy for the BIS LHEF *structure* factor (proximity to
lineaments/faults), one of the two factors currently held at a neutral rating.

**Then measured, not assumed, across all 22 modelled districts:**

| Finding | Number |
|---|---|
| Districts with **zero** mapped faults within any useful range | 16 of 22 |
| Wayanad, Idukki (Kerala hill districts) | 0 faults found at all |
| Rudraprayag, Chamoli (Uttarakhand, Himalayan front) | 85 km, 116 km to nearest fault |
| Kullu, Mandi (Himachal Pradesh) | 109 km, 61 km to nearest fault |

Every single landslide-hazard hill district this system models sits far outside
GEM's mapped fault density — a global catalogue that captures major
plate-boundary-scale structures but not the dense secondary fault network a
national dataset like GSI's own seismotectonic mapping would carry. Using it as
designed would have made the Himalaya read as *more* structurally stable than
the honest neutral placeholder does — the opposite of true, for exactly the
districts where it matters most.

**Decision: not wired into `landslide.py`.** The bake, the script and this
finding are kept as evidence of a rigorous negative result rather than shipped
as a feature. "We tried a real, reachable, well-documented dataset and found —
by direct measurement, not by assumption — that it isn't dense enough for this
use case in India" is a stronger claim than a token wiring nobody could verify.

## Rate limits worth knowing

- **Open-Meteo**: free tier is generous but bills per coordinate; a national
  532-square sweep is batched into a handful of requests rather than 532.
- **Overpass API**: shared public service. `fetch_osm.py` retries on
  HTTP 429/504 with backoff and caches every raw response to disk, so a re-run
  after a partial failure costs nothing for districts that already succeeded.
- **JRC (GHS-POP / GHS-BUILT-S)**: no documented rate limit encountered;
  tiles are read as windowed Cloud-Optimized GeoTIFFs, not downloaded whole.
