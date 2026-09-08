# DRISHTI

**District Risk Intelligence & Satellite Hazard Tracking Interface**

**SIH26191** — Ministry of Home Affairs / NDRF, DM Division.

A GIS decision-support platform for State Disaster Management Authorities. It
identifies **multi-hazard Red Zones** unsuitable for permanent habitation,
assesses the **carrying capacity** of safer relocation sites, and **ranks
vulnerable habitations** for immediate, short-term or medium-term relocation.

> Relocation today starts after the disaster. This says which villages to move
> before the next one.

---

## Run it

**As a desktop application** — no Python, no Node, nothing to install:

```
dist/installer/DRISHTI-Setup-1.0.0.exe
```

**From source:**

```bash
python scripts/vendor_assets.py   # once, needs network — makes the app fully offline
./run.sh                          # macOS / Linux
run.bat                           # Windows
```

Then open <http://127.0.0.1:8000>. API documentation is at `/docs`.

**Build the installer:** `python scripts/build_desktop.py` — see
[docs/DESKTOP_BUILD.md](docs/DESKTOP_BUILD.md).

Requires Python 3.9+. **No GPU, no GDAL, no Node, no build step.** The only hard
dependencies are `numpy`, `fastapi` and `uvicorn`.

---

## What it does

```
                 5-year  ┐
  four hazards   25-year ─┼─► recurrence ──► RED ZONE ──► habitations ──► relocation
  at 3 events    100-year┘   (return period)  (unsuitable)   (ranked +      (sites, capacity,
                                                               horizon)        allocation)
  flood · landslide · waterlogging · coastal erosion
```

A place chest-deep in water once a century is a place you protect. The same
depth every fifth monsoon is a place you **move**. The severity of any single
event says almost nothing — **recurrence** is the number a relocation decision
needs, and it is what no existing portal reports.

Four screens:

| Screen | Question it answers |
|---|---|
| **Red zones** | Which land is unfit to live on, how often, and because of what? |
| **Relocation** | Where can these people go, and is there room? |
| **Event** | What does one flood actually look like? (supporting evidence) |
| **National** | Which district needs attention first? |

---

## The ideas worth knowing

**Recurrence over severity.** Every cell records the shortest return period at
which any hazard renders it uninhabitable. That one number is physically
meaningful, maps directly onto a relocation horizon, and turns "this flooded
badly once" into "this is unliveable every five years".

**HAND** — Height Above Nearest Drainage — is the number that makes this cheap.
For every cell it records how far above its own river that cell sits. A cell
2 m above the river floods when the river rises 2 m, wherever it is on the map.
Compute it once from elevation, and the flooding order of an entire district is
known before a drop of rain falls. Inundation becomes a subtraction rather than
a hydrodynamic solve, which is why a district resolves in about a second and a
half on a laptop.

**Indian standards where they exist.** Landslide hazard follows **BIS IS 14496
(Part 2)** — the LHEF rating scheme the Geological Survey of India uses — rather
than an invented weighted overlay. A State Disaster Management Authority can
only act on a red zone if the method behind it is one their own guidelines
already recognise. Two of the six LHEF factors need a GSI map and are held at a
neutral rating; the API says so on every response.

**Radar** still matters, as evidence rather than as alarm. Sentinel-1's C-band
goes through storm cloud and works at night, and open water reflects the pulse
away and appears black — which is what lets inundation be observed rather than
only modelled.

---

## Layout

```
backend/app/
  core/
    grid.py          analysis frame, 160x160 per district
    terrain.py       Horn slope, priority-flood, Garbrecht-Martz flats,
                     D8 routing, HAND, TWI, drainage density
    sar.py           Lee speckle filter, Otsu threshold, change detection
    landslide.py     BIS IS 14496 LHEF rating, Caine trigger, runout
    deforestation.py root cohesion decay, curve-number shift, counterfactual
    national.py      532-square live screen over India, IMD warning bands
    ml.py            water classifier and red-zone surrogate, district-held-out
    erosion.py       Bruun shoreline retreat, wave and sediment modulation
    redzone.py       multi-hazard recurrence, unsuitability, horizons
    relocation.py    site suitability, carrying capacity, allocation
    rainfall.py      design storms, antecedent wetness
    hydrology.py     SCS Curve Number, unit hydrograph, compound-channel Manning
    flood.py         HAND inundation, depth, duration, storm surge
    waterlogging.py  susceptibility overlay, drain-down time
    exposure.py      dasymetric population, roads, facilities
    impact.py        zone ranking, cut-off detection
    playbook / response.py   NDMA SOP engine, shelters, routes, supplies
    render.py        pure-Python PNG encoder, hillshade, colour ramps
    scenario.py      orchestration and caching
  api/routes.py      HTTP surface
  data/              districts, SOP rules, citation table
frontend/            vanilla ES modules + Leaflet, no build step
scripts/             vendor_assets, bench, validate_hindcast
docs/                METHODOLOGY, ASSUMPTIONS, DEMO_SCRIPT
```

