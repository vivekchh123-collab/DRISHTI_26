"""Which districts need attention today, and exactly why.

This is the screen the whole system exists to produce. Everything else — the
terrain, the red zones, the relocation plan — answers *where is dangerous*. This
answers **where is dangerous right now**, which is the question a control room
actually asks at seven in the morning.

Four live signals, and one standing one. The standing one is the point.

* **Cloud pattern** — the vertical structure of the cloud column, read against
  textbook convective meteorology in :mod:`app.core.cloudwatch`. Not a rainfall
  forecast and not machine learning: it says the sky is organising, how deep the
  column is, how fast it is deepening, and whether that is unusual *here*.
* **Rainfall** — Open-Meteo's own forecast, placed against the India
  Meteorological Department's warning bands and against this location's own
  five-year norm for this calendar week.
* **Soil saturation** — whether the ground can still absorb anything, which is
  what separates a wet week from Kerala 2018.
* **River response** — stage against bankfull, routed from live rainfall through
  the district's real 30 m terrain. **This is modelled, not gauged**, and every
  response says so: there is no public CWC feed, and a modelled stage presented
  as a gauge reading would be a lie with a decimal point on it.
* **Standing exposure** — how many people already live inside that district's
  red zones.

The last one is what makes this different from a weather app. Rain over empty
ground is weather. The same rain over a district where three lakh people live
inside a modelled red zone is an emergency, and the ranking says so.

**Cost discipline.** The cloud and rainfall read is one batched request over 22
district centroids and takes a couple of seconds. The river response costs a
full hydrological simulation, so it is spent only on districts the cheap signals
have already flagged — the same principle as the national grid, one level down.

Scope is the 22 modelled districts, deliberately. A river response needs a DEM,
and claiming a stage rise for a district whose terrain we have never loaded
would be exactly the kind of confident nonsense the rest of this system is built
to avoid. Screening districts are served by
:mod:`app.core.national_districts`, which says plainly that it is weather only.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..data import districts as districts_data
from . import cloudwatch

BOARDS = os.environ.get("DRISHTI_BOARDS") or os.path.join(
    os.path.dirname(__file__), "..", "data", "boards")

CACHE_TTL = 900.0            # 15 minutes; the forecast itself updates hourly
_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, "WatchBoard"]] = {}

# Past/forecast window. Five past days give the cloud read something to measure
# a deepening rate against; three forecast days cover the horizon a district
# authority can actually act on.
PAST_DAYS, FORECAST_DAYS = 5, 3

# Urgency above which the expensive river simulation is worth running.
RIVER_THRESHOLD = 35.0

# Urgency above which it is worth loading a district's standing red-zone
# exposure. Every one of those is a full assessment, and doing all 22 on a cold
# process took two and a half minutes - far too slow for the first screen anyone
# sees. Below this threshold the district is not going on the board anyway, so
# the exposure weighting cannot change the outcome.
EXPOSURE_THRESHOLD = 18.0

# Action bands. Chosen to map onto what a District Magistrate already does
# rather than onto a colour scheme invented here.
ACT, PREPARE, WATCH, ROUTINE = "ACT", "PREPARE", "WATCH", "ROUTINE"

ACTION_MEANING = {
    ACT: ("Move on this today. Live signals are severe over land already known "
          "to be dangerous."),
    PREPARE: ("Stage resources and warn the blocks named. The signal is real "
              "but the lead time is usable."),
    WATCH: "Worth a look at the next refresh. Nothing to move yet.",
    ROUTINE: "Nothing unusual over this district.",
}


@dataclass
class DistrictWatch:
    code: str
    name: str
    state: str
    lat: float
    lon: float

    urgency: float
    action: str
    lead_time_hours: Optional[int]

    cloud: dict
    rain: dict
    soil: dict
    river: dict
    exposure: dict
    why: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "state": self.state,
            "lat": self.lat, "lon": self.lon,
            "urgency": round(self.urgency, 1),
            "action": self.action,
            "action_meaning": ACTION_MEANING[self.action],
            "lead_time_hours": self.lead_time_hours,
            "lead_time_note": (
                "already at its peak state" if self.lead_time_hours is None
                else "escalates in about %d hours" % self.lead_time_hours),
            "cloud": self.cloud,
            "rainfall": self.rain,
            "soil": self.soil,
            "river": self.river,
            "exposure": self.exposure,
            "why": self.why,
        }


@dataclass
class WatchBoard:
    districts: List[DistrictWatch]
    live: bool
    generated_at: str
    seconds: float
    source: str
    river_runs: int
    replay_date: Optional[str] = None
    reason: str = ""

    @property
    def needing_attention(self) -> List[DistrictWatch]:
        return [d for d in self.districts if d.action in (ACT, PREPARE)]

    def to_dict(self) -> dict:
        need = self.needing_attention
        return {
            "live": self.live,
            "mode": "replay" if self.replay_date else "live",
            "replay_date": self.replay_date,
            "generated_at": self.generated_at,
            "build_seconds": round(self.seconds, 1),
            "source": self.source,
            "reason": self.reason,
            "counts": {
                "assessed": len(self.districts),
                "needing_attention": len(need),
                "act": sum(1 for d in self.districts if d.action == ACT),
                "prepare": sum(1 for d in self.districts if d.action == PREPARE),
                "watch": sum(1 for d in self.districts if d.action == WATCH),
                "river_simulations": self.river_runs,
            },
            "headline": self.headline(),
            "population_at_stake": sum(
                d.exposure.get("population_in_red_zone", 0) for d in need),
            "method": (
                "Cloud structure read against convective meteorology; rainfall "
                "from the Open-Meteo forecast against IMD warning bands; river "
                "stage modelled from live rainfall through real 30 m terrain. "
                "No machine learning in this path, and no gauge readings - the "
                "river figure is modelled and labelled as such."),
            "districts": [d.to_dict() for d in self.districts],
        }

    def headline(self) -> str:
        if self.replay_date and self.live:
            need = self.needing_attention
            if not need:
                return ("Replay of %s - not live. Nothing was on the board "
                        "that day." % self.replay_date)
            people = sum(d.exposure.get("population_in_red_zone", 0)
                         for d in need)
            return ("Replay of %s - not live. %d district%s needed attention "
                    "that day, covering %s people living inside a modelled red "
                    "zone." % (self.replay_date, len(need),
                               "" if len(need) == 1 else "s",
                               "{:,}".format(people)))
        if not self.live:
            return ("No live weather connection. This board cannot say what the "
                    "sky is doing, and does not guess.")
        need = self.needing_attention
        if not need:
            # Saying "nothing on the board" above a list of watch-level rows is
            # the sort of small contradiction that costs more credibility than
            # it saves words.
            watching = sum(1 for d in self.districts if d.action == WATCH)
            if watching:
                return ("Nothing needs acting on today. %d district%s worth "
                        "watching at the next refresh - which is the normal "
                        "state, and is what makes the exceptions worth reading."
                        % (watching, "" if watching == 1 else "s"))
            return ("Nothing on the board. No modelled district is showing a "
                    "live signal worth acting on - which is the normal state, "
                    "and is what makes the exceptions worth reading.")
        people = sum(d.exposure.get("population_in_red_zone", 0) for d in need)
        lead = [d.lead_time_hours for d in need if d.lead_time_hours is not None]
        tail = (" Earliest escalation in about %d hours." % min(lead)) if lead else ""
        return ("%d district%s need attention. %s people in those districts "
                "already live inside a modelled red zone.%s"
                % (len(need), "" if len(need) == 1 else "s",
                   "{:,}".format(people), tail))


def _rain_read(w, norms: Optional[dict]) -> dict:
    """Forecast rainfall against IMD bands and against this week's local norm."""
    from .national import rainfall_band

    next24 = float(getattr(w, "rain_next_24h", 0.0))
    next72 = float(getattr(w, "rain_next_72h", 0.0))
    past24 = float(getattr(w, "rain_24h", 0.0))
    peak = float(getattr(w, "peak_hourly_next_24h", 0.0))
    band, colour = rainfall_band(max(next24, past24))

    out = {
        "next_24h_mm": round(next24, 1),
        "next_72h_mm": round(next72, 1),
        "past_24h_mm": round(past24, 1),
        "peak_hourly_mm": round(peak, 1),
        "imd_band": band,
        "imd_colour": colour,
        "source": "Open-Meteo forecast model; bands from IMD warning categories",
        "vs_normal": None,
    }

    wk = str(cloudwatch._week_of_year())
    weekly = ((norms or {}).get("weekly") or {}).get(wk) if norms else None
    if weekly:
        # The archive gives a weekly total; compare like with like.
        norm_week = float(weekly.get("rain_weekly_mean_mm", 0.0))
        if norm_week > 1.0:
            out["vs_normal"] = {
                "local_weekly_norm_mm": round(norm_week, 1),
                "ratio": round(next72 / norm_week, 2),
                "note": ("%.0f mm forecast over 72 hours against a local norm "
                         "of %.0f mm for this week"
                         % (next72, norm_week)),
            }
    return out


