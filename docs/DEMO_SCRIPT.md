# Demo script — 6 minutes

Timed walkthrough for the SIH presentation. Rehearse it twice; the second time
without looking at this page.

**Before you start**

```bash
python scripts/bench.py --district BR-DAR
cd backend && python -m pytest -q
```

Both take under a minute and both give you numbers to quote (313 tests passing,
median 1.5 s per district). Leave the app already running at
`http://127.0.0.1:8000` on the **Now** board — never open a laptop in front of
judges and wait for a server to boot.

**Pick your board.** Open **Now**. The date control offers *Live now* and four
replays. Check which one has something on it:

- If live shows districts on **ACT** or **PREPARE**, use live. It is stronger.
- If today is quiet — and most days are — the board says so and offers you the
  Wayanad replay in one click. Take it. Same pipeline, archive inputs, stamped
  `REPLAY` on screen throughout.

**Every replay loads in well under a second, from disk, with no network.** They
are pre-computed by `scripts/bake_demo_boards.py`; a replayed day cannot change,
so that is the same arithmetic rather than a cached guess. The live board warms
itself in the background when the server starts.

Nothing in the replay path touches the internet. If the venue wifi dies mid-demo,
keep going — see *If the network dies* below.

Zoom the browser to 100 %. Close every other tab.

---

## 0:00 — 0:50 · The board ← *open here*

You are on **Now**. Do not touch anything yet.

> *"This is every district we model, scored on live weather this morning.*
>
> *Seven need attention today. Two need action. Between them, 87 lakh people
> already live inside a red zone we have mapped."*

Point at the top card.

> *"Puri. The reason is not that it is raining — it is barely raining yet. It is
> the shape of the sky.*
>
> *Cloud is filled at all three levels, and the high cloud is 30 points ahead of
> the low cloud. That is an anvil: cirrus blowing off the top of a storm that has
> already topped out. The column is 45 points deeper than normal for Puri in this
> week of the year — we hold a five-year baseline per district, so we know what
> normal looks like here.*
>
> *And the soil is at 96 %. There is nowhere left for the water to go."*

Then the line that matters:

> *"None of that is machine learning. Every one of those is a published
> threshold you can check. We are not asking you to believe a model — we are
> showing you the sky and the arithmetic."*

---

## 0:50 — 1:20 · Why this district and not the weather app

> *"A weather app would rank these by rainfall. We rank them by rainfall
> **times who is standing underneath it**.*
>
> *Rain over empty ground is weather. The same rain over Puri, where eight lakh
> people live inside a modelled red zone, is an emergency. That is the whole
> difference between a forecast and a decision."*

Click the Puri card. You land in **District → Red zones**.

---

## 1:20 — 1:50 · The problem

> *"It is two in the morning in Darbhanga. It has rained for three days and the
> Bagmati is rising. The District Magistrate has to decide three things right
> now: which villages to evacuate first, where to send the twelve boats he has,
> and which roads still carry a truck.*
>
> *Today he finds out by telephone. Someone rings the block office, the block
> office rings a village head. By the time a picture assembles, the water has
> moved.*
>
> *Meanwhile satellites pass over that exact spot every few days and record all
> of it. That data is free. It is almost entirely unused at district level,
> because it arrives as a raster file for a remote-sensing analyst, not as an
> answer for the person holding the phone."*

**Do not touch the screen yet.** Let them read the map.

---

## 1:50 — 2:20 · Why radar

> *"A flood happens under storm cloud, and an optical satellite sees only the
> cloud. That is why most flood imagery arrives days late.*
>
> *Sentinel-1 is radar. Microwaves go through weather and work at night. And
> water is smooth, so it bounces the pulse away from the satellite instead of
> back — water is black in a radar image. That is the whole detection
> principle."*

Point at the **provenance badge**, top right.

> *"That badge is permanent and it currently says DEMO DATA. The algorithms are
> real and cited — Otsu, Lee, HAND, Manning, SCS Curve Number — but the input
> rasters in this build are modelled, not observed. I would rather tell you that
> than have you find it."*

⚠️ **Say this line. Do not skip it.** It buys you every subsequent claim.

Then point at the end of the sub-line under the district name:

> *"That is live, though. `LAST SENTINEL-1 PASS: 3.4 DAYS AGO` — that number
> came from the Copernicus catalogue just now, over this district's actual
> bounding box. Six real granules in the last fortnight. It also happens to be
> the honest answer to the revisit question, measured rather than claimed."*