---

## Verify it

```bash
cd backend && python -m pytest -q     # 308 passing, 6 skipped
python scripts/bench.py               # per-stage timings, all 22 districts
python scripts/validate_hindcast.py   # detector scored against known truth
```

Measured on a laptop, single core, no GPU:

| | |
|---|---|
| Median district build | **1.50 s** |
| Slowest district | 3.13 s |
| All 22 districts | 32.8 s |
| SAR detection, plains and deltas | precision **0.79–0.86**, F1 **0.75–0.83** |
| SAR detection, steep terrain | F1 **0.26** — a C-band limitation, not a code one |
| SAR detection (ML, held-out districts) | precision **0.95**, recall **0.98**, F1 **0.96** |
| Red-zone surrogate | R² **0.77**, MAE **0.06** |
| Full red-zone assessment | ~5–11 s per district (three events + four hazards) |

The tests are the interesting part. They check the published algorithms against
cases with hand-computable answers — D8 on a tilted plane, Otsu on a histogram
whose optimum is known analytically, HAND on a constructed ramp, priority-flood
on a pit whose spill level can be reasoned out — plus physical invariants
(runoff never exceeds rainfall, extent grows monotonically with stage,
population is conserved) and byte-level determinism.

The most important single test is
`test_no_relocation_site_is_inside_a_red_zone`. Everything else is correctness;
that one is safety. A bug that let the allocator send people from one hazard
zone into another would be invisible on the map and obvious in an inquiry.

---

## Honesty

**Elevation is real.** Copernicus DEM GLO-30 (ESA, 30 m) is baked onto each
district grid by `scripts/fetch_real_dem.py` and ships as 1.8 MB of compressed
numpy, so HAND, slope, wetness, flow routing, the BIS landslide rating and the
erosion model are all derived from real terrain — with no GDAL and no network at
runtime. Rainfall, soil moisture and Sentinel-1 pass times are live.

**The radar scene is still simulated**, and population, boundaries and land cover
are still modelled. Every API
response carries a `provenance` block saying so and the interface shows a
permanent badge.

The flood model has **not** been validated against a real historic event, and it
cannot be on synthetic elevation. `scripts/validate_hindcast.py` implements that
comparison and states exactly which two files it needs to run for real.

Full register: [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).

---

## Going live

Real data drops in without touching the models:

| Layer | Source | Status |
|---|---|---|
| **Radar catalogue** | Copernicus Sentinel-1 | ✅ **Live now.** `providers/copernicus.py` queries the real OData catalogue and needs no credentials to search |
| Elevation | SRTM / CartoDEM / NASADEM | Drop in a GeoTIFF — `terrain.load_dem_geotiff` (needs `rasterio`) |
| **Geology** | Geological Survey of India | `landslide.load_geology` — promotes LHEF to a complete 6-factor rating |
| Boundaries | Survey of India / LGD | Drop in GeoJSON at `data/boundaries/districts.geojson` |
| Granule download | Copernicus | Needs `COPERNICUS_USER` / `COPERNICUS_PASSWORD` |
| River stage | Central Water Commission | Not implemented |
| Rainfall | GPM IMERG, IMD | Not implemented |
| Indian EO | ISRO Bhoonidhi, Bhuvan, MOSDAC | Not implemented |

`GET /api/providers` via `/api/methods` reports which of these can run right now
and exactly what each missing one needs. The district screen shows the age of the
**real** most recent Sentinel-1 pass over that district, queried live.

---
[![Live Demo](https://img.shields.io/badge/Live_Demo-Render-46E3B7?style=for-the-badge&logo=render)](https://your-app.onrender.com)

## Methods

Every algorithm is published and cited, in code, at `/api/methods`, and in
[docs/METHODOLOGY.md](docs/METHODOLOGY.md): **BIS IS 14496 (Part 2)**, Caine
(1980), Bruun (1962), Otsu (1979), Lee (1980), Horn (1981), O'Callaghan & Mark
(1984), Beven & Kirkby (1979), Wang & Liu (2006), Garbrecht & Martz (1997),
Rennó (2008) / Nobre (2011), Leopold & Maddock (1953), Chow (1959), Cohen
(2018), USDA-NRCS Curve Number, NDMA guidelines, IPHS, Sphere.

Physics first, deliberately. A relocation order moves people out of their homes
and has to be defensible in an inquiry; a black box is not defensible. Machine learning belongs where it
earns its place — refining the water/land threshold, learning cloud-to-flood
patterns — not in the decision path.