def _soil_read(w) -> dict:
    sat = float(getattr(w, "soil_saturation", 0.0) or 0.0)
    if sat >= 0.9:
        reading = "saturated - further rain will run off, not soak in"
    elif sat >= 0.75:
        reading = "wet - little absorption left"
    elif sat >= 0.5:
        reading = "moist"
    else:
        reading = "dry - the catchment can still absorb"
    return {"saturation": round(sat, 3), "reading": reading,
            "antecedent_mm": round(float(getattr(w, "antecedent_index_mm", 0.0) or 0.0), 1)}


def _river_read(code: str) -> dict:
    """Trunk-river stage against bankfull, from live rainfall over real terrain.

    The hydrology is fully spatial - stage is (hours, n, n) and bankfull is
    (n, n) - so "the river level in this district" needs a cell chosen for it.
    That cell is the one with the **largest upstream catchment**: the trunk
    river at the point it carries the most water, which is the reach a district
    authority means when it asks whether the river is rising. Picking the
    maximum stage anywhere on the grid instead would report a puddle on a
    hillside as a flood.

    Expensive, and only ever called for a district the cheap signals already
    flagged. Returns ``available: False`` rather than a number if anything
    fails: a river figure is exactly the sort of thing that must never be
    quietly replaced with a guess.
    """
    from . import scenario as scenario_mod
    try:
        sc = scenario_mod.build_live(code)
    except Exception:
        sc = None
    if sc is None or getattr(sc, "hydrograph", None) is None:
        return {"available": False, "gauged": False,
                "reason": "live rainfall unavailable; no stage modelled"}

    hyd = sc.hydrograph
    stage = np.asarray(hyd.stage, np.float32)
    catch = np.asarray(hyd.catchment_km2, np.float32)
    bank = np.asarray(hyd.bankfull_m, np.float32)
    if stage.ndim != 3 or not catch.size:
        return {"available": False, "gauged": False,
                "reason": "no stage series"}

    j, i = np.unravel_index(int(np.nanargmax(catch)), catch.shape)
    series = np.nan_to_num(stage[:, j, i])
    bankfull = float(bank[j, i])
    now = float(series[0])
    peak = float(np.nanmax(series))
    peak_hour = int(np.nanargmax(series))
    over = peak - bankfull

    if bankfull <= 0:
        reading = "channel geometry unavailable at the trunk reach"
    elif over >= 1.0:
        reading = ("modelled to overtop the bank by %.1f m - out-of-channel "
                   "flooding" % over)
    elif over >= 0:
        reading = "modelled to reach bankfull"
    elif over >= -0.5:
        reading = "modelled to approach bankfull"
    else:
        reading = "modelled to stay well within the channel"

    return {
        "available": True,
        "gauged": False,
        "stage_now_m": round(now, 2),
        "peak_stage_m": round(peak, 2),
        "rise_m": round(peak - now, 2),
        "bankfull_m": round(bankfull, 2),
        "hours_to_peak": peak_hour,
        "catchment_km2": round(float(catch[j, i]), 1),
        "reading": reading,
        "provenance": ("Modelled: live Open-Meteo rainfall routed through "
                       "Copernicus 30 m terrain by SCS-CN runoff and Manning "
                       "compound-channel hydraulics, read at the district's "
                       "largest-catchment reach. NOT a gauge reading - no "
                       "public CWC feed exists. See providers/cwc.py."),
    }


