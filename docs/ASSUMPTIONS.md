# Assumptions and limitations

Every simplification in DRISHTI, in one place, written before anyone had to find
them.

A system that claims no weaknesses has not been tested. The purpose of this
document is that a reviewer's hardest question is one we have already answered
in writing — and that anyone using the output knows exactly what it is and is
not.

---

## 1. The single most important one

**In demo mode the input rasters are modelled, not observed.**

The elevation model, the radar scene, and the rainfall field are all synthesised.
The **algorithms** operating on them are the real, published methods listed in
[METHODOLOGY.md](METHODOLOGY.md) and served at `/api/methods`, and they are
identical in demo and live mode. But the numbers on screen in demo mode describe
a plausible district, not a real one.

Every API response carries a `provenance` block saying so, and the interface
shows a permanent badge. **Do not describe demo output as real satellite
observation.**

One genuine exception, and it is worth being precise about: the **Sentinel-1
catalogue query is live**. The granule identifiers and acquisition times shown
on the district screen are real passes returned by the Copernicus catalogue. We
list them; we do not download or process them in demo mode. Say it that way.

---

## 2. Data

| Assumption | Why | Consequence |
|---|---|---|
| District boundaries are an *approximate envelope* — a smooth polygon with the correct centroid and the correct area | We do not redistribute Survey of India boundary data | Areas and population totals are district-scale and correct in aggregate; the outline is not a legal boundary. Drop a real GeoJSON into `data/boundaries/` and it is used instead |
| Population is Census 2011 projected to 2025 at 1.6 %/yr | No district-level annual census exists | A planning estimate, not a count. Labelled as an estimate everywhere it appears |
| Population is distributed dasymetrically by modelled built-up intensity | WorldPop is not bundled | The district total is exact; the *within-district* distribution is modelled |
| Rainfall depth-duration-frequency is derived from the IMD annual normal via Gumbel factors | The full IMD DDF atlas is not public per station | Correct order of magnitude and correct ranking between districts; not a design value for engineering |
| Roads are least-cost paths between settlements | OpenStreetMap extracts are not bundled | Topologically sensible network for cut-off analysis; not the actual road alignment |
| Facility counts follow IPHS provision rates | Real facility registers are not bundled | Plausible capacity for a district of that size; not the actual list of schools |

---

## 3. Terrain and hydrology

- **Depression filling removes real closed basins.** Priority-flood assumes every
  depression is an artefact. On karst or endorheic terrain some are not.
- **Flat resolution invents a drainage pattern.** Ground that is genuinely flat
  has no natural flow direction; Garbrecht & Martz imposes a plausible one. The
  pattern is defensible, not observed.
- **D8 routes all flow in one direction.** It cannot represent divergent flow on
  an alluvial fan or a braided reach — which is exactly what the Brahmaputra
  does. A multiple-flow-direction scheme would be more faithful and slower.
- **HAND assumes a level water surface** normal to the channel, and knows
  nothing about embankments finer than the DEM. In embanked reaches of the Kosi
  and the Bagmati this is a significant simplification, and it is the single
  largest source of error in the flood extent.
- **The hydrology is lumped and quasi-steady.** No backwater, no momentum, no
  unsteady wave routing. A proper hydrodynamic solve (HEC-RAS, LISFLOOD-FP) is
  the right tool for a design study and the wrong one for a dashboard that must
  answer in under two seconds.
- **Curve numbers are inferred from modelled land cover**, not measured, and are
  the largest free parameter in the runoff model.
- **Channel geometry uses regional hydraulic-geometry averages.** A specific
  reach can differ several-fold.

---

## 4. Flood model

- **Volume is not conserved.** A cell floods when the stage at *its own* outlet
  exceeds its HAND. This can wet a hollow that has no physical path for water to
  reach it. Mitigation, once embankment data is digitised, is a barrier mask.
- **Surge and riverine flooding are combined by taking the deeper**, not by
  adding them. Adding would double-count the same water; the true interaction is
  a backwater effect that neither approach captures.
- **The reporting floor is 0.15 m.** Below that, water is treated as wet ground
  rather than flood. Without a floor, most of a delta reports as "flooded" at two
  centimetres and the number stops meaning anything.
