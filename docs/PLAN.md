# DRISHTI — Master Plan

**District Risk Intelligence & Satellite Hazard Tracking Interface**

Smart India Hackathon submission. This document is the single source of truth for
what we are building, why, and in what order.

---

## 1. What it is

> A satellite-fed flood dashboard that tells a District Magistrate **which villages
> are worst hit right now, and exactly what to do about it.**

**Who it is for:** the District Emergency Operations Centre. One district, one
screen, at 2 a.m., during a flood.

**The gap we fill:** ISRO's Bhuvan and NDEM already publish real flood maps. They
are good. But they stop at a picture, delivered as a file, for a scientist.

| | Existing systems | DRISHTI |
|---|---|---|
| Output | A map | A ranked task list |
| User | Analyst | District Magistrate |
| Answers | "Where is the water?" | "Where do I send the boats?" |

**One-line pitch:** *Their output is a map. Ours is a decision.*

---

## 2. Scope

**In scope — flooding, in depth:**

- Riverine flood (Brahmaputra, Bagmati, Kosi)
- Storm-surge flood (Bay of Bengal cyclones)
- Urban pluvial flood and **waterlogging**
- Flash flood (steep catchments)

**Out of scope for v1 (documented as roadmap, not faked):**

- Landslide susceptibility — the hazard interface is built to accept it later
- Drought, heatwave, earthquake

**Why flood-only:** depth beats breadth. Floods affect more Indians than any other
natural hazard. A judge can push hard on one deep module and we hold; they will
break three shallow ones in a minute.

---

## 3. How it works — the pipeline, end to end

Ten stages, raw signal to a decision on a phone.

```
  ┌── STAGE A: ALWAYS ON, WHOLE COUNTRY ─────────────────────────┐
  │                                                               │
  │  1. INGEST      INSAT-3D cloud (30 min)                       │
  │                 GPM rainfall (4 h)                            │
  │                 CWC river gauges (1 h)                        │
  │                        ↓                                      │
  │  2. SCREEN      score all 766 districts every 30 min          │
  │                 cold cloud x rain anomaly x wet ground        │
  │                 x gauge near danger level                     │
  │                        ↓                                      │
  │                 WATCHLIST: the 6 districts that matter today  │
  └───────────────────────────┬───────────────────────────────────┘
                              │  only these go deep
  ┌── STAGE B: DEEP MODEL, ONE DISTRICT ─────────────────────────┐
  │                                                               │
  │  3. TERRAIN     elevation -> HAND, wetness, flow paths        │
  │                 (precomputed once, cached forever)            │
  │                        ↓                                      │
  │  4. DETECT      Sentinel-1 radar: before vs now -> water      │
  │                        ↓                                      │
  │  5. MODEL       between radar passes, predict from            │
  │                 rain + river level + HAND                     │
  │                        ↓                                      │
  │  6. DEPTH       water edge + ground height -> depth in metres │
  │                        ↓                                      │
  │  7. EXPOSE      overlay people, roads, hospitals, crops       │
  │                        ↓                                      │
  │  8. RANK        score every cell -> worst-affected list       │
  │                        ↓                                      │
  │  9. ACT         playbook + shelters + routes + resources      │
  │                        ↓                                      │
  │ 10. DELIVER     dashboard · SMS/CAP alert · PDF sitrep        │
  └───────────────────────────────────────────────────────────────┘
```

### The stages in plain words

**1. Ingest.** Pull whatever is fresh. Cloud pictures every 30 minutes, rainfall
every 4 hours, river levels every hour, radar every few days.

**2. Screen.** Cheap check on every district in India, every half hour. Is there
violent cloud here? Is the rain unusual *for this place, this week*? Is the ground
already saturated? Is the river close to danger level? Score it. Only districts
that fire go to Stage B.

*Why: we cannot run a 10 m model over all of India. We can run a cheap screen over
all of India and spend real compute on the six districts that matter.*

**3. Terrain.** For each district, turn elevation into the two numbers that decide
everything:

- **HAND** — how many metres above the nearest river is this spot? If it is 2 m
  above and the river rises 2 m, it goes under. This gives us the flooding order
  of every point in the district before a drop of rain falls.
- **TWI** — is this a bowl that collects water and cannot drain? That is a
  waterlogging trap.

Computed once per district and cached.

**4. Detect.** Radar sees through cloud. Water is smooth, so it bounces radar away
and appears **black**. Compare a "before" radar image to "now": grey that turned
black is new flood. Then remove the permanent river, steep slopes, and anything
too high above the river to be flooded — those are the known false positives.

**5. Model.** Radar only comes every few days. In between, we predict: rainfall
becomes runoff, runoff raises the river, river level plus HAND gives an inundation
map, refreshed hourly. When a radar pass lands, we **snap the model back to
observed truth**.

*This is data assimilation — how weather agencies actually work. It means the
dashboard is never blank, only more or less confident.*

**6. Depth.** Radar tells us where water is, not how deep. Take the ground height
at the water's edge, read the water surface level, subtract the ground height
inside. Result: depth in metres. 0.5 m means wade. 1.8 m means send a boat.