def _exposure_read(code: str) -> dict:
    """The standing red-zone caseload. Cached hard; this does not change hourly."""
    from . import scenario as scenario_mod
    try:
        a = scenario_mod.assessment(code)
    except Exception:
        return {"available": False}
    mask = a.exposure.district_mask
    red = a.redzones.is_red & mask
    pop = int(round(float(a.exposure.population[red].sum()))) if red.any() else 0
    imm = sum(1 for h in a.habitations if h.horizon == "immediate")
    return {
        "available": True,
        "population_in_red_zone": pop,
        "red_zone_km2": round(float(red.sum()) * a.grid.cell_area_km2, 1),
        "immediate_habitations": imm,
        "dominant_hazard": a.redzones.dominant_hazard_name
        if hasattr(a.redzones, "dominant_hazard_name") else None,
    }


def _score(cloud, rain: dict, soil: dict, exposure: dict) -> Tuple[float, List[str]]:
    """Combine the live signals, then weight by who is standing underneath.

    Weights are published here rather than tuned until the demo looked good.
    Rainfall leads because it is the proximate cause; the cloud read is second
    because it is the earlier signal; soil decides how much of the rain becomes
    runoff.
    """
    why: List[str] = []

    rain_t = float(np.clip(rain["next_24h_mm"] / 204.5, 0, 1))
    rain72_t = float(np.clip(rain["next_72h_mm"] / 350.0, 0, 1))
    cloud_t = cloud.escalation / 3.0
    soil_t = float(np.clip(soil["saturation"], 0, 1))
    burst_t = float(np.clip(rain["peak_hourly_mm"] / 50.0, 0, 1))

    score = 100.0 * (0.30 * rain_t + 0.16 * rain72_t + 0.24 * cloud_t
                     + 0.18 * soil_t + 0.12 * burst_t)

    # An anomalous column counts for more than a routine one of the same depth.
    if cloud.anomaly_available and cloud.depth_above_norm >= cloudwatch.DEPTH_ANOMALY_PTS:
        score *= 1.15

    # Rain over empty ground is weather. The same rain over land where people
    # already live inside a red zone is the thing this system exists to catch.
    pop = exposure.get("population_in_red_zone", 0) or 0
    if pop:
        score *= 1.0 + 0.25 * float(np.clip(pop / 1_000_000.0, 0, 1))

    if rain["imd_band"] != "no warning":
        why.append("IMD %s rainfall: %.0f mm forecast in 24 h"
                   % (rain["imd_band"], rain["next_24h_mm"]))
    vs = rain.get("vs_normal")
    if vs and vs["ratio"] >= 1.8:
        why.append("%.1f× the normal rainfall for this week here (%s)"
                   % (vs["ratio"], vs["note"]))
    if cloud.escalation >= 2:
        why.append("cloud column is %s. %s" % (cloud.state, cloud.state_rule))
    if cloud.anomaly_available and cloud.depth_above_norm >= cloudwatch.DEPTH_ANOMALY_PTS:
        why.append(cloud.anomaly_note)
    if cloud.deepening_pct_per_6h >= 8:
        why.append("column deepening %.0f points every six hours"
                   % cloud.deepening_pct_per_6h)
    if soil["saturation"] >= 0.85:
        why.append("soil %s" % soil["reading"])
    if pop >= 100_000:
        why.append("%s people already live inside this district's red zones"
                   % "{:,}".format(pop))

    # Anything that reaches the board must say why it is there. A district can
    # cross the threshold on the weighted sum without any single signal crossing
    # its own narrative threshold, and a score on screen with an empty
    # explanation is the one thing nobody can defend when asked about it.
    if not why:
        terms = [
            ("rainfall", 0.30 * rain_t,
             "%.0f mm forecast in 24 h" % rain["next_24h_mm"]),
            ("cloud", 0.24 * cloud_t,
             "cloud column is %s" % cloud.state),
            ("soil", 0.18 * soil_t,
             "soil at %.0f%% saturation" % (soil["saturation"] * 100)),
            ("rain72", 0.16 * rain72_t,
             "%.0f mm forecast over 72 h" % rain["next_72h_mm"]),
            ("burst", 0.12 * burst_t,
             "peak %.0f mm in a single hour" % rain["peak_hourly_mm"]),
        ]
        _, weight, text = max(terms, key=lambda t: t[1])
        why.append("nothing individually alarming; ranked mainly on %s" % text
                   if weight > 0 else
                   "no single signal is elevated - listed for completeness")

    return float(np.clip(score, 0, 100)), why


