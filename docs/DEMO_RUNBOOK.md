# Demo runbook — proving it is live, not just saying it

Six proof moves, in order. The first two are **interactive** — the
presenter clicks something and the interface visibly changes in front of the
panel, live. Nothing about them is a canned animation; both hit the real
backend and wait for a real answer.

## 1. Press "Refresh live" and watch the timestamp move

Open **Now**. The board is already alive without touching anything — a
pulsing dot beside "LIVE", and a "read Xs ago" clock genuinely ticking up
once a second. That much proves the page is not frozen.

Then press **Refresh live**, top-right of the board. It disables itself,
shows a spinner and "Reading live weather…" for a few seconds — this is a
**real re-read**, not a cached response redressed: it bypasses both the
browser cache and the server's 15-minute cache
(`?force=true`, verified by `test_force_bypasses_the_cache`). When it
returns, the timestamp jumps to *just now* and every card sweeps with a
soft highlight — proof the numbers actually moved, not just that a spinner
finished.

> *"I'm not going to tell you this is live. I'm going to press this button,
> right now, and you watch the clock reset."*

The board also refreshes itself quietly every four minutes on its own — if
one of those catches mid-sentence, that is a bonus, not a glitch.

## 2. Flip a district from demo rain to real rain — the whole screen changes

Go to **District → Inundation & relief**. The **Live rainfall** toggle sits
next to the severity selector, off by default. Click it.

For a few seconds it reads "Reading live rain…" — a full live scenario is
being built: real Open-Meteo rainfall run through the same SCS-CN → HAND →
Manning physics as every other assessment. When it lands, **everything on
the screen changes together**: the flood-depth layer on the map, the ranked
red zones, the action cards, the time slider, and the provenance badge, which
now reads the real `rainfall_source`. Population, built-up land, cropland,
roads and facilities were already real either way — the toggle only changes
which rainfall drives the physics, and it changes it everywhere at once.

That consistency is the point, and it took a real fix to get: before tonight
only the ranked-zone panel understood `live=true` — the map would have kept
showing the synthetic storm while the list beside it claimed to be live, a
worse failure than either being wrong alone. Every endpoint the screen calls
now shares one fallback rule
(`test_live_fallback_is_consistent_across_every_district_endpoint`), so a
judge comparing the map to the numbers sees one answer, not two.

If the live feed is genuinely unreachable, the toggle turns amber, not green,
and says so — *"Live feed unreachable — showing the design storm instead"* —
rather than quietly serving synthetic data under a live badge.

## 3. There is not one API key in this codebase

```bash
grep -ri "api_key\|apikey\|api key" backend/app -r
```

Every hit is a comment reading *"no API key required"* — never a credential.
Say it plainly: *"Every data source behind this application is open. Nobody
had to register for anything."*

## 4. Match it against the source, live, in a second tab

Pick any district centroid and open Open-Meteo's own forecast URL for that
coordinate. The rainfall figure on the card matches, because it came from
there a few minutes ago.

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

- **313 tests passing**, 5 skipped.
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
