# Judge Q&A — how every variable is actually calculated

One entry per number a judge can point at on screen and ask "how do you know
that." Each gives the exact formula or rule, in the code, with its citation.
Nothing here is a black box — every figure can be traced to a published
method and a line of code.

---

## Soil wetness / soil saturation

**What's shown:** a 0–100% saturation reading, e.g. "soil at 100%, saturated
— further rain will run off, not soak in."

**How:** Open-Meteo's forecast model publishes volumetric soil water content
for the top 0–7 cm layer (m³ of water per m³ of soil), the same variable a
weather model uses internally to partition rain into runoff vs. infiltration.
Volumetric water content around **0.45 is saturation** for most soil types,
so the raw reading is scaled against that ceiling and clipped to [0, 1]:

```
saturation = clip(soil_moisture_m3_per_m3 / 0.45, 0, 1)
```

`backend/app/providers/openmeteo.py:220`. This is a live, observed/forecast
value — not modelled — for both the live board and any live-rainfall district
assessment.

**Antecedent index** (a separate but related figure, "148.7 mm antecedent"):
a decay-weighted sum of the past week's rainfall, each day weighted `0.87^d`
back from today — an exponential memory of recent wetness, the same shape
used in the SCS Antecedent Moisture Condition (AMC) adjustment below.
`openmeteo.py:229`.

---

## Rainfall bands (IMD warning categories)

**What's shown:** "IMD heavy / very heavy / extremely heavy" against a
24-hour rainfall figure.

**How:** the exact numeric bands India Meteorological Department issues
colour warnings on — not invented thresholds:

| Depth (24h) | Band |
|---|---|
| < 64.5 mm | no warning |
| ≥ 64.5 mm | heavy (yellow) |
| ≥ 115.6 mm | very heavy (orange) |
| ≥ 204.5 mm | extremely heavy (red) |

`backend/app/core/national.py`, `IMD_RAINFALL`. The 24h figure itself is
`max(observed past 24h, forecast next 24h)` from Open-Meteo — whichever
window has already produced the worse number.

---

## Cloud column reading (the live board's core signal)

**What's shown:** "deep convection," "building," "quiet," "mature system" —
plus percentages per level and an anvil flag.

**How, no ML:** three cloud-cover percentages (low 0–3 km, mid 3–8 km, high
above 8 km) are each tested against **55% = "filled"**. A level is anvil
signature when high cloud runs **≥25 percentage points** ahead of low cloud
*and* high itself is filled — cirrus blowing off the top of a storm that has
already topped out. State is then a simple rule table over how many levels
are filled and CAPE:

```
filled = count(level >= 55% for level in [low, mid, high])
anvil  = (high - low) >= 25  AND  high >= 55%

if filled == 0 or CAPE < 1000 J/kg:        state = "quiet"
elif filled >= 3 and CAPE >= 2500 J/kg:    state = "mature system" if anvil else "deep convection"
else:                                       state = "building"
```

`backend/app/core/cloudwatch.py:203-229`. When replaying a historical day
(CAPE isn't in the ERA5 archive), the CAPE condition is dropped and the state
comes from structure alone — the code explicitly avoids treating a missing
CAPE as zero, which would misread a fully-filled sky as calm.

**Anomaly** ("+40 pts vs 57% normal here"): each district's cloud coverage
this week is compared against its **own 5-year mean for that same calendar
week**, computed once from the Open-Meteo ERA5 archive and baked. Reported in
**percentage points**, not a ratio, because cloud cover is bounded at 100 and
a ratio would explode near zero.

---

## Landslide hazard — BIS IS 14496 (Part 2): 1998, LHEF

**What's shown:** TEHD (Total Estimated Hazard) out of 10, a susceptibility
percentage, and a zone label.

**How:** the six-factor LHEF rating scheme the Geological Survey of India
itself uses, not an invented overlay. Each factor is scored 0–2 and summed
against a maximum of 10 (`tehd_max`). Two of the six factors — lithology and
structure — need a geological survey map that isn't publicly available for
this project (GSI Bhukosh timed out; substitutes were tried and rejected, see
`DATASETS.md`), so they're held at a **neutral rating** and the achievable
maximum is reported as 8/10, not 10/10. This keeps the absolute BIS zone
**conservative** rather than silently inflating a district's rating.

`susceptibility` — the field actually used to rank ground within a district
— is TEHD normalised against the achievable maximum, so it isn't penalised
by the two missing factors.

**Slope** is measured at the DEM's **native 30 m resolution** (90th
percentile within each analysis cell), not the coarser grid-scale slope,
because slope is scale-dependent: a 340 m cell average erases a real
escarpment. `terrain.hillslope` in `backend/app/core/terrain.py`.

