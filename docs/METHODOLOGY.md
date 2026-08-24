# Methodology

How DRISHTI turns elevation and radar into a ranked task list, and the published
source for every step.

This is the document to hand a reviewer who asks how it actually works. The same
table is served machine-readably at `/api/methods`, generated from the same
source, so the documentation cannot drift from the code.

Companion documents: [ASSUMPTIONS.md](ASSUMPTIONS.md) for what each step gets
wrong, [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the walkthrough.

---

## Stage 1 — Terrain

Everything begins with a 160 × 160 square analysis grid centred on the district
centroid, sized so its area is 1.39 × the district's. The pad matters: a flood
arrives from upstream and a surge from offshore, so the model has to see beyond
the boundary.

### 1.1 Slope and aspect — Horn (1981)

A 3 × 3 weighted finite difference. Verified in the test suite against a plane of
known gradient: a 10 % slope must read atan(0.1) = 5.7106°.

### 1.2 Depression filling — Wang & Liu (2006)

Priority-flood. Cells are pushed into a min-heap from the grid boundary inward;
any cell lower than the level at which it is reached is raised to that level.
This guarantees every cell has a non-ascending path to the edge, which is what
makes flow routing terminate.

The spill level of a pit is the minimum, over all escape routes, of the highest
point along that route — not simply the lowest point on its rim. Both cases are
tested.

### 1.3 Flat resolution — Garbrecht & Martz (1997)

Filling leaves large regions at exactly one elevation. D8 has no gradient to
follow across them and breaks the tie in visit order, which draws long straight
parallel channels converging on the spill point. On a Bihar floodplain, filled
flats reach 17 % of the district and that artefact dominates the map.

Two distance fields are combined:

```
increment = 2 × (distance from outlet) + (max − distance from high edge)
```

The **weight of 2 is load-bearing.** Stepping one cell toward the outlet always
reduces the first term by 1, but can raise the second by 1. Weighted equally the
two cancel, the cell keeps no strictly-lower neighbour, and D8 dead-ends
mid-flat — which fragmented our stream network into 59 disconnected pieces. At
weight 2 the sum falls by at least 1 per step, so every flat cell provably
drains.

The imposed relief is capped at both half the drop below the flat *and* half the
rise above it. Capping only the drop lets a raised flat cell strand a neighbour
outside the flat that used to drain into it.

Post-condition, asserted for all 22 districts: **zero interior cells without a
lower neighbour.**

### 1.4 Flow routing — O'Callaghan & Mark (1984)

D8 steepest descent over the eight neighbours, distance-weighted so diagonals
compete fairly. Accumulation walks cells in descending elevation order, so each
is visited once.

### 1.5 HAND — Rennó (2008), Nobre (2011)

**The keystone.** For each cell, the vertical distance to the drainage cell it
flows into. Computed by walking the ascending-elevation order once, so each cell
inherits its receiver's already-resolved outlet in O(n).

A cell with HAND = 2 m floods when its river rises 2 m, wherever it sits. That
turns inundation from a hydrodynamic solve into a subtraction, and it is why a
district resolves in about 1.5 seconds.

We also retain **which** drainage cell each cell drains into. That is what lets
one district hold several rivers at different stages simultaneously.

### 1.6 Topographic wetness index — Beven & Kirkby (1979)

`ln(a / tan β)` — large upslope area draining onto flat ground. The terrain
signature of a waterlogging trap.

---

## Stage 2 — Radar observation

### 2.1 Why radar

A flood happens under storm cloud. Optical satellites see cloud. Sentinel-1's
C-band radar passes through weather and works at night, and open water is
specularly smooth so it reflects the pulse away from the sensor. **Water is
black.**

### 2.2 Speckle suppression — Lee (1980)

Speckle is multiplicative, so a plain blur destroys edges while removing it. The
Lee filter interpolates between the local mean and the observed value by how
much local variance speckle alone can explain: flat regions smooth hard, edges
survive. Tested to preserve the mean (no radiometric bias) while reducing
variance.

### 2.3 Thresholding — Otsu (1979)

Maximises between-class variance, landing in the trough of a bimodal water/land
histogram with no tuning.

**Its failure mode is the important part.** Otsu returns a threshold whether or
not a water mode exists; on a dry scene it happily splits cropland from built-up
and declares half the district to be water. Two guards:

- **Separability** (between-class variance as a fraction of total) is reported
  alongside. But note its floor: a single Gaussian split at its median scores
  ω₀ω₁(μ₀−μ₁)²/σ² = 0.25 × 1.5958² = **0.637** analytically. Any scene scores
  ~0.64, so separability alone cannot detect the absence of water. Confidence
  bands are set above that floor.
- **A physical ceiling of −13 dB.** Open water in VV essentially always lies
  below it. If Otsu returns higher, there is no water mode and the result is
  reported as low confidence. This is what actually rejects a dry scene.

### 2.4 Change detection and terrain masking

A pre-event baseline separates permanent water from new flooding: a cell must
have *darkened* by ≥ 2.5 dB. Then three terrain masks remove the well-known
false positives — dry sand, smooth tarmac and radar shadow are radiometrically
indistinguishable from water, and only the DEM separates them:

- permanent-water reference
- slope ≤ 12° (steeper ground cannot hold standing water)
- HAND ≤ 25 m (ground too high above its river to be reached by it)

**Measured performance**, scored against truth the detector was never shown:
precision **0.924**, recall **0.675**, F1 **0.780**. Recall is bounded below by
physics — flooded vegetation double-bounces off crop stems and barely darkens in
C-band VV.

---

## Stage 3 — Rainfall to river stage

### 3.1 Design storm

Depth-duration-frequency from the district's IMD annual normal, scaled by Gumbel
frequency factors to 5 / 25 / 100-year return periods. Temporal shape by storm
type — convective (short, violent), orographic (sustained), monsoon depression
(broad, twin-peaked). Spatial pattern is orographic over hills.

### 3.2 Runoff — SCS Curve Number (USDA-NRCS NEH-630)

```
S  = 25400/CN − 254        Ia = 0.2 S
Q  = (P − Ia)² / (P − Ia + S)   for P > Ia
```

Curve numbers from land cover, adjusted for antecedent moisture by interpolating
between the handbook's AMC I / II / III conversions on the **Antecedent
Precipitation Index** — a decay-weighted sum of the last week's rain. This is the
difference between a storm that runs off and one that soaks away, and it is why
Kerala 2018 was catastrophic: the catchment was already saturated.

Computed on the cumulative rainfall curve and differenced, so initial abstraction
behaves correctly — the first millimetres soak in, later ones do not.

### 3.3 Routing

Runoff is accumulated downstream over the existing D8 network, then delayed by a
gamma unit hydrograph whose time-to-peak scales with catchment area (∝ A^0.30).
Cells are grouped into area classes so headwaters peak hours before the trunk
they feed.

### 3.4 Stage — Manning, compound section

Channel width from downstream hydraulic geometry (Leopold & Maddock, 1953);
bankfull depth likewise. Then Manning for a **compound** section — in-bank
channel plus a rougher floodplain that only exists above bankfull and whose width
grows as it deepens.

Solving against the channel alone sends every extra cubic metre into depth and
produced a **15 m stage on the Bagmati**, which is impossible. Iterating width
against depth does not converge either: with a floodplain widening by hundreds of
metres per metre the map is not a contraction and successive substitution
oscillates between wildly overbank and comfortably in-bank.

Conveyance is, however, strictly increasing in depth. **Bisection** is therefore
unconditionally convergent and trivially vectorised. Tested both for monotonicity
and for round-trip consistency: solve for stage, feed it back, recover the
discharge.

---

## Stage 4 — Inundation

```
depth = stage at the cell's own outlet − the cell's HAND
```

The operational form of FwDET (Cohen et al., 2018). Storm surge floods by
absolute elevation instead, with a connectivity test so an inland hollow below
the surge height is only wetted if water can actually reach it. Surge and
riverine flooding combine by taking the **deeper**, not the sum — adding would
double-count the same water.

Reporting floor 0.15 m. Depth bands are set where a responder's action changes:
wadeable, impassable, boat-only.

---

## Stage 5 — Exposure

Population is allocated **dasymetrically** — the district total distributed in
proportion to modelled built-up intensity. An even spread puts a third of a
district's people in the river and makes every ranking meaningless. Built-up
intensity follows terrain: flat, well-drained, near water but not in it, which
reproduces the characteristic ribbon of villages on the higher terrace.

Roads are least-cost paths over a surface where slope and river crossings are
expensive, so they follow valleys and cross rivers at a few points — which is
what makes cut-off analysis meaningful. Facility counts follow IPHS provision
rates.

The district total is preserved exactly. Asserted.

---

## Stage 6 — Impact

Zones are **settlement catchments** (nearest-settlement Voronoi), so a zone
corresponds to somewhere you can send boats.

```
score = 0.40 population + 0.22 depth + 0.14 duration
      + 0.16 isolation  + 0.08 vulnerability
```

Each component normalised against the worst zone in that district. **Isolation**
is a breadth-first search over road cells below 0.30 m — the depth at which a
relief truck stops. That flag is the most operationally useful output in the
system: a moderately flooded but unreachable zone needs boats and airlift, while
a deeper reachable one needs trucks.

Every component is returned alongside the total. The weights are published so the
ranking can be argued with rather than trusted.

---

## Stage 7 — Response

Rules are a declarative table over depth, isolation, duration and exposure, each
carrying its NDMA / IPHS / Sphere source through to the screen.

Shelter allocation is **proportional with a worst-first residual pass**. Pure
worst-first greed is what a naive loop does and it is wrong: when demand exceeds
capacity — which for a district of five million it always does — the top zone
swallows every shelter and every other zone is told it has nowhere to send
anyone.

Where a requirement exceeds what a district holds, that is surfaced as an
**escalation** rather than hidden. "This needs 1,039 boats and you have about 30"
is not a silly number; it is the finding, and it says call the State EOC tonight.

Supply quantities come from Sphere and NDMA norms, each with the norm and the
arithmetic shown.

---

## Waterlogging

A separate product, because it is a separate problem. River flooding leaves when
the river falls; waterlogging is rain with nowhere to drain to and persists for
weeks.

```
susceptibility = 0.34 TWI + 0.30 (1−HAND) + 0.21 imperviousness
               + 0.15 (1−drainage density)
```

Drain-down time from linear-reservoir recession, with residence time short on
paved drained ground and long in a flat clay basin. Each hotspot reports **why**
it is one, not just a score.

Computable before it rains — a standing planning asset for siting drainage
investment.

---

## The live board — reading cloud pattern without predicting rain

The board answers *where is dangerous right now*. It is the only part of the
system that runs on data measured this morning, and it contains **no machine
learning at all** — deliberately. A model that predicts rainfall is a model that
has to be believed; this reports what the sky is doing against published
thresholds, so every step can be checked.

### Why not cloud cover

Total cloud cover says the sky is grey. It does not distinguish a wet afternoon
from Kedarnath. What distinguishes them is the **vertical structure** of the
column and the **rate at which it is deepening**.

| Signal | What it means |
|---|---|
| `cloud_cover_low` / `mid` / `high` | Deep convection fills all three at once. Stratiform monsoon rain sits low and mid and leaves the high level comparatively clear. |
| high ≫ low | The **anvil signature** — cirrus outflow from a cumulonimbus that has already topped out. This is the cold-cloud shield IMD reads off INSAT. |
| `cape` | Energy released if the column is triggered. Structure without CAPE is cloud; structure with CAPE is a storm. |
| `freezing_level_height` | A high freezing level means a deep warm-cloud layer — the classic precondition for Himalayan cloudburst rain rates. |

### The four states

Escalating, each a stated rule over stated numbers (`core/cloudwatch.py`):

1. **quiet** — no level filled, or CAPE below 1000 J/kg
2. **building** — column filling with CAPE above 1000 J/kg
3. **deep convection** — all three levels above 55% with CAPE above 2500 J/kg
4. **mature system** — deep convection plus high cloud at least 25 points ahead
   of low cloud

### The anomaly, and why it is in percentage points

A troposphere-deep column over Cherrapunji in July is a Tuesday; the same column
over Jaisalmer is an emergency. Every reading is placed against **that
location's own five-year mean for that calendar week**, baked per district by
`scripts/fetch_climatology.py` from the ERA5 archive.

Reported in percentage points above normal, not as a ratio: cloud cover is
bounded at 100, so "twice normal" is a meaningless statement about a variable
that cannot exceed 100 no matter what the sky does.

**CAPE has no anomaly.** The ERA5 archive returns it as a column of NaN rather
than as an error, so there is no five-year CAPE baseline to be had. It is judged
against absolute instability bands instead, which need no local baseline. When
replaying a historical day the column is classified on structure alone and the
response says `cape_available: false` — because treating a missing value as zero
would classify a sky filled to 100% at every level as *quiet*, which is exactly
how the morning of the Wayanad landslide would have read as calm.

### River response

Stage against bankfull, from live rainfall routed through the district's real
30 m terrain by the same SCS-CN runoff and Manning hydraulics used everywhere
else. **This is modelled, never gauged.** There is no public CWC feed
(`providers/cwc.py` records what was tried), and a modelled stage presented as a
gauge reading would be a lie with a decimal point on it. Every response carries
`gauged: false` and the provenance sentence.

It is expensive, so it is spent only on districts the cheap signals have already
flagged — the same discipline as the national grid, one level down.

### Ranking by who is standing underneath

Rain over empty ground is weather. The same rain over a district where six lakh
people already live inside a modelled red zone is an emergency. The composite
weights rainfall 0.30, cloud state 0.24, soil saturation 0.18, 72-hour rainfall
0.16 and peak hourly 0.12, then multiplies by standing red-zone population. That
last term is what separates this from a weather app.

### Replay

`GET /api/watch?date=YYYY-MM-DD` runs the identical pipeline against the archive
for a day that has already happened. It exists for after-action review, and it
means the board can be demonstrated on a quiet week without anybody inventing a
storm. Every response is stamped `mode: replay`.

---

## National coverage — two tiers, and why they are kept apart

Every one of India's 735 districts is addressed. They are not known to the same
depth, and conflating the two depths would be the most misleading thing this
system could do.

**Modelled (22 districts).** Everything above: real 30 m Copernicus elevation,
depression-filled and flat-resolved, D8 routing, HAND, the BIS landslide rating,
recurrence across three return periods, exposure and relocation caseload. Roughly
1.5 s of computation per district, and about 25 MB of baked elevation each.

**Screening (713 districts).** The district's bounding box is intersected with
the 27 km national weather grid and the cell scores are averaged. The inputs are
live rainfall, convective energy and soil saturation. **There is no terrain
analysis behind the number** — no HAND, no slope, no routing. It is a statement
about the weather over a district, not about the district.

A bounding box rather than point-in-polygon: at 27 km the grid is coarser than
most districts, so a strict polygon test would leave small districts with no
cells and a misleading zero.

Every record carries its `tier` and a plain-language `basis` string. The
interface renders the two differently, and screening records deliberately carry
**no** population-in-red-zone or red-zone-area fields — there is no physics
behind such figures, so they are not produced at all rather than produced and
disclaimed.

**Offline behaviour.** With no live weather connection every weather field sits
at its default and every screening cell scores zero. Publishing that would render
as a confident national all-clear. Screening districts therefore report *no
score* when the connection is down, with the reason stated. Modelled districts
are unaffected, because their physics is baked.

Promotion from screening to modelled is a data task: bake a district's elevation
with `scripts/fetch_real_dem.py`.

Boundaries are geoBoundaries gbOpen ADM2 (CC-BY 4.0), simplified with
Ramer–Douglas–Peucker at a 0.02° tolerance — about 2 km, invisible at national
zoom, and roughly a 91% reduction in payload.

---

## What is not claimed

The flood model has **not** been hindcast against a gauged historic event.
`scripts/validate_hindcast.py` is the harness, and it states plainly the two
files it needs to run for real.

What *has* been measured, on real terrain: the Red Zones rank the places
disasters actually happened at a mean susceptibility percentile of **66.6**
against a chance baseline of **50** (Spearman ρ = 0.462). That is positive, and
it rests on four events — too few to call the model validated, and reported here
with the sample size attached for exactly that reason.

Radar detection is measured per terrain type rather than averaged: lowland and
coastal districts reach F1 **0.79**, steep-mountain districts **0.25**, because
C-band VV is a poor flood sensor in steep country. See
[ASSUMPTIONS.md](ASSUMPTIONS.md) §7 for the full status of every claim.
