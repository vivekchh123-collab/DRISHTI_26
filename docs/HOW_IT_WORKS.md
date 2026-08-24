# How it works — one page per model

Every method below is a published, cited technique — never an invented
overlay. Each section is: **inputs → equations/rules → thresholds → sources →
known limits.** This is the page to read from when a judge asks "how do you
calculate that?"

---

## 1. Rain → river → flood

**Pipeline:** rainfall → SCS-CN runoff → NRCS unit hydrograph (catchment lag)
→ D8 flow accumulation → Leopold & Maddock channel geometry → Manning
compound-section stage → HAND flooding order → FwDET depth.

- **Runoff.** USDA-NRCS curve-number method (National Engineering Handbook,
  Part 630), with Antecedent Moisture Condition adjustment. Computed on the
  *cumulative* rainfall curve and differenced — this is what makes initial
  abstraction behave correctly: the first millimetres of a storm soak in,
  later ones do not.
- **Routing.** NRCS dimensionless unit hydrograph for catchment lag; D8 flow
  accumulation (O'Callaghan & Mark, 1984) for drainage direction.
- **Channel geometry.** Leopold & Maddock (1953) hydraulic geometry — channel
  width scales as a power of discharge.
- **Stage.** Manning's equation on a compound channel section, **solved by
  bisection.** Fixed-point iteration was tried first and oscillated between
  6.5 m and 0.5 m without converging; bisection converges because conveyance
  is monotonic in depth. Solved only on stream cells (the `nearest` stream
  lookup always points at one), which took the solve from 12 s to 0.67 s.
- **Inundation.** HAND — Height Above Nearest Drainage (Rennó et al. 2008;
  Nobre et al. 2011) — orders cells by flooding sequence from the channel
  outward. Depth by FwDET (Cohen et al. 2018).

**Known limit.** Not hindcast against a gauged historic event — there is no
public CWC feed to validate against. `scripts/validate_hindcast.py` states
exactly what two files it needs.

---

## 2. Landslides — BIS IS 14496 (Part 2): 1998 LHEF

The Landslide Hazard Evaluation Factor scheme is the **Indian national
standard** — the method GSI itself uses for macro-zonation, not an invented
weighted overlay. Six factors summed into a Total Estimated Hazard:

| Factor | Max rating | Source here |
|---|---|---|
| Slope morphometry | 2.0 | Real DEM slope |
| Relative relief | 1.0 | Real DEM local relief |
| Land use / land cover | 2.0 | Real GHSL built-up + WorldCover cropland/tree cover |
| Hydrogeology | 1.0 | Topographic wetness index |
| Lithology | 2.0 | **Neutral rating — GSI Bhukosh unreachable, see DATASETS.md** |
| Structure | 2.0 | **Neutral rating — GEM faults tried, found too sparse over India, see DATASETS.md** |

**The detail that matters: slope is scale-dependent.** A 340 m analysis cell
averaged away the Wayanad escarpment entirely — the ground genuinely failed
there, but the cell was too coarse to see the cliff. Slope for the landslide
factor is now measured at the DEM's **native 30 m resolution** (90th
percentile within each analysis cell), so the model degrades to the coarser
answer only where finer data doesn't exist, rather than always.

**Initiation zones over-corrected once, and the fix is worth explaining.**
The first version flagged ~30% of Rudraprayag's area as initiation-prone —
technically consistent with the rating, uselessly broad in practice.
Initiation now takes the **upper tail** — 0.2 to 3.0% of eligible cells,
scaled by how far the trigger exceeds the threshold, restricted to cells with
TEHD ≥ 6 and slope ≥ 20°.

**Triggering:** Caine (1980) rainfall intensity–duration threshold, with
antecedent wetness shifting the threshold down — the reason Kerala 2018 and
Wayanad 2024 both followed weeks of prior rain rather than a single storm.
**Runout:** Corominas (1996) angle of reach.

---

## 3. Cloudbursts

IMD's own operational definition: **≥100 mm of rain in one hour.**
Susceptibility = orographic potential × catchment flashiness. Design
intensities: 60 / 100 / 150 mm/hr for moderate / severe / extreme.

**Why it is a separate event type, not folded into the flood model:** reading
the monsoon design storm's own peak hour gave 8–14 mm/hr — well below the IMD
threshold — and produced 0 km² of cloudburst hazard everywhere. A cloudburst
is genuinely a different, shorter, more intense event, and modelling it as
one required treating it as one.

---

## 4. Cloud-pattern reading — the live signal, and why there is no ML in it

The vertical **structure** of the cloud column, not a rainfall forecast:

- **Levels filled** — low/mid/high cover each ≥55%, the threshold below which
  a level is not meaningfully overcast.
- **Anvil signature** — high cloud running ≥25 percentage points ahead of low
  cloud: cirrus outflow from a storm that has already topped out.