**Initiation** (where a slide actually starts) is deliberately the **upper
0.2–3.0% tail** of eligible cells (TEHD ≥ 6, slope ≥ 20°), scaled by how far
the triggering rainfall exceeds threshold — because susceptibility is not the
same as failure, and marking every steep slope as a source area produces a
map that means nothing.

**Trigger** — Caine (1980) rainfall intensity–duration threshold, adjusted
for wet antecedent conditions (a soaked catchment fails at lower intensity):

```
exceedance_ratio = event_intensity_mm_hr / threshold_mm_hr(antecedent-adjusted)
```

Reported per event: intensity, adjusted threshold, exceedance ratio, and the
exact hour it crossed. `backend/app/core/landslide.py`.

**Runout** — Corominas (1996) angle of reach, converting an initiation point
into the debris path downhill that actually reaches settlements.

---

## Cloudburst hazard

**What's shown:** susceptibility %, a trigger check against IMD's definition.

**Definition:** IMD's own operational threshold — **≥100 mm of rain in one
hour** over a small area. Not derived; quoted directly.

**Method:** `susceptibility = orographic_potential × catchment_flashiness`.
Orographic potential is high where slope lifts moisture-laden air fast;
flashiness is high in a small, steep, convergent catchment that concentrates
runoff rather than spreading it. "A cloudburst over a broad flat basin is a
wet afternoon; over a small steep convergent catchment it is a debris flow."
`backend/app/core/cloudburst.py`.

Cloudbursts are modelled as a **distinct event type** from the monsoon
design storm — reading the design storm's peak hour gives 8–14 mm/hr, which
never crosses 100 and would report zero cloudburst risk everywhere. Design
intensities: 60 / 100 / 150 mm/hr for moderate / severe / extreme.

---

## Flood depth and river stage

**Chain:** SCS-CN runoff → NRCS unit hydrograph lag → D8 flow accumulation →
Leopold & Maddock (1953) channel geometry → Manning compound-channel stage,
solved by **bisection** (conveyance is monotonic in depth, so bisection
converges where fixed-point iteration oscillated in testing) → HAND flooding
order → FwDET depth (Cohen 2018).

**SCS-CN runoff** (`backend/app/core/hydrology.py:88`):
```
S  = 25400 / CN − 254        potential maximum retention (mm)
Ia = 0.2 × S                 initial abstraction
Q  = (P − Ia)² / (P − Ia + S)   for P > Ia, else 0
```
Curve Number itself (30–98) is highest on built-up land (~90, concrete
infiltrates nothing) and lowest on well-drained forest; the spatial pattern
follows the terrain because settlements themselves sit on the same flat,
dry, well-drained ground the curve-number model rewards.

**HAND** — Height Above Nearest Drainage (Rennó 2008; Nobre 2011): the
vertical distance from every cell to the stream cell it drains into, walked
once in ascending-elevation order. A cell with HAND = 2 m floods when the
river rises 2 m, wherever it sits on the map — the single strongest terrain
predictor of riverine inundation. `backend/app/core/terrain.py:639`.

---

## Topographic Wetness Index (waterlogging)

**Formula** — Beven & Kirkby (1979):
```
TWI = ln( upslope_contributing_area / tan(local_slope) )
```
High TWI marks a large catchment draining into flat ground — the terrain
signature of a waterlogging hotspot, independent of river flooding.
`backend/app/core/terrain.py:674`.

---

## Population vulnerability index

**What's shown:** an index 0–1, with literacy/SC-ST/rural components.

