# Data sources — the complete list

Every source below was **probed from this machine**. Where something failed, the
failure is recorded with what was tried, rather than the source being listed as
"integrated" and quietly not working.

Legend: 🟢 wired and live · 🔵 verified reachable, not yet wired · 🟡 needs a free
account · 🔴 needs money or a partnership · ⚫ tried, does not work

---

## The reframe that matters

For a **relocation planning** problem, live weather is close to decoration. A red
zone is a recurrence assessment across 5-, 25- and 100-year events; whether it
rained this morning does not change whether a village should be moved.

What legitimately *updates* a red zone is **change on the ground**:

| Change | Why it moves the answer | Source |
|---|---|---|
| Forest lost on a slope | Root cohesion decays; the slope is now more dangerous | GFW / WorldCover |
| **New building inside a red zone** | **Someone moved into the danger. This is the operational question.** | GHSL / WSF |
| Shoreline retreated | The land is gone, not flooded | Sentinel-2 series |
| A flood or landslide occurred | Recurrence estimate updates — the PS's "disaster history" clause | GLC / EMS |

Live rainfall stays useful for the *event* view. It is not what makes the red
zone live.

---

## 🟢 Wired and live now — no credentials at all

| Source | Endpoint | Gives | Cost |
|---|---|---|---|
| **Copernicus DEM GLO-30** | `copernicus-dem-30m.s3.amazonaws.com` | **Real 30 m elevation, global** | free, open |
| **Open-Meteo** | `api.open-meteo.com` | rainfall, soil moisture, CAPE, cloud | free |
| **Open-Meteo Archive** | `archive-api.open-meteo.com` | 5-year cloud and rainfall norms, **and historical replay** | free |
| **Copernicus catalogue** | `catalogue.dataspace.copernicus.eu/odata/v1` | real Sentinel-1 granule times | free |
| **NASA POWER** | `power.larc.nasa.gov/api` | daily precipitation archive | free |
| **geoBoundaries ADM2** | `geoboundaries.org/api/current/gbOpen/IND/ADM2` | **all 735 district boundaries** | free, CC-BY 4.0 |
| **GHSL GHS-BUILT-S R2023A** | `jeodpp.jrc.ec.europa.eu` | built-up surface 1975-2030, 100 m | free, open |
| **IBTrACS** | `ncei.noaa.gov/data/international-best-track-archive` | cyclone best tracks since 1842 | free |

### Copernicus DEM is the important one

**No account. No key. 30 m global elevation, Cloud-Optimized GeoTIFF with byte-range
support**, so a district is read as a window over HTTP rather than by pulling the
46 MB tile.

`scripts/fetch_real_dem.py` bakes each district onto its analysis grid and stores
it as ~85 kB of compressed numpy — **1.8 MB for all 22 districts**. The shipped
application therefore runs on real terrain with **no GDAL, no rasterio and no
network**.

Verified against published district elevation ranges — Wayanad came out
34–2039 m against a published 700–2100 m; Idukki 16–2550 m against 100–2695 m.
All 22 agree.

Two traps, both handled in the script:

- **A district grid usually spans several 1° tiles.** Reading only the centroid's
  tile leaves a third of the frame as nodata zeros, which then reads as
  sea-level ground and floods spectacularly.
- **Copernicus records sea surface as 0 m**, it does not omit ocean tiles. So
  elevation alone cannot separate ocean from land at 0–2 m — which is precisely
  the deltaic terrain that matters. Water is derived by connectivity instead:
  the sea reaches the frame edge, an inland hollow does not.

---

## 🔵 Verified reachable, not yet wired

Ranked by what they would actually change.

| Source | Endpoint | Gives | Why it matters here |
|---|---|---|---|
| **GHSL** (JRC) | `jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/` | built-up surface, **1975→2030 multi-temporal** | **"Who built inside a red zone since 2015."** The single most decision-relevant unwired dataset. |
| **ESA WorldCover** | `esa-worldcover.s3.amazonaws.com` | 10 m land cover, 2020/2021 | real forest and built-up instead of modelled |
| **WorldPop** | `worldpop.org/rest/data/pop/wpgp` | 100 m gridded population | replaces the Census-2011 dasymetric estimate |
| **NASA GIBS** | `gibs.earthdata.nasa.gov/wmts` | MODIS/VIIRS imagery tiles | visual context, cloud |

---

## 🟡 Free, needs an account — about 10 minutes each

| Source | For | Note |
|---|---|---|
| **ISRO Bhoonidhi** | CartoDEM, Resourcesat AWiFS, EOS-04 SAR | Indian data. Now a *refinement* rather than a blocker — Copernicus DEM already gives real terrain. Approval 1–2 days. |
| **GSI Bhukosh** | lithology, structure | Completes the BIS LHEF rating. Two of six factors are currently neutral and reported as such. |
| **NASA Earthdata** | GPM IMERG, MODIS NRT flood | second rainfall source |
| **Copernicus login** | *downloading* Sentinel-1 granules | catalogue search already works without it |
| **OpenTopography** | SRTM / NASADEM clips | returned **401** without a key. Not needed — Copernicus DEM is direct. |
| **Global Forest Watch** | tree-cover loss, RADD alerts | RADD is radar-based, so it sees through cloud, and is **weekly** |
| **EM-DAT** | historical disaster records | the "disaster history" clause |

---

## 🔴 Money or partnership