def _action(urgency: float, lead: Optional[int]) -> str:
    """Urgency decides whether to act; lead time decides how soon."""
    if urgency >= 60:
        return ACT if (lead is None or lead <= 24) else PREPARE
    if urgency >= 40:
        return PREPARE
    if urgency >= 25:
        return WATCH
    return ROUTINE


def _replay_window(date: str):
    """The archive window around a replayed date, and which hour is "now"."""
    t = time.mktime(time.strptime(date, "%Y-%m-%d"))
    start = time.strftime("%Y-%m-%d", time.gmtime(t - 86400 * PAST_DAYS))
    end = time.strftime("%Y-%m-%d", time.gmtime(t + 86400 * FORECAST_DAYS))
    return start, end, PAST_DAYS * 24


def build(with_river: bool = True, date: Optional[str] = None) -> WatchBoard:
    """Score every modelled district, ranked by urgency.

    ``date`` (YYYY-MM-DD) replays a day that has already happened, through the
    identical pipeline, from the archive. That is worth having for its own sake
    - an after-action review asks exactly this question - and it also means the
    board can be demonstrated on a quiet week without anybody inventing a storm.
    It is labelled ``replay`` on every response and can never read as live.
    """
    from ..providers.openmeteo import OpenMeteoProvider

    t0 = time.time()
    ds = list(districts_data.DISTRICTS)
    norms = cloudwatch.load_norms()
    provider = OpenMeteoProvider()
    points = [(d.lat, d.lon) for d in ds]

    if date:
        start, end, past_hours = _replay_window(date)
        wx = provider.fetch_archive(points, start, end, past_hours,
                                    variables=cloudwatch.HOURLY_CLOUD)
        source = ("Open-Meteo ERA5 archive - REPLAY of %s, not live. "
                  "Identical pipeline, historical inputs." % date)
    else:
        wx = provider.fetch(points, past_days=PAST_DAYS,
                            forecast_days=FORECAST_DAYS,
                            variables=cloudwatch.HOURLY_CLOUD)
        source = ("Open-Meteo live forecast (no API key) - cloud structure, "
                  "CAPE, rainfall and soil moisture")

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not wx or len(wx) != len(ds):
        return WatchBoard(
            districts=[], live=False, generated_at=stamp,
            seconds=time.time() - t0, source="no connection",
            river_runs=0, replay_date=date,
            reason=("Open-Meteo unreachable. This board reports nothing rather "
                    "than scoring every district zero, because a board of "
                    "zeroes reads as an all-clear."))

    # Pass one: weather only. Cheap, and enough to decide who is worth the
    # expensive lookups.
    reads = []
    for d, w in zip(ds, wx):
        norm = norms.get(cloudwatch._norm_key(d.lat, d.lon))
        cloud = cloudwatch.read(w, norm)
        rain = _rain_read(w, norm)
        soil = _soil_read(w)
        base, _ = _score(cloud, rain, soil, {})
        reads.append((d, cloud, rain, soil, base))

    # Pass two: for districts the weather has already flagged, load the standing
    # red-zone exposure and rescore. This is what turns "it will rain hard" into
    # "it will rain hard on 5.6 lakh people who live inside a red zone".
    rows: List[DistrictWatch] = []
    for d, cloud, rain, soil, base in reads:
        exposure = (_exposure_read(d.code) if base >= EXPOSURE_THRESHOLD
                    else {"available": False,
                          "reason": "not loaded - district is not on the board"})
        urgency, why = _score(cloud, rain, soil, exposure)
        lead = cloud.hours_to_peak
        rows.append(DistrictWatch(
            code=d.code, name=d.name, state=d.state, lat=d.lat, lon=d.lon,
            urgency=urgency, action=_action(urgency, lead), lead_time_hours=lead,
            cloud=cloud.to_dict(), rain=rain, soil=soil,
            river={"available": False, "reason": "not run - below threshold",
                   "gauged": False},
            exposure=exposure, why=why))

    rows.sort(key=lambda r: -r.urgency)

    # Spend the expensive physics only where the cheap signals already fired.
    river_runs = 0
    if with_river and not date:
        for r in rows:
            if r.urgency < RIVER_THRESHOLD or river_runs >= 5:
                continue
            r.river = _river_read(r.code)
            river_runs += 1
            if (r.river.get("available")
                    and r.river["peak_stage_m"] > r.river["bankfull_m"]):
                r.why.insert(0, "river %s" % r.river["reading"])

    return WatchBoard(
        districts=rows, live=True, generated_at=stamp,
        seconds=time.time() - t0, source=source,
        replay_date=date, river_runs=river_runs)