**Source:** Census of India 2011, state-resolution (district-resolution
indicators aren't published at this granularity).

**Formula:**
```
index = 0.35 × illiteracy + 0.30 × SC/ST share + 0.35 × rural share
```
Weights are stated in the output so the composite can be checked, not just
trusted. **0.5 ≈ the national average**; higher means a population with
comparatively fewer resources to recover on its own. This is a relative
planning input, not a claim about any individual or community.

---

## Relocation site suitability (where people are moved to)

Every candidate cell passes a **hard eligibility mask first** — not inside a
red zone, slope within the buildable limit, not stream or sea, inside the
district. An ineligible cell scores zero regardless of everything else.

Eligible cells are then scored on **six weighted components**:

| Component | Weight | Why |
|---|---|---|
| Safety margin from red zone | 0.26 | hazard maps have error bars; distance absorbs them |
| Road access | 0.22 | a site nobody can reach is not a site |
| Water access | 0.16 | **non-monotonic** — too far is a supply problem, too close is a flood problem; optimum ~4 cells back from the channel |
| Buildability (slope) | 0.14 | |
| Land headroom | 0.12 | existing occupation nets off capacity |
| Farmland conflict | 0.10 | penalised, not forbidden — taking cropland trades one livelihood for another |

Capacity uses **Sphere Handbook 45 m²/person** at a rural/urban-interpolated
sustainable density. `backend/app/core/relocation.py`.

---

## Red Zone priority ranking (which habitation moves first)

```
priority = 0.34 × exposure + 0.26 × frequency + 0.16 × unsuitability
         + 0.08 × fraction-in-red + 0.16 × vulnerability
```
`exposure` = population in red zone ÷ a population cap (so a very large
district doesn't automatically dominate the list). `frequency` is a log
scale on return period — a hazard that recurs every 5 years scores far
higher than one recurring every 90. Vulnerability carries real weight (0.16,
not a token share) because the problem statement names population
vulnerability alongside hazard intensity, not beneath it.
`backend/app/core/redzone.py`.

**Hazards combine by maximum, not by sum** — a place that floods 3 m deep
every five years is unsuitable regardless of its landslide rating; a small
multi-hazard premium is added on top because two independent hazards do mean
more disrupted days per decade, but the dominant hazard is what's named and
what governs the relocation horizon.

---

## Mitigation strategy (protect vs. relocate)

```
if hazard_return_period < protection_design_life (25 years):
    strategy = "relocate"   # a protection work would be overtopped
                            # several times before it was due for replacement
else:
    strategy = "protect"
```
`backend/app/core/mitigation.py:strategy_for()`. The comparison — and the
resulting sentence — is returned in the API response, so the conclusion can
be checked, not just trusted. Each individual measure additionally carries
its own accountable authority and the statute it rests on (DM Act 2005
ss.30/31/38, RFCTLARR 2013, FCA 1980, CRZ 2019, MBBL 2016, PMAY-G, SDMF).

---

## Population, built-up land, cropland (the exposure layer)

**Real, for the 22 modelled districts:**
- Population — **GHS-POP R2023A** (JRC), observed counts at 100 m
- Built-up surface — **GHS-BUILT-S R2023A** (JRC), satellite-derived,
  1990–2025 epochs (drives the encroachment/deforestation reading too)
- Cropland / tree cover — **ESA WorldCover** 10 m v200 (2021)
- Roads / hospitals / schools — **OpenStreetMap**, via Overpass

**Falls back to modelled** (dasymetric allocation by terrain-suitability,
Census 2011 total preserved) for any of the 713 districts outside the 22
baked — and the API says which mode produced any given figure.

---

## Why there is no machine learning in the decision path

Every rule above is a published threshold or formula a judge — or a District
Magistrate defending a decision afterward — can check directly. "The model
said so" is not an answer that survives an inquiry; "IMD's own ≥100 mm/hr
cloudburst definition, crossed at hour 75 with a 4.95× exceedance ratio" is.

---

## The one honest caveat to lead with

Two BIS LHEF factors (lithology, structure) are held neutral pending a
geological survey map. River stage is modelled, never gauged — there is no
public real-time API for Central Water Commission data (three routes to one
are documented in `backend/app/providers/cwc.py`). Both are stated in the
API output itself, not hidden.