**7. Expose.** Lay the population map, road network, hospitals, schools and
farmland over the flood map. Water in an empty field is a statistic. Water in a
village of 4,000 is an emergency. **This step turns a map into a priority.**

**8. Rank.** Every cell gets a score:

```
people affected  x  water depth  x  how long it will stay  x  is it cut off?
```

Sort. Top of the list is where you go first. This is the answer to *"which regions
are worst affected."*

**9. Act.** The part nobody else builds. For each top zone:

- how many boats, and from where
- nearest shelter, whether it has capacity, which one to open next
- which roads are cut, what the alternative route is
- food packets, ORS, medical teams, pumps — from official IRS norms
- a ready-to-send SMS in the local language

Every instruction traces back to an **NDMA SOP**, not to our opinion.

**10. Deliver.** Screen, alert, or printable situation report.

---

## 4. What you see — three screens

| Screen | When | Shows |
|---|---|---|
| **NATIONAL** | Always | Map of India, districts lit by alert score. The watchlist. |
| **DISTRICT** | Event | The flood map, worst-affected list, action cards, time slider |
| **PLANNING** | Peacetime | Waterlogging hotspots, shelter coverage gaps, HAND risk map |

**District screen layout**

```
┌──────────────────────────────────────────────────────────────┐
│  DRISHTI    Darbhanga, Bihar        [🟢 SAR-OBSERVED 4h ago] │
│  ┌────────────────────────────┐  ┌────────────────────────┐  │
│  │                            │  │ WORST AFFECTED         │  │
│  │                            │  │ 1. Zone 14  4,200 ppl  │  │
│  │       FLOOD MAP            │  │    1.8 m · CUT OFF     │  │
│  │       (click a zone)       │  │ 2. Zone 07  3,100 ppl  │  │
│  │                            │  │ 3. Zone 22  2,800 ppl  │  │
│  │                            │  ├────────────────────────┤  │
│  │                            │  │ ACTIONS                │  │
│  │                            │  │ ▸ 6 boats to Zone 14   │  │
│  │                            │  │ ▸ Open 2nd shelter     │  │
│  └────────────────────────────┘  │ ▸ NH-57 cut, use bund  │  │
│  ◀━━━━━━━━●━━━━━━━━▶  Day 3      └────────────────────────┘  │
│     time slider: watch the flood grow and recede             │
└──────────────────────────────────────────────────────────────┘
```

**Three demo moments, in order:**

1. India map, districts lighting up → zoom into one. *"Here is why we picked this one."*
2. Drag the time slider → the flood grows and shrinks. Motion is what judges remember.
3. Click the worst zone → a task list appears. *"That is the difference between a map and a decision."*

---

## 5. Where the data comes from

### Live sources (all free)

| Source | What | Fresh every | Resolution |
|---|---|---|---|
| **Sentinel-1** (Copernicus) | Radar flood extent | 2–6 days | 10 m |
| **INSAT-3D/3DR** (MOSDAC) | Cloud, storm intensity | 30 min | 1–4 km |
| **GPM IMERG Early** (NASA) | Rainfall | 4 hours | 11 km |
| **CWC** | River gauge levels + danger thresholds | 1 hour | Point |
| **Bhoonidhi / NRSC** | Resourcesat AWiFS, EOS-04 SAR | 5 days | 56 m |
| **Sentinel-2** | Crop damage, after the storm | 5 days | 10 m |
| **Bhuvan WMS** | NRSC flood layers | Event | Varies |

**Accounts to open now** (Bhoonidhi first — approval takes a day or two):

1. Bhoonidhi / NRSC — `bhoonidhi.nrsc.gov.in`
2. Copernicus Data Space — `dataspace.copernicus.eu`
3. NASA Earthdata — `urs.earthdata.nasa.gov`
4. Google Earth Engine — fastest path to preprocessed Sentinel-1

### The confidence badge — always visible

| Badge | Meaning |
|---|---|
| 🟢 **SAR-OBSERVED** | Real radar, under 12 hours old |
| 🟡 **MODELLED** | Between radar passes — predicted from rain + gauges |
| 🟠 **DEMO** | Simulated input data, offline mode |

**The rule: never tell a judge demo data is real satellite imagery.** The
algorithms are real and citable either way. Saying so plainly earns more credit
than the lie would, and it means a dead venue wifi cannot kill the demo.

---

## 6. What we build