- **Events are 96 hours.** Floods that persist for weeks — which is normal in
  Assam — are truncated at the modelling horizon.

---

## 5. Radar detection

- **Dry sand, smooth tarmac and radar shadow are radiometrically
  indistinguishable from water.** No threshold separates them. They are removed
  with the permanent-water, slope and HAND masks, which is a terrain argument,
  not a radiometric one.
- **Flooded vegetation is systematically under-detected.** Crop stems produce a
  double bounce that keeps backscatter high, so water standing in a paddy field
  may not darken at all. Measured recall on our own generated scenes is about
  **0.67** for this reason, against precision of about **0.92**. This is a
  property of C-band VV, not of the implementation, and it is why L-band NISAR
  matters for Indian agriculture.
- **Otsu separability has a floor of 0.637.** A single-mode histogram scores that
  by construction, so separability alone cannot detect the absence of water. A
  physical threshold ceiling of −13 dB is what actually rejects a dry scene.
- **Dense urban areas are hard.** Layover and corner reflection dominate; flood
  detection between tall buildings is largely unsolved in C-band.

---

## 5b. Red zones, landslide and erosion

- **Two of six BIS LHEF factors are unmapped.** Lithology and structure require a
  Geological Survey of India map and cannot be inferred from elevation. They are
  held at a neutral rating, `assessment_complete` is reported as `false`, and the
  absolute BIS zone boundaries are therefore **conservative** — a district cannot
  reach "very high" on terrain factors alone. Rank within a district using
  `susceptibility`, which is normalised against the achievable maximum.
- **Recurrence is quantised to three events.** A cell's return period can only be
  5, 25, 100 years or never, because those are the events modelled. Finer
  resolution needs more events, at linear cost.
- **The Caine threshold is a global relation** and conservative for the Indian
  monsoon. Tuning it without a local landslide inventory would be fitting to
  nothing.
- **Landslide runout uses a fixed 22° angle of reach**, appropriate for small
  shallow slides. Large rock avalanches travel much further.
- **Erosion retreat over decades is sub-pixel** at 160 m resolution. The retreat
  *rate* is the meaningful output; the area figure is flagged as
  resolution-limited in the response itself.
- **Hazards combine by dominance, not by sum.** Two hazards cannot make a place
  more than uninhabitable. A small multi-hazard premium is added and published.
- **`MATERIAL_EXPOSURE = 0.25`** — a settlement's horizon is governed by the
  return period at which a quarter of its people are exposed. That threshold is a
  judgement call, published rather than buried, and it is what stops one affected
  field on a village boundary from classifying the whole village.

## 5c. Relocation

- **Sustainable density is assumed**, at 2,500/km² rural to 8,000/km² urban.
  Sphere's planned-settlement minimum of 45 m²/person implies ~22,000/km², which
  is camp density, not somewhere people live permanently.
- **Site capacity is net of existing population**, so a district already at its
  sustainable density reports little headroom — which for parts of north Bihar is
  the correct and important answer.
- **Distance is a cost, not a constraint.** Displacement beyond ~8 km is flagged
  because resettlement commonly fails past it; beyond 25 km no allocation is
  made. Both figures are stated in the response.
- **Land acquisition, tenure, consent and compensation are out of scope.** The
  system says where land is physically suitable and has room. Whether it can be
  acquired is a legal and political question it does not model.

## 6. Impact and response

- **Zones are settlement catchments** (nearest-settlement Voronoi), not
  administrative blocks. They do not correspond to a revenue boundary.
- **The priority score is a weighted overlay with fixed published weights.** It
  is a defensible ordering, not an optimum. Every component is returned
  alongside the total so the ranking can be argued with.
- **Displacement is assumed to be 35 % of the affected population.** Many people
  stay with relatives or on raised embankments. Assuming everyone needs a camp
  bed over-orders relief by several times.
- **Cut-off detection uses a 0.30 m depth threshold on the modelled road
  network.** Real trafficability depends on vehicle type, road construction and
  current velocity.
- **SOP rules encode national doctrine.** A district's own disaster management
  plan may differ and takes precedence over anything this system says.

---

## 7. What has and has not been validated