*(If the venue has no network this line simply does not appear — the dashboard
is unaffected. Check before you promise it.)*

---

## 2:20 — 3:05 · The time slider ← *the moment they remember*

Press **space**, or hit play.

> *"This is the event running. Ninety-six hours. Watch the water come up the
> tributaries first, then the trunk."*

Let it run to the peak, then press space again.

> *"Peak at hour forty. Nineteen point seven lakh people affected — forty per
> cent of the district. Eleven settlements cut off."*

Point at the sparkline.

> *"The bars behind the slider are rainfall. The curve is flooded area. You can
> see the lag between them — that is the catchment response time, and it is
> what gives you your warning window."*

---

## 3:05 — 4:00 · The ranked list ← *the differentiator*

> *"Everything so far, ISRO's Bhuvan already does, and does well. Here is what
> it does not do."*

Click **zone 1**.

> *"Six kilometres west of Darbhanga. Four point nine eight lakh people, water
> to three metres, road cut. And then — what to actually do about it."*

Scroll the action card slowly. Land on three things:

1. **The actions.** *"Every one of these cites an NDMA SOP. Not our opinion — the
   national guideline, named, so a Magistrate can justify the order afterwards."*
2. **The escalation box.** *"It needs a thousand boats. A district holds about
   thirty. That is not a silly number — it is the finding. It says call the
   State EOC tonight, not tomorrow after the first rescue attempt fails."*
3. **Why this rank.** *"The score is a weighted overlay and every component is
   published. You can disagree with the weights. You cannot disagree with a
   black box."*

Then the drafted SMS at the bottom.

> *"Two hundred and fourteen characters. Ready to send."*

---

## 4:00 — 4:30 · Waterlogging

Click **Planning**.

> *"Different problem, different remedy. River flooding leaves when the river
> falls. Waterlogging is rain that lands where there is nowhere to drain to, and
> it persists for weeks after the river is back in bank. It is what keeps relief
> camps open, and it is what drowns Chennai.*
>
> *This is computed from terrain — wetness index, height above drainage,
> imperviousness, drainage density. Which means we can compute it **before it
> rains**. This is a standing planning asset: it tells a district where to spend
> its drainage budget."*

Point at a hotspot's drain-down figure and its **why**.

> *"Four point eight days to clear, and it says why: low-lying, no fall to any
> channel."*

---

## 4:30 — 5:30 · Scale ← *the honesty moment*

Click **India → All districts**.

Click **National**, then **All 735 districts**.

> *"Every district in India is on this screen. They are not all known to the
> same depth, and we will not pretend otherwise.*
>
> *Twenty-two are modelled — real 30 m Copernicus elevation, flow routing, HAND,
> the BIS landslide rating, exposure, relocation caseload.*
>
> *The other 713 are screening only: live weather, no terrain analysis. That tag
> is on every single row.*
>
> *Screening tells a State Authority where to look. It is not a hazard
> assessment, and we never draw it as one."*

Point at a `screening` chip, then a `modelled` chip.

> *"Promoting a district from one to the other is a data task, not a code
> change. Bake its elevation and it moves tier.*
>
> *And if the network drops, those 713 report no score at all — not zero.
> A zero would read as a national all-clear. We would rather say we do not
> know."*

That last sentence is the one judges remember. It is also true; the test that
enforces it is `test_offline_screening_does_not_report_an_all_clear`.

> *"A district solves in one and a half seconds on this laptop. No GPU, no GDAL,
> no build step. It runs on a district NIC server, or on a laptop in a field
> operations centre with the network unplugged — which is exactly when it is
> needed."*

---

## 5:30 — 6:00 · Close

> *"Bhuvan gives an analyst a map. We give a District Magistrate a ranked task
> list.*
>
> *Their output is a map. Ours is a decision."*

Stop talking.

---

## If the network dies

**Nothing happens, and this is worth doing on purpose.** Leaflet, the fonts and
the basemap are served locally; the relief map is rendered from our own elevation
model rather than a tile server; the terrain, the disaster register, the cyclone
tracks, the district boundaries, the cloud baselines and all four replay boards
are baked into the build.

**Unplug the wifi before you start.** Then, when someone asks about offline
operation, the answer is that it has been disconnected the whole time.

The only thing that needs the internet is the *live* board, and when it cannot
reach Open-Meteo it says so plainly rather than scoring every district zero —
because a board of zeroes reads as a national all-clear. Switch to a replay and
carry on.

