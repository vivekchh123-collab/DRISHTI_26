# Roadmap

What is built, and what comes next. Everything below the line marked *built* is
a proposal, and is described here rather than stubbed in code, so that nothing
in the running system pretends to exist when it does not.

---

## Phase 1 — Built and tested

- **Real 30 m terrain** — Copernicus DEM GLO-30 baked for 22 districts across
  four terrain archetypes. HAND, slope, wetness index, flow routing, the BIS
  landslide rating and the erosion model all derive from real elevation.
- Five hazards: flood, landslide (BIS IS 14496 LHEF), waterlogging, coastal
  erosion, cloudburst (IMD ≥100 mm/hr)
- Recurrence-based Red Zones across 5-, 25- and 100-year events
- Relocation site suitability, carrying capacity and habitation ranking
- **Population vulnerability** (Census 2011) weighted into habitation priority
- **Disaster history** — 41 cited events 1999–2024, plus 328 IBTrACS cyclone
  tracks, on a national timeline
- **Encroachment** — GHSL built-up surface differenced 2015→2025 and intersected
  with the Red Zones: where construction is still growing inside them
- **Mitigation** — 18 measures across structural, regulatory, relocation and
  preparedness, each naming an accountable authority and the statute it rests on
- **State rollup and CSV export** for State Disaster Management Authorities
- **All 735 districts addressed**, at two clearly-separated tiers — 22 modelled,
  713 screening-only on live weather
- 287 automated tests, including the safety invariant that no relocation site
  may fall inside a Red Zone
- Live rainfall, soil moisture and CAPE (Open-Meteo); live Sentinel-1 catalogue
- Two ML models, validated on held-out districts
- Windows desktop installer — no Python, no Node required

---

## Phase 2 — Better inputs

Elevation and boundaries are **done** — Copernicus DEM GLO-30 and geoBoundaries
ADM2 respectively, both open and requiring no credentials. What remains:

| Input | Source | Effect | Status |
|---|---|---|---|
| Elevation | Copernicus DEM GLO-30 | Real 30 m terrain for every hazard output | ✅ **shipped** |
| District boundaries | geoBoundaries ADM2 | All 735 districts, replacing the approximate envelope | ✅ **shipped** |
| Built-up surface | GHSL GHS-BUILT-S | Encroachment inside Red Zones, 1975–2030 | ✅ **shipped** |
| Lithology and structure | GSI Bhukosh | Completes the BIS LHEF assessment. Two of six factors are still held at a neutral rating and reported as such. | ⬜ open |
| Housing type | Census House Listing | Vulnerability currently uses literacy, SC/ST share and rurality at **state** resolution. Kachcha-housing share at district resolution would improve it materially. | ⬜ open |

No code changes for the remaining two: `landslide.load_geology` and
`vulnerability.load_district_indicators` already accept them, and the API stops
reporting `assessment_complete: false`.

---

## Phase 3 — River gauges and national coverage

- **CWC gauge feed.** The connector is written and waiting on an endpoint; see
  [LIVE_DATA_SETUP.md](LIVE_DATA_SETUP.md) for why no public API exists and the
  three routes to a real one.
- **All districts — shipped, at screening depth.** All 735 are addressed; 22
  carry the full physics. What remains is *promotion*: baking elevation for more
  districts moves them from screening to modelled. That is a data task and a
  disk-space question, not a code change.

---

## Phase 4 — Citizen reporting as a ground-truth channel

Most Indian villages have no river gauge and no soil sensor. All of them have
phones. A hotline turns that into a data layer for the places instrumentation
does not reach.

**Why it belongs in this problem statement.** SIH26191 asks the system to
integrate *hazard intensity, population vulnerability and disaster history*.
Disaster history is the weakest of the three for ungauged terrain, and citizen
reports are how you build it. This is a **ground-truth channel feeding the
recurrence model** — not a warning product. Framed as anything more, it drifts
into a different problem statement.

### How it would work

```
  call ──▶ IVR captures village ──▶ LGD code ──▶ lat/lon
                                                   │
                          a single call is a rumour │
                          a cluster is a signal     ▼
                                        spatio-temporal clustering
                                                   │
                    ┌──────────────────────────────┼──────────────────────────┐
                    ▼                              ▼                          ▼
              rainfall anomaly            terrain susceptibility       latest SAR pass
                    └──────────────────────────────┼──────────────────────────┘
                                                   ▼
                                         evidence fusion  ──▶  confidence per hazard
                                                   │
                                                   ▼
                                    logged to the district's disaster history
```

### Two things to get right, because both are easy to get wrong

**Location does not come from the call.** A voice call or SMS gives you the
caller's *number*, never their position. Cell-tower location needs a telecom
licence agreement; GPS needs an app, which defeats the purpose of a hotline.
The method that works on the feature phone a farmer actually owns is **IVR
capturing the village name, resolved against LGD** — the Local Government
Directory, India's authoritative registry of every village and block with codes.
It is also better than commercial geocoders, whose Indian village coverage is
patchy.

**Scoring starts Bayesian, not learned.** There is no labelled dataset of
*(call cluster → confirmed hazard)*, so a gradient-boosted model trained today
would be trained on nothing. Phase 4a treats each signal — call density,
rainfall anomaly, terrain susceptibility, satellite agreement — as a likelihood
ratio and multiplies them: explainable, needs no training data, works on day
one. Phase 4b replaces it with gradient-boosted trees **once the system has
logged real events with outcomes**, which is the same discipline applied to
every other model here.

### What it needs

| Component | Status |
|---|---|
| Webhook endpoint for call events | buildable now |
| LGD village geocoder | buildable now |
| Spatio-temporal clustering | buildable now |
| Cross-check against rainfall, terrain, SAR | **layers already exist** |
| Evidence-fusion confidence score | buildable now |
| Telephony (Exotel preferred for India; Twilio workable) | **paid account + KYC** |

The telephony provider only has to POST into the webhook, so connecting a real
line is a configuration change rather than a rewrite.

---

## Phase 5 — Institutional integration

Government partnership, not engineering. Do not promise these for a demo.

- **Official short code** (a 1077-style number) — requires a telecom allocation.
- **NDMA Sachet.** Alerts cannot be pushed into Sachet without being an
  authorised agency. What the system already does is emit **CAP-format XML**,
  the exact standard Sachet consumes, so integration becomes a permission rather
  than a build.
- District Disaster Management Plan alignment, and sign-off from the relevant
  SDMA before any output is used operationally.

---

## Deliberately not planned

- **Real-time flash-flood warning.** Sentinel-1 revisits every 2–6 days. A
  system built on it should not claim minute-scale warning, and this one does
  not.
- **Replacing the physics with ML.** A family being moved out of their home is
  entitled to a reason better than a model's activation. ML stays where it is
  measurable and out of the decision path.
