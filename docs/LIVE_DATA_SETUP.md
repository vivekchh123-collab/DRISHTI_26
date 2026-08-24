# Live data — what works now, and what needs you

Written after actually trying every source below. Where something does not work,
this says what was tried and what happened, rather than listing it as
"integrated".

---

## Working right now, with no account and no key

Nothing to do. These are live the moment the app starts.

| Source | What it gives | Where it shows |
|---|---|---|
| **Open-Meteo** | rainfall (observed + forecast), **CAPE**, **soil moisture** | National screen · `?live=true` assessments |
| **Copernicus catalogue** | real Sentinel-1 granule times over any district | "Last Sentinel-1 pass" on the district header |
| NASA POWER | daily precipitation archive | climatology fallback |

**Open-Meteo is the one carrying the live system.** It needs no registration at
all, and it supplies the variable that had been the largest approximation in the
whole chain: real soil moisture, in place of a decay-weighted rainfall proxy.

### The rate limit is the thing to understand

Open-Meteo's free tier bills **per coordinate, not per request**. A national
sweep of 532 squares costs **532 calls** even though it travels as three HTTP
requests.

| Activity | Cost |
|---|---|
| One national sweep | 532 calls |
| National sweep every 30 min | ~1,064 calls/hour |
| One live district assessment | 16 calls (4×4 sample grid) |
| Free tier allowance | a few thousand per hour |

Comfortable for the intended half-hourly refresh. **Easy to exhaust while
developing** — which happened during the build of this system, and is why the
app now detects a 429, stands down for ten minutes, serves cached results, and
says plainly in the UI that it has done so.

If you need more headroom, Open-Meteo sells a commercial key that raises the
limit; set it in `providers/openmeteo.py`.

---

## Needs you to register — 10 minutes each

I cannot create these accounts. They require your identity, email and phone.

### 1. ISRO Bhoonidhi — **start this first, approval takes a day or two**

<https://bhoonidhi.nrsc.gov.in>

Gives CartoDEM (a real Indian DEM, replacing our synthetic terrain), Resourcesat
AWiFS, and EOS-04 SAR. **This is the highest-value single item on the list** —
a real DEM turns every hazard output from "modelled terrain" into "real
terrain", and it is the input the whole system is built around.

Once downloaded:

```bash
# any GeoTIFF covering the district
pip install rasterio          # optional dependency, only needed for real rasters
```

Drop the file in and point `terrain.load_dem_geotiff` at it. No code change.

### 2. Copernicus Data Space — only needed to *download* granules

<https://dataspace.copernicus.eu>

Catalogue search already works without it. Credentials are needed only to pull
the actual SAR imagery.

```bash
set COPERNICUS_USER=you@example.org
set COPERNICUS_PASSWORD=...
```

### 3. NASA Earthdata

<https://urs.earthdata.nasa.gov>

For GPM IMERG rainfall and MODIS/VIIRS products, if you want a second rainfall
source alongside Open-Meteo.

### 4. Geological Survey of India — lithology

Two of the six BIS IS 14496 landslide factors (**lithology** and **structure**)
cannot be derived from elevation and are currently held at a neutral rating.
A GSI lithology raster promotes the assessment from partial to complete, and the
API stops reporting `assessment_complete: false`.

Load via `landslide.load_geology(path, grid)`.

---

## Tried, and does not work

### Central Water Commission — **no public API exists**

This is the one I most wanted and could not get.

| Endpoint | Result |
|---|---|
| `ffs.india-water.gov.in` | HTML only. A JavaScript application that fetches data after load; no documented JSON endpoint. |
| `indiawris.gov.in` | Connection timed out. |
| `arc.indiawris.gov.in` | Connection timed out. |

India-WRIS also requires registration for bulk access. **I did not build a
scraper against an undocumented endpoint** — it would break without warning and
calling it an integration would be dishonest.

The connector is written and waiting. Point it at any JSON feed matching its
contract and it goes live with no code change:

```bash
set DRISHTI_CWC_ENDPOINT=https://your-feed/gauges.json
```

Expected shape: a list of objects with `station`, `river`, `lat`, `lon`,
`level_m`, `warning_level_m`, `danger_level_m`, `observed_at`.

Three routes to a real feed, best first:

1. **A data-sharing agreement with CWC.** The correct route for anything
   operational, and obtainable by a state authority.
2. **India-WRIS registered access.**
3. **A state flood-control department feed.** Bihar, Assam and Kerala each run
   their own gauge telemetry, often with fewer restrictions than the national
   service.

### ISRO Bhuvan WMS — responds, then times out

`GetCapabilities` returns 248 layers. Actual layer queries timed out
repeatedly. The connector exists; nothing depends on it.

### Global Forest Watch — needs a key

The dataset listing is open; the query endpoint redirects and requires an API
key. Forest cover is modelled from terrain by default, and
`deforestation.load_forest_raster` accepts a real Hansen or Forest Survey of
India raster.

---

## What "live" actually means in this build

Be precise about this, because it is the question a reviewer will press on.

| Layer | Status |
|---|---|
| Rainfall (observed + forecast) | 🟢 **live** |
| Soil moisture / antecedent wetness | 🟢 **live** |
| Convective instability (CAPE) | 🟢 **live** |
| Sentinel-1 acquisition times | 🟢 **live** |
| Elevation | 🟠 modelled — until a CartoDEM tile is dropped in |
| Population | 🟠 Census 2011 projected |
| Boundaries | 🟠 approximate envelope |
| River stage | 🔴 no feed available |
| SAR imagery itself | 🟠 simulated |

So a live district assessment is **a real event on modelled terrain** — and
that is exactly what the provenance block says, word for word. It does not
claim to be a fully observed assessment, because it is not one.

---

## Quick check

```bash
curl http://127.0.0.1:8000/api/methods          # every provider's live status
curl http://127.0.0.1:8000/api/live/BR-DAR      # live weather over a district
curl "http://127.0.0.1:8000/api/districts/BR-DAR/assessment?live=true"
```

If a live call falls back, the response carries a `live_fallback` block naming
the reason and the retry time. It never silently serves a synthetic result
labelled live.