| Claim | Status |
|---|---|
| The published algorithms are correctly implemented | ✅ **Tested** against analytical cases with hand-computable answers (`tests/test_algorithms.py`) |
| The system is internally consistent | ✅ **Tested** — 308 passing tests (6 skipped) covering mass balance, monotonicity, conservation, determinism and the two-tier coverage seam |
| No relocation site lies inside a red zone | ✅ **Tested** — the safety invariant, asserted for every district |
| Hazard extent grows monotonically with rarity | ✅ **Tested** across the severity ladder |
| The radar chain recovers a flood from backscatter | ✅ **Re-measured tonight on real GHSL/WorldCover land cover, all 22 districts** (was measured on modelled built-up/cropland before). Lowland and coastal (n=16): precision mean **0.83** (min 0.73), recall **0.47**, F1 **0.58**. Steep mountain (n=6): precision **0.84**, recall **0.16**, F1 **0.23**. Recall on the plains dropped materially against the modelled-data measurement (0.75 → 0.47) — real land cover is patchier than the smooth surface it replaced, and that patchiness genuinely confuses the Otsu threshold more than expected. Precision held. This is reported as found, not investigated to a root cause tonight, and not tuned away |
| **Radar works as well in the mountains as on the plains** | ❌ **Measured, and it does not.** Recall collapses to 0.18 in steep districts. C-band VV is a poor flood sensor there: layover and shadow corrupt the geometry, and what flooding occurs stays inside a channel corridor that is already permanent water, so there is little change to detect. This is the sensor's limit, not the code's, and the mountain figure is reported rather than averaged away |
| **The cloud read is auditable rather than fitted** | ✅ **Tested** — 18 tests fix the inputs by hand and assert the state the published rule requires. No machine learning in this path at all |
| **A missing CAPE record cannot read as a calm sky** | ✅ **Tested** — the ERA5 archive serves CAPE as NaN; treated as zero it classified a sky filled to 100% at every level as *quiet*. `test_missing_cape_does_not_read_as_a_calm_sky` |
| **The live board's river figure is a gauge reading** | ❌ **No, and it never claims to be.** There is no public CWC feed. Stage is modelled from live rainfall through real terrain and every response carries `gauged: false` |
| **Red zones rank the places disasters actually happened above their surroundings** | ✅ **Measured** against the observed record — mean susceptibility percentile **66.6** against a chance baseline of **50** (per-event 38.3, 58.4, 74.3, 95.3; Spearman ρ = 0.462, n = 4). Positive but on four events, which is too few to call the model validated |
| Output is reproducible | ✅ **Tested** — identical results for identical inputs |
| Performance | ✅ **Measured** — median 1.5 s per district, no GPU |
| Sentinel-1 catalogue queries return real granules | ✅ **Live** — `providers/copernicus.py` queries the Copernicus OData catalogue with no credentials. The pass ages shown in the UI are real acquisitions |
| **Flood extent matches a real historic event** | ❌ **Not validated.** Cannot be, on synthetic elevation. `scripts/validate_hindcast.py` implements the comparison and states plainly what data it needs |

---

## 8. Out of scope

Drought, heatwave, earthquake and cyclone wind are **not implemented**. Five
hazards are: flood, landslide, waterlogging, coastal erosion and cloudburst.

**The live board covers only the 22 modelled districts.** A river response needs
a DEM, and a cloud anomaly needs a baked local baseline; claiming either for a
district whose terrain has never been loaded would be exactly the confident
nonsense the rest of this system exists to avoid. The other 713 are served by the
screening tier, which says plainly that it is weather only.

**Coverage is two-tier, and the line between them is load-bearing.** All 735
districts are addressed. Twenty-two are *modelled* — full physics, exposure and
relocation caseload. The remaining 713 are *screening* only: live weather
aggregated from the 27 km national grid, with no terrain analysis behind the
number at all. Screening says where to look. It is not a hazard assessment, and
the interface labels every record with its tier and a plain-language statement of
what stands behind it.

When the live weather connection drops, **screening districts report no score
rather than zero**. Every weather field would otherwise sit at its default and
all 713 would score 0.0, which reads as a confident national all-clear — the most
harmful output this system could produce. Modelled districts are unaffected,
because their physics is baked.

Land acquisition law, tenure, consent, compensation and the political economy of
resettlement are outside the system entirely. It answers where land is
physically suitable and has room — not whether a relocation can actually be
carried out.
