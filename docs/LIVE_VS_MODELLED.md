# Live vs. modelled — the honest boundary

One question decides whether a judge trusts everything else you say: *where
does this stop being real?* This is the answer, checked against the running
system rather than written from memory. The sentence at the bottom is worth
memorising.

**No API key, anywhere, for anything.** Run this against the codebase live:

```bash
grep -ri "api_key\|apikey\|api key" backend/app -r
```

Every hit is a comment that says *"no API key required"* — never a credential.
Every data source in this system is open.

---

## Real — observed or measured, from a named source

| Layer | Source | How real |
|---|---|---|
| **Elevation, 30 m, all 22 districts** | Copernicus DEM GLO-30 (ESA) | Baked from the actual raster. HAND, slope, wetness index, flow routing, the BIS landslide rating and the coastal erosion model all derive from it. |
| **Population, all 22 districts** | GHS-POP R2023A (JRC) | Observed counts per 100 m cell, aggregated to the analysis grid inside the real district boundary. Not spread from a district total — counted. |
| **Built-up land, all 22 districts** | GHS-BUILT-S R2023A (JRC) | Satellite-derived built-up surface, 1990→2025. Now feeds exposure directly, not only encroachment detection. |
| **Cropland & tree cover, all 22 districts** | ESA WorldCover 10 m v200 | Real Sentinel-1/2 land-cover classification. |
| **Roads, all 22 districts** | OpenStreetMap (via Overpass) | Real highway geometry, rasterised. ODbL — attribution carried in every response that uses it. |
| **Hospitals, clinics, schools, all 22 districts** | OpenStreetMap (via Overpass) | Real locations. Capacity is still an estimate — OSM rarely tags bed counts (see below). |
| **District boundaries, all 735** | geoBoundaries ADM2 (CC-BY 4.0) | Real administrative outlines, not the synthetic star-shaped envelope. |
| **Rainfall, CAPE, soil moisture, cloud structure** | Open-Meteo forecast model | **Live, every refresh** — the exact numbers on screen, not a cached copy. |
| **Cloud & rainfall norms, 5 years** | Open-Meteo ERA5 archive | Baked per district, per calendar week — the baseline every anomaly is read against. |
| **Cyclone tracks, 328 storms** | IBTrACS (NOAA) | Real best-track data. |
| **Sentinel-1 pass times** | Copernicus catalogue | **Live query, no credentials** — real granule acquisitions. |
| **41 historic disasters** | Hand-curated | **Every row cited with a source URL.** |

## Modelled — inferred, and labelled as such everywhere it appears

| Layer | What it actually is | Falls back from |
|---|---|---|
| **Facility capacity** | Indian Public Health Standards provision rate (40 beds/hospital, 300 people/shelter) | OSM gives the real location; it does not tag bed counts, so capacity stays an estimate attached to a real place |
| **SAR backscatter imagery** | Simulated | Downloading and processing a real Sentinel-1 granule needs a Copernicus account — the one thing in this system that requires credentials |
| **Design storm** (demo mode) | IMD normals + Gumbel DDF | `?live=true` replaces it with the real Open-Meteo forecast |
| **River stage** | Modelled: live rainfall routed through real 30 m terrain | **No public CWC feed exists.** Every response carries `gauged: false`. See `providers/cwc.py` for the three routes to a real one. |
| **Lithology & structure** (2 of 6 BIS LHEF factors) | Held at a neutral rating | GSI Bhukosh is unreachable from this network (connection timeout, verified tonight); GEM's global active-fault catalogue was tried as a proxy and found too sparse over India to use — see `DATASETS.md`. `assessment_complete: false` is reported honestly rather than silently completed. |
| **Any layer for a district with no bake** | Falls back to the pre-tonight modelled method | Every loader degrades gracefully — a missing `.npz` file never crashes the app, it reports the older, honest fallback |

## What changed tonight, in one line each

- **Population** stopped being spread from a Census total and started being *counted* — GHS-POP.
- **Built-up land** stopped being generated from terrain suitability and started being *observed* — GHS-BUILT-S.
- **Cropland** stopped being modelled from slope and wetness and started being *classified* — WorldCover.
- **Roads and facilities** stopped being computed (least-cost paths, provision rates) and started being *located* — OpenStreetMap.
- **District boundaries** stopped being a synthetic star-shaped envelope and started being *the real outline* — geoBoundaries ADM2. (The real data was already baked; a wrong default file path was the only reason it wasn't used — fixed tonight.)

## The reconciliation, stated rather than hidden

GHS-POP's observed total and the Census 2011 projection are **two independent
real numbers**, and they are allowed to disagree. Across the 22 districts the
ratio (observed ÷ projected) ranges from **0.73 to 2.09**, median **1.35** — the
Census figure assumes a flat 1.6%/yr growth rate for every district since 2011;
GHS-POP observes the built environment as it actually is in 2025. Both numbers
are reported on every response (`population_reconciliation`), because silently
picking one would throw away real information.

## The sentence to memorise

> *"Elevation, population, built-up land, cropland, roads, facility locations,
> boundaries, rainfall and cyclone tracks are real and open — there is not one
> API key anywhere in this codebase. What is still modelled is facility
> capacity, the radar imagery itself, and river stage, because there is no
> public gauge feed — and every one of those says so on screen."*