| | Reality |
|---|---|
| **Exotel** | Indian IVR, ~₹0.5–1/min. KYC + prepaid. Better than Twilio for India. |
| Twilio | Works; pricier; DLT registration needed for SMS |
| Google Maps Geocoding | Billing account. **Use LGD instead — free, official, better for Indian villages.** |
| Planet / ICEYE / Capella | Daily-revisit commercial imagery. Tasking in hours, which free satellites cannot do. |
| Short code (1077-style) | Telecom allocation |
| **NDMA Sachet** | ⚠️ Push requires being an authorised agency. We emit CAP XML — the standard it consumes — so it is a permission, not a build. |

---

## ⚫ Tried, does not work

Documented because "why didn't you use X" is a question worth having an answer to.

| Source | What happened |
|---|---|
| **CWC flood forecasting** | `ffs.india-water.gov.in` returns HTML only — a JavaScript app with no documented JSON endpoint. `indiawris.gov.in` and `arc.indiawris.gov.in` both **timed out**. No public API exists. |
| **ISRO Bhuvan WMS** | `GetCapabilities` succeeds and lists 248 layers; actual layer queries **time out** repeatedly. Connector exists; nothing depends on it. |
| NASA Global Landslide Catalog | 404 at the endpoint tried. Worth retrying — it holds ~11,000 located landslide events and would be real validation for the LHEF model. |
| Copernicus EMS Rapid Mapping | 404 at the endpoint tried. Worth retrying — it publishes **real observed flood extents**, which is exactly what a hindcast needs. |

**No scraper was written against an undocumented endpoint.** It would break
without warning, and calling it an integration would be dishonest. The CWC
connector is written and goes live against any feed matching its contract:

```bash
set DRISHTI_CWC_ENDPOINT=https://your-feed/gauges.json
```

---

## Where the honesty table stands now

| Layer | Before | Now |
|---|---|---|
| **Elevation** | 🟠 synthetic | 🟢 **Copernicus DEM GLO-30, 30 m** |
| Rainfall, soil moisture, CAPE | 🟢 live | 🟢 live |
| Sentinel-1 pass times | 🟢 live | 🟢 live |
| Land cover / built-up | 🟠 modelled | 🟢 **GHSL built-up surface, 22 districts baked** |
| Population | 🟠 Census 2011 projected | 🔵 available (WorldPop) |
| Boundaries | 🟠 approximate envelope | 🟢 **geoBoundaries ADM2, all 735 districts** |
| Cyclone history | 🔴 none | 🟢 **IBTrACS, 328 storms baked** |
| SAR imagery | 🟠 simulated | 🟡 Copernicus credentials |
| River stage | 🔴 no feed | 🔴 no public API |

Terrain moving from synthetic to real is the largest single change this project
has had: **HAND, slope, wetness index, flow routing, the BIS landslide rating and
the erosion model are all now derived from real 30 m elevation.**

---

## Cloud pattern, and what the archive will not give you

The live board reads cloud structure rather than forecasting rain. The variables
below all come from Open-Meteo with no credentials:

| Variable | Forecast API | ERA5 archive | Used for |
|---|---|---|---|
| `cloud_cover_low` / `mid` / `high` | ✅ | ✅ | Column depth, anvil signature |
| `cape` | ✅ | ❌ **empty column** | Instability, live only |
| `freezing_level_height` | ✅ | ✅ | Warm-cloud depth |
| `precipitation` | ✅ | ✅ | Rainfall, and the weekly norm |
| `soil_moisture_0_to_7cm` | ✅ | ✅ | Saturation |

**The CAPE gap is the one worth knowing about.** The archive returns it as a
column of NaN rather than as an error, so it fails silently. Treated as zero it
classifies a sky filled to 100% at every level as *quiet*. Replayed days are
therefore classified on cloud structure alone and say so; see
`test_missing_cape_does_not_read_as_a_calm_sky`.

Norms are baked per district by `scripts/fetch_climatology.py` — five years of
hourly cloud and rainfall reduced to a per-calendar-week mean, about 0.19 MB
total. Baked rather than fetched because a five-year normal does not change
between refreshes and Open-Meteo bills per coordinate.

---

## National coverage, and the line running through it

All 735 districts are addressed. They are **not** all known to the same depth,
and the difference is the most important thing on that screen.

| Tier | Districts | What is behind the number |
|---|---|---|
| **Modelled** | 22 | HAND inundation, D8 routing, BIS landslide rating, recurrence across 5-, 25- and 100-year events, exposure, relocation caseload |
| **Screening** | 713 | Live weather aggregated from the 27 km grid. **No terrain analysis at all.** |

Screening says *where to look*. It is not a hazard assessment, every record
carries its tier and a plain-language `basis` string, and the interface renders
the two differently on purpose.

**When the weather connection drops, screening districts report no score rather
than a zero.** Every weather field would otherwise sit at its default and all 713
would score 0.0 — which reads as a confident national all-clear. Absence of
information has to look like absence of information; the modelled districts are
unaffected, because their physics is baked.

Promotion is a data task, not a code change: bake a district's elevation with
`scripts/fetch_real_dem.py` and it moves tier on the next build.

---

## Rate limits worth knowing

**Open-Meteo bills per coordinate, not per request.** A 532-square national sweep
costs 532 calls even though it travels as three HTTP requests.

| | |
|---|---|
| National sweep | 532 calls |
| Sweep every 30 min | ~1,064/hour |
| One live district | 16 calls (4×4 grid) |
| Free allowance | a few thousand per hour |

Fine for the intended refresh, easy to exhaust while developing — which happened
during this build. The app now detects a 429, stands down for ten minutes, serves
cached results, and says so rather than silently serving a stale forecast.