| # | Module | File | Does | Status |
|---|---|---|---|---|
| 1 | District registry | `data/districts.py` | 22 real districts, 4 archetypes | ✅ done |
| 2 | Analysis grid | `core/grid.py` | 160×160 raster frame per district | ✅ done |
| 3 | Terrain + hydrology | `core/terrain.py` | HAND, TWI, slope, D8 flow | ✅ done |
| 4 | Radar flood detection | `core/sar.py` | Speckle filter, Otsu, change detection | ⏳ |
| 5 | Rainfall + runoff | `core/rainfall.py` `core/hydrology.py` | SCS-CN runoff, routing, river stage | ⏳ |
| 6 | Flood engine | `core/flood.py` | Extent, depth, duration, forecast | ⏳ |
| 7 | Waterlogging | `core/waterlogging.py` | Hotspots + drain-down time | ⏳ |
| 8 | Exposure | `core/exposure.py` | Population, roads, infra, crops | ⏳ |
| 9 | Impact ranking | `core/impact.py` | Worst-affected list, cut-off detection | ⏳ |
| 10 | Action playbook | `core/playbook.py` `data/sops.py` | NDMA SOP engine | ⏳ |
| 11 | Response planning | `core/response.py` | Shelters, routes, resources, alerts | ⏳ |
| 12 | National screen | `core/screen.py` | 766-district alert score | ⏳ |
| 13 | Data providers | `providers/*.py` | Copernicus, IMD, CWC, demo | ⏳ |
| 14 | API | `api/*.py` `main.py` | FastAPI + auto docs | ⏳ |
| 15 | Frontend | `frontend/` | Leaflet map, panels, time slider | ⏳ |

**Stack:** Python + FastAPI + NumPy backend, vanilla JS + Leaflet frontend.
No Node build step, no GDAL. Runs on any laptop with `python -m uvicorn`.

---

## 7. Build order

**Phase 1 — the spine.** Modules 4, 5, 6 + a bare API. One district end to end:
radar in, flood map out. *Proves the core works.*

**Phase 2 — the differentiator.** Modules 8, 9, 10, 11. The ranked list and the
action cards. *This is the part that wins.*

**Phase 3 — the demo.** Modules 14, 15. Map, time slider, action panel.
*Make it look like a product.*

**Phase 4 — the scale story.** Modules 7, 12, 13. Waterlogging, national screen,
live connectors. *Answers "does it scale" and lights the LIVE badge.*

**Phase 5 — the paperwork.** Methodology doc, demo script, hindcast validation.

If time runs short, cut Phase 4 before Phase 2. A narrow system that gives real
answers beats a wide one that gives pictures.

---

## 8. Every algorithm is published and citable

Nothing invented. This is the sheet to hand a judge who asks how it actually works.

| What | Method | Source |
|---|---|---|
| Water from radar | Otsu automatic thresholding | Otsu 1979 |
| Radar noise | Lee speckle filter | Lee 1980 |
| Flooding order | HAND | Rennó 2008, Nobre 2011 |
| Waterlogging | Topographic Wetness Index | Beven & Kirkby 1979 |
| Slope | Horn 3×3 | Horn 1981 |
| Sink removal | Priority-flood | Wang & Liu 2006 |
| Flow routing | D8 | O'Callaghan & Mark 1984 |
| Water depth | FwDET | Cohen 2018 |
| Rain to runoff | SCS Curve Number | USDA-NRCS |
| Storm intensity | Cold cloud / IR brightness temperature | Standard MCS thresholds |
| Actions | NDMA flood SOPs + IRS norms | NDMA guidelines |

---

## 9. Hard questions, and our answers

**"Bhuvan already does this."**
> Bhuvan gives an analyst a map. We give a District Magistrate a ranked task list.
> We consume Bhuvan; we do not compete with it.

**"Sentinel-1 only passes every 2–6 days. Your data is stale."**
> We are not primarily a warning system. Assam 2020 lasted six weeks — nobody's
> problem was not knowing a flood was coming, it was not knowing which of 2,100
> villages were under water today. That is a mapping problem, and a 2–4 day
> revisit solves it. Between passes we model and label it 🟡 MODELLED.

**"Is this real satellite data?"**
> The algorithms are real and cited. Input is 🟠 DEMO right now; here is the
> Copernicus connector and here is the badge that shows which mode we are in.

**"Does it scale to all of India?"**
> Two stages. Cheap screen over 766 districts every 30 minutes, expensive 10 m
> model only where it fires.

**"What about flash floods and cities?"**
> Radar cannot catch a 4-hour Chennai flood — we say so. That is what the
> 30-minute cloud screen is for. Different sensor, different timescale.

**"Where is the AI?"**
> Physics first, because physics is auditable and a District Magistrate must be
> able to justify an evacuation order. ML sits where it earns its place:
> refining the water/land threshold and learning the cloud-to-flood pattern from
> past events.

---

## 10. Known limits — say these before a judge finds them

- Sentinel-1 revisit is a hard floor: 2–6 days at 10 m
- Radar false positives: dry sand, smooth tarmac, radar shadow — mitigated with
  permanent-water, slope and HAND masks
- Radar struggles under dense canopy and inside dense cities
- CWC has roughly 200 gauge stations for the whole country
- Cloud screening gives 30 min – 6 h of lead time, and will over-warn. A nowcast
  that never over-warns is under-warning.
- Population is a 2011 census projection, not a live count

---

## 11. Deliverables

1. **Working application** — runs offline on any laptop
2. **Methodology document** — every algorithm, every citation, every limitation
3. **Demo script** — timed 5-minute walkthrough with a no-network fallback
4. **Hindcast validation** — model run against the district's real reference event
   (Assam 2020, Fani 2019, Chennai 2015) with the numbers we got vs what happened

---

*Status: modules 1–3 built and tested. Next: module 4, radar flood detection.*
