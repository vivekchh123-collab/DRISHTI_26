# Judge Q&A, part 2 — river modelling, slope, and the tech stack

Continues `JUDGE_QA.md`. Everything here is pulled from the running code and
the actual dependency manifests just now, not from memory.

---

## "How do you model the river?" — the full chain, step by step

There is **no gauged river data** behind any of this — there is no public,
documented, keyless real-time API for Central Water Commission gauge levels
(three routes to a real one, and what happened trying each, are written up
in `backend/app/providers/cwc.py`). So the river is built entirely from
terrain and rainfall, six steps, each a named published method:

**1. Where the channel is** — D8 flow routing (O'Callaghan & Mark, 1984).
Every cell points to whichever of its 8 neighbours is steepest downhill;
walking that graph gives flow accumulation (how many cells drain through
each point) and, thresholded, the stream network itself.

**2. How wide the channel is** — Leopold & Maddock (1953) downstream
hydraulic geometry:
```
width_m = clip(2.6 * catchment_km2 ^ 0.50, 3, 2500)
```
Width scales with the square root of catchment area — a river draining 100×
more land is roughly 10× wider. `backend/app/core/hydrology.py:183`.

**3. How deep it is at bankfull** — the depth limb of the same relation:
```
bankfull_m = clip(0.30 * catchment_km2 ^ 0.30, 0.5, 14)
```
Depth grows far more slowly than width with catchment size, which is the
real-world reason big rivers are broad rather than deep, not a modelling
artefact.

**4. How much water is arriving** — SCS-CN runoff (see part 1) routed
through a **unit hydrograph**: a single-peaked gamma curve (shape k=3, a
typical basin shape) that spreads instantaneous runoff over time, because
water does not arrive at the outlet the instant it falls — the catchment
delays and smears it. Time-to-peak comes from the SCS lag relation:
```
tp_hours = clip(1.6 * area_km2^0.30 / slope_pct^0.15, 0.6, 48)
```
A 5 km² headwater peaks near an hour after rain starts; a 2000 km² trunk
takes closer to a day — the right order of magnitude for Indian river
basins. `backend/app/core/hydrology.py:146-167`. The hydrograph is applied
as a lower-triangular convolution matrix so all cells route in one matrix
multiply rather than a per-cell Python loop.

**5. How deep the water sits, given that discharge** — Manning's equation,
inverted for depth:
```
Q = (1/n) · W · h^(5/3) · S^(1/2)     (Chow, 1959; n = 0.035, natural channel)
h = (Q·n / (W·√S))^0.6
```
This alone over-predicts once flow spills onto the floodplain — the true
hydraulics turn two-dimensional and a channel-only formula keeps sending
every extra cubic metre straight into depth.

**6. The floodplain fix, and why it's solved by bisection.** A first version
that let stage keep climbing on channel geometry alone produced a **15 m**
river in flat, floodplain-heavy Darbhanga — physically impossible. The
obvious fix — widening the cross-section as depth rises past bankfull — does
not converge by simple iteration: a floodplain that widens by hundreds of
metres per extra metre of depth is not a contraction map, and successive
substitution oscillated between "wildly overbank" and "comfortably in-bank"
rather than settling. **Conveyance is, however, strictly increasing in
depth**, so the compound stage is solved by **bisection** instead — always
convergent, and cheap enough to vectorise over the whole grid at once.
`backend/app/core/hydrology.py:249`, `stage_compound()`.

**Result:** every district assessment reports `river.gauged: false` and
`confidence: "modelled"` — the system never dresses this chain up as a
sensor reading. When `?live=true` is used, the *rainfall* driving all six
steps is real observed/forecast data; the terrain and channel geometry are
unchanged either way, because they are physical properties of the ground,
not weather.

---

## "How exactly is slope calculated?"

**Horn's method (1981)**, the same 3×3 finite-difference kernel used in
Esri's and QGIS's own slope tools — not a novel formula:

```
dz/dx = [(z3 + 2z6 + z9) − (z1 + 2z4 + z7)] / (8 · cell_size)
dz/dy = [(z7 + 2z8 + z9) − (z1 + 2z2 + z3)] / (8 · cell_size)
slope  = arctan( √(dz/dx² + dz/dy²) )
aspect = arctan2(dz/dy, −dz/dx)
```
where z1..z9 are the elevations of a cell and its eight neighbours, read
left-to-right, top-to-bottom. `backend/app/core/terrain.py:314`.

**Two different slopes are reported, deliberately:**
- **Grid-scale slope** (`net.slope`) — Horn's method run on the analysis
  grid itself (roughly 300 m cells for most districts). Answers "how tilted
  is this cell, on average."
- **Native-resolution slope** (`terrain.hillslope`, the 90th percentile of
  slope measured at the DEM's original **30 m** resolution within each
  analysis cell) — answers "is any part of this cell steep," which is the
  question a landslide actually asks. A 300 m cell average genuinely erases
  a real escarpment — this was found directly during Wayanad testing, where
  a cliff-edge cell read as nearly flat on the grid-scale figure while its
  native-resolution hillslope was steep enough to initiate a slide; the exact
  pair of numbers isn't being re-quoted here without re-running the check
  live. Landslide assessment uses the native figure for exactly this reason
  — it is not a cosmetic difference, it changes which cells the model flags.
  Every other slope-dependent
  calculation (curve number, buildability scoring) uses the grid-scale one,
  since those operate at the grid's own resolution anyway.

---

## Tech stack — read straight from the actual dependency files

**Backend — `requirements.txt`:**
```
fastapi>=0.110
uvicorn[standard]>=0.27
numpy>=1.24
```
Three hard dependencies. No GDAL, no rasterio, no GPU stack **at runtime** —
the explicit design target is a district server or a field laptop where a
heavyweight geospatial install isn't realistic. `rasterio` is listed but
commented out, because it's needed only at *bake time* (see below), and
every code path that touches it degrades cleanly when it's absent.

**Machine learning — scikit-learn, imported lazily.**
`backend/app/core/ml.py` wraps `RandomForestClassifier` /
`RandomForestRegressor` behind a function that imports sklearn only when
called, so the rest of the system runs without it installed. This is the
**national screening surrogate only** — it estimates a coarse hazard score
for the 713 districts without baked terrain, trained on the 22 districts
that do have full physics, validated with **district-held-out**
cross-validation (a district the model never trained on). It plays no role
in any of the district-level hazard, relocation, or mitigation calculations
covered in part 1 — those are 100% published formulas over measured data,
by design (see "why no ML in the decision path," part 1).

**Frontend — vanilla, no framework.** `index.html` loads exactly two
scripts: `vendor/leaflet.js` (the map library) and `js/app.js` as an ES
module, which imports the rest of the app's own JS files directly — no
React, no bundler, no build step. Leaflet is the only third-party
dependency in the entire frontend.

**Bake-time only** (`scripts/fetch_*.py`, never shipped, never imported at
runtime) — `rasterio` for reading GeoTIFFs: `fetch_real_dem.py` (Copernicus
DEM GLO-30), `fetch_ghsl.py` (built-up surface), `fetch_ghspop.py`
(population), `fetch_worldcover.py` (land cover), `fetch_faults.py` (GEM
active faults), `fetch_osm.py` (roads/facilities via Overpass, no rasterio
needed — GeoJSON). Each writes a small `.npz`/`.json` per district that the
running app reads with **numpy alone** — this is what keeps "no GDAL at
runtime" true even though the source imagery is real GeoTIFF/COG data.

**Packaging — PyInstaller + Electron, two independent stages**
(`scripts/build_desktop.py`): PyInstaller freezes the Python backend
(interpreter, numpy, FastAPI, every baked `.npz`/`.json`) into a
self-contained folder; Electron wraps that folder plus the static frontend
into a Windows installer (`desktop/package.json`, `electron-builder`,
NSIS target). The result is a normal Windows `.exe` installer — no Python
install, no `pip`, no internet required to run it.

**Why this combination:** every layer was chosen for the same reason —
runs on a district office's laptop with no admin rights, no GPU, and no
guaranteed internet, while the actual analysis (DEM processing, flow
routing, hydrology) still runs on real satellite-derived elevation rather
than a toy dataset. The heavy geospatial tooling (rasterio, GDAL) does real
work exactly once, at bake time, on a development machine — the field
deployment only ever touches numpy arrays.