There is a test for exactly this: `test_replay_boards_are_baked_and_load_without_
a_network` makes every network call raise, then serves all four boards.

## If the app will not start

```bash
cd backend && python -m uvicorn app.main:app --port 8000
```

Fallback in order: (1) another port, (2) `python scripts/bench.py` prints real
numbers with no server at all, (3) `docs/pitch.html` opens in any browser and
has the interactive HAND demonstration in it.

---

## Questions you will get

**"Is this real satellite data?"**
> Layer by layer, and the badge on screen says which is which. **Elevation is
> real** — Copernicus DEM GLO-30 at 30 m, baked for all 22 modelled districts,
> and every hazard output derives from it. **Built-up surface is real** — GHSL,
> differenced 2015 to 2025. **Rainfall, soil moisture and convective energy are
> live** right now. The *catalogue query* is live: those Sentinel-1 granule times
> came from Copernicus just now. What is still modelled is the **backscatter
> imagery itself** — downloading and processing a granule is the one thing that
> needs credentials rather than code.

**"Prove it's live, don't just tell me."**
> Two ways, both live, both take under thirty seconds. On **Now**, press
> **Refresh live** — it bypasses every cache, top to bottom, and rebuilds the
> board against Open-Meteo right now. Watch the timestamp reset. Or on
> **District → Inundation & relief**, flip the **Live rainfall** toggle — the
> whole screen rebuilds on real observed rainfall: the map, the ranked zones,
> the action cards, the time slider, all together, because they now share one
> rule about what "live" means rather than five different guesses.

**"Bhuvan already does this."**
> Bhuvan gives an analyst a flood extent raster. We give a District Magistrate a
> ranked task list with shelter allocation and supply quantities. We consume
> Bhuvan; we do not duplicate it.

**"Six-day revisit makes your data stale."**
> For flash floods, correct, and we say so. For the riverine flooding that
> displaces millions in Assam and Bihar, events last weeks — Assam 2020 ran six.
> Nobody's problem was not knowing a flood was coming. It was not knowing which
> of 2,100 villages were under water that morning. That is a mapping problem and
> a two-to-six day revisit solves it.

**"How accurate is it?"**
> Three questions, and the third is the honest one. **The detector**, measured on
> real terrain across all 22 districts: on the plains and coasts where this is
> aimed, precision 0.84, recall 0.75. In steep mountain districts recall falls to
> 0.18 — C-band radar is a poor flood sensor there, and we report that number
> rather than averaging it away. **The red zones** rank the places disasters
> actually happened at a mean susceptibility percentile of 66.6 against a chance
> baseline of 50. **The flood model against a gauged historic event: still not
> hindcast.** That is on four observed events, which is too few to call
> validated, and `scripts/validate_hindcast.py` says exactly which two files it
> needs. I am not going to give you a number I cannot stand behind.

**"Is the cloud thing just a rainfall forecast with extra steps?"**
> No, and it is deliberately not. The rainfall number on that card comes from
> Open-Meteo's weather model and is labelled as theirs. The cloud reading is a
> separate statement about column structure — how many levels are filled, whether
> there is an anvil, how fast it is deepening, and how that compares with five
> years of the same week at that exact location. We never blend the two into one
> invented number. They can disagree, and when they do that is information.

**"What if it is a quiet day when we present?"**
> Then the board says so, and I will show you a replay instead — the same
> pipeline against the archive for 30 July 2024, the morning of the Wayanad
> landslide. It is stamped REPLAY everywhere it appears. A board that invents a
> storm to look impressive is a board you could never trust on a real morning.

**"Where is the AI?"**
> Deliberately not in the decision path. An evacuation order has to survive an
> inquiry. Physics is auditable; a network activation is not. ML belongs on the
> water/land threshold and on learning cloud-to-flood patterns from past events.

**"You only really model 22 districts."**
> Correct, and that is on the screen rather than buried. All 735 are addressed;
> 22 carry the full physics and 713 carry live weather screening, and every row
> is tagged with which it is. Screening says where to look. Promotion is a data
> task — bake a district's elevation and it moves tier — so the limit is disk and
> bandwidth, not architecture. What I will not do is draw a screening score in
> the same colour as a modelled one.

**"What would you do with six more months?"**
> Wire the live Copernicus and CWC connectors, digitise embankments — the largest
> single source of error in the extent — add GSI lithology to complete the BIS
> rating, and promote districts from screening to modelled state by state,
> starting with Assam and Bihar.
