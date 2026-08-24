# Demo runbook — proving it is live, not just saying it

Six proof moves, in order. Each one takes under a minute and each one shows
something rather than claims it.

## 1. There is not one API key in this codebase

```bash
grep -ri "api_key\|apikey\|api key" backend/app -r
```

Every hit is a comment reading *"no API key required"* — never a credential.
Say it plainly: *"Every data source behind this application is open. Nobody
had to register for anything."*

## 2. The timestamp is minutes old

Open **Now**. `generated_at` and `build_seconds` on the board are real —
point at the clock, point at the reading.

## 3. Match it against the source, live, in a second tab

Pick any district centroid and open Open-Meteo's own forecast URL for that
coordinate. The rainfall figure on the card matches, because it came from
there a few minutes ago.

## 4. Flip a district from demo to live

`?live=true` on a district assessment changes `rainfall_source` from
*"synthetic design storm (IMD normals + Gumbel DDF)"* to the real Open-Meteo
forecast, and the provenance badge changes with it. Population, built-up
land, cropland, roads and facilities are **already real regardless** — the
toggle only changes which rainfall drives the physics.

## 5. Sentinel-1 catalogue query is live

Real granule acquisition times, queried from Copernicus anonymously, no
account. This is the one satellite feed genuinely queried in real time rather
than baked — say so, and say clearly that the *imagery itself* is simulated,
because downloading and processing a real granule is the one thing in this
system that needs credentials.

## 6. Pull the cable — the moment that actually lands

Unplug the wifi. Then:

- The four **replay boards** load in well under a second, from disk, with
  zero network calls — verified by a test that makes every network call
  raise (`test_replay_boards_are_baked_and_load_without_a_network`).
- **Terrain, red zones, relocation, mitigation** all keep serving — every
  layer that matters is baked into the installer.
- The **live board** does not pretend. With no connection it reports that it
  has none, rather than scoring every district zero. *"A board of zeroes
  reads as a national all-clear. We would rather say we do not know."*

---

## If the venue wifi is dead from the start

Lead with a replay instead of live. The offline story becomes the *opening*
rather than the recovery — arguably the stronger version of the demo, since
nobody has to take "it still works offline" on faith.

## What to say if asked "how much of this is real"

> *"Elevation, population, built-up land, cropland, roads, facility
> locations, district boundaries, rainfall, cloud structure and cyclone
> tracks are all real and open. What's still modelled — facility bed counts,
> the radar imagery, river stage — is labelled everywhere it appears, and I
> can show you exactly where each one is in the code."*

## What to say if asked about a specific gap

Two features were **tried and deliberately not shipped**, and both are
stronger answers than pretending nothing was tried:

- **GSI Bhukosh** (lithology): connection times out from this network,
  verified directly.
- **GEM Global Active Faults** (structural hazard proxy): reachable, wired,
  fully tested — then measured across all 22 modelled districts and found too
  sparse over India to use. Every Himalayan hill district shows either zero
  mapped faults or one 60–120 km away, which would have made the Himalaya
  read as *safer* than the honest neutral rating does. See `DATASETS.md` for
  the exact numbers.

## Numbers worth having ready

- **309 tests passing**, 5 skipped.
- **22 districts** with full physics; **735** addressed nationally at a
  weather-screening tier, clearly separated on screen.
- Population reconciliation ratio (observed ÷ Census projection) across the
  22 districts: **0.73 to 2.09**, median **1.35** — two real sources, both
  reported.
- SAR flood detection, re-measured after tonight's real land-cover data:
  lowland/coastal mean precision **0.83** (min 0.73), recall **0.47**, F1
  **0.58** across 16 districts — recall dropped materially against the old
  modelled-data measurement (0.75), reported as found rather than tuned away.
- Red Zone ranking against the observed disaster record: mean susceptibility
  percentile **66.6** vs. a chance baseline of **50** (n = 4 events —
  reported honestly as too few to call validated).