def baked(date: str) -> Optional[dict]:
    """A replay board computed ahead of time, or None if it was never baked.

    A cold board costs minutes: twenty-two coordinates of weather, twenty-two
    district assessments behind the exposure figures, and a hydrological
    simulation for anything that fires. A control room can wait; a demonstration
    cannot, and neither can a district office on a bad connection.

    A replayed day cannot change, so this is not a cache that might be stale -
    it is the same arithmetic, written down once. It also needs no network,
    which is what lets the whole replay path run with the cable out.
    """
    try:
        with open(os.path.join(BOARDS, "%s.json" % date), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def baked_dates() -> List[dict]:
    """Which replay dates have a board on disk."""
    try:
        with open(os.path.join(BOARDS, "index.json"), encoding="utf-8") as fh:
            return json.load(fh).get("dates", [])
    except Exception:
        return []


def get(with_river: bool = True, date: Optional[str] = None) -> WatchBoard:
    key = date or "live"
    with _LOCK:
        hit = _CACHE.get(key)
    # A replayed day cannot change, so it never goes stale.
    if hit and (date or (time.time() - hit[0]) < CACHE_TTL):
        return hit[1]
    built = build(with_river, date)
    if built.live:
        with _LOCK:
            _CACHE[key] = (time.time(), built)
    return built