- **CAPE bands** — 1000 J/kg (moderate), 2500 J/kg (strong) instability.
- **Freezing level** — above 4500 m signals a deep warm-cloud layer, the
  classic precondition for the high rain rates behind Himalayan cloudbursts.
- **Anomaly** — in **percentage points** against that district's own 5-year
  mean for that calendar week, not a ratio (cloud cover is bounded at 100, so
  "twice normal" is meaningless once cover approaches saturation).

Four states in escalating order: quiet → building → deep convection → mature
system. Each is a stated rule over stated numbers — check `STATE_RULES` in
`core/cloudwatch.py` directly.

*"There is no machine learning in the decision path, deliberately. An
evacuation order has to survive an inquiry. Physics and thresholds are
auditable; a network activation is not."*

---

## 5. Relocation siting — how sites are actually picked

**A hard eligibility mask first.** Not inside a red zone, slope within the
buildable limit, not sea or stream, inside the district. **Ineligible cells
score zero and can never be allocated**, whatever else about them looks good.

Then six weighted components on every eligible cell:

| Component | Weight | Why |
|---|---|---|
| Safety margin from red zone | 0.26 | Hazard maps carry error bars; a buffer absorbs them |
| Road access | 0.22 | A site nobody can reach is not a site |
| Water access | 0.16 | **Non-monotonic** — too far is a supply problem, too close is a flood problem; the optimum sits a few cells back from the channel |
| Buildability (slope) | 0.14 | |
| Land headroom | 0.12 | |
| Farmland conflict | 0.10 | Penalised, not forbidden — taking cropland for housing trades one livelihood for another |

**Capacity:** Sphere Handbook shelter standard, 45 m²/person, at a density
interpolated between rural and urban norms by the district's urban fraction.

**Two different numbers, deliberately kept apart.** The site *shortlist* is a
dozen places a District Magistrate could realistically acquire and build on.
`district_headroom()` separately reports the *ceiling* — every eligible cell
in the district, filled to a sustainable density. The gap between them is
informative: where headroom is large but the shortlist is small, land exists
and the constraint is site assembly; where headroom itself is small, the
district is genuinely full and relocation becomes a state-level decision.

---

## 6. Red zones and relocation horizons

The shortest return period — across 5-, 25- and 100-year events — at which
*any* of five hazards (flood, landslide, waterlogging, coastal erosion,
cloudburst) renders a cell uninhabitable. That single number is the Red Zone
index, and it maps directly onto a planning horizon: immediate (<10 yr),
short-term (<30 yr), medium-term (<100.5 yr), monitor (no habitation-
threatening hazard at the 100-year event).

**A settlement's horizon uses population-weighted exposure, not the minimum
hazard cell.** Taking the minimum meant one flooded field on a village's edge
put the whole settlement at "immediate," which made every habitation in a
district top priority — and a list where everything is top priority is not a
list. The reported return period is the shortest one at which at least a
quarter (`MATERIAL_EXPOSURE`) of the settlement's people are in a red cell.

**A real bug this discipline caught tonight:** a settlement whose footprint
geometrically touched a red cell — but where the real, patchy GHS-POP data
showed *zero* people actually living on that cell — was still being assigned
a relocation horizon. The fallback logic had assumed population always
tracked built-up smoothly, an assumption that held under modelled data and
broke under real data. Fixed: a red cell with nobody in it now correctly
reports "monitor."

Priority score: exposure 0.34, frequency 0.26, unsuitability 0.16,
fraction-in-red 0.08, population vulnerability 0.16.

---

## What is not claimed

The flood model is **not hindcast against a gauged historic event** — no
public CWC feed exists to validate against. The Red Zones' ranking *has* been
checked against the observed disaster record: mean susceptibility percentile
66.6 against a chance baseline of 50 (Spearman ρ = 0.462, n = 4 events) — the
right direction, on too small a sample to call validated, and reported with
that sample size attached for exactly that reason.

SAR flood detection is measured per terrain type, not averaged into one
number. Lowland/coastal districts, re-measured tonight after real (patchier)
GHSL/WorldCover land cover replaced the modelled surface: precision mean
**0.83** (minimum 0.73), recall **0.47**, F1 **0.58** across 16 districts.
Recall on the plains dropped materially against the earlier modelled-data
measurement (0.75 → 0.47) — real land cover is patchier than the smooth
surface it replaced, and that genuinely confuses the Otsu threshold more than
expected. Precision held almost exactly. That drop is reported as measured,
not chased to a root cause tonight, and not tuned away by loosening the
detector. Steep-mountain districts run precision 0.84 but recall only 0.16
(C-band VV is a poor flood sensor where layover and shadow corrupt the
geometry) — the sensor's limit, not the code's.
