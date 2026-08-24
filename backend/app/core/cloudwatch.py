"""Cloud-pattern reading: what the sky is doing, and whether that is unusual.

**There is no machine learning here, and that is deliberate.** A model that
predicts rainfall is a model that has to be believed. This reads the *structure
of the cloud column* against textbook convective meteorology and reports what it
sees, so a District Magistrate — or a judge — can check every step.

Cloud cover on its own is close to useless: it says the sky is grey, not that it
is about to collapse. What distinguishes a wet afternoon from Kedarnath 2013 is
the **vertical structure** of the column and the **rate at which it is
deepening**:

``cloud_cover_low`` / ``mid`` / ``high``
    Cover at three levels. Deep convection fills all three at once — a
    troposphere-spanning column. Stratiform monsoon rain sits in the low and mid
    levels and leaves the high level comparatively clear.
``cloud_cover_high`` >> ``cloud_cover_low``
    The **anvil signature**. Cirrus outflow spreading from the top of a mature
    cumulonimbus, the visual signature IMD reads off INSAT as a cold-cloud
    shield.
``cape``
    How much energy the atmosphere will release if the column is triggered.
    Structure without CAPE is cloud; structure with CAPE is a storm.
``freezing_level_height``
    A high freezing level means a deep warm-cloud layer, which is the classic
    precondition for the high rain rates behind Himalayan cloudbursts.

From these come four **states**, in escalating order: ``quiet``, ``building``,
``deep convection``, ``mature system``. Each is a stated rule over stated
numbers, listed in :data:`STATE_RULES`, and each reports the values that put it
there.

**The anomaly is the point.** A deep column over Cherrapunji in July is a
Tuesday; the same column over Jaisalmer is not. Every reading is placed against
that location's own 5-year record for the same calendar week, so the screen does
not fire on every day of the monsoon and get ignored by June.

What this is not: a rainfall forecast. It says the sky is organising and how
fast. Open-Meteo's own precipitation forecast is reported alongside it, from the
weather model, and is never blended with this into a single invented number.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

CLIMATOLOGY = os.environ.get("DRISHTI_CLOUD_CLIMATOLOGY") or os.path.join(
    os.path.dirname(__file__), "..", "data", "climatology", "cloud_norms.json")

# Hourly fields the cloud read needs. Wider than HOURLY_SCREEN, which is
# deliberately thin for the 532-square national sweep; here it runs over 22
# district centroids, so the extra columns cost one request, not four hundred.
HOURLY_CLOUD = ("precipitation", "cloud_cover", "cloud_cover_low",
                "cloud_cover_mid", "cloud_cover_high", "cape",
                "freezing_level_height",
                # Soil moisture belongs here even though it is not a cloud
                # variable: leaving it out made every saturation reading 0.0,
                # which is not a missing signal but a wrong one - it says the
                # ground is bone dry and can absorb anything.
                "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm")

# Deep convection needs the column filled at every level. 55% is the threshold
# below which a level is not meaningfully overcast.
LEVEL_FILLED = 55.0

# Anvil: high cloud running well ahead of low cloud, in percentage points.
ANVIL_LEAD = 25.0

# CAPE, J/kg. Standard convective-instability bands.
CAPE_MODERATE, CAPE_STRONG = 1000.0, 2500.0

# Freezing level, m. Above this the warm-cloud layer is deep enough to support
# the collision-coalescence rain rates behind Indian cloudbursts.
WARM_CLOUD_M = 4500.0

STATE_RULES = {
    "quiet": "No filled cloud level, or CAPE below 1000 J/kg.",
    "building": ("Column filling and CAPE above 1000 J/kg — instability "
                 "present, convection not yet organised."),
    "deep convection": ("All three levels filled with CAPE above 2500 J/kg — "
                        "a troposphere-spanning convective column."),
    "mature system": ("Deep convection plus an anvil signature: high cloud "
                      "running at least 25 points ahead of low cloud, which is "
                      "cirrus outflow from a storm that has already topped out."),
}

STATE_ORDER = ("quiet", "building", "deep convection", "mature system")

# The same four states, justified without CAPE. Used when replaying a historical
# day: the archive has no CAPE column, and quoting "CAPE above 2500 J/kg" as the
# reason would describe a test that was never run.
STATE_RULES_NO_CAPE = {
    "quiet": "No cloud level meaningfully filled. CAPE unavailable for this date.",
    "building": ("At least one cloud level filled. CAPE unavailable for this "
                 "date, so this is structure alone."),
    "deep convection": ("All three levels filled - a troposphere-spanning "
                        "column. CAPE unavailable for this date, so instability "
                        "is not confirmed."),
    "mature system": ("All three levels filled with an anvil signature: high "
                      "cloud at least 25 points ahead of low cloud. CAPE "
                      "unavailable for this date."),
}


def _norm_key(lat: float, lon: float) -> str:
    return "%.2f,%.2f" % (lat, lon)


def _week_of_year(ts: Optional[float] = None) -> int:
    return int(time.strftime("%W", time.gmtime(ts if ts else time.time())))


def load_norms() -> Dict[str, dict]:
    """Baked 5-year cloud and rainfall norms, or {} if not baked.

    Baked rather than fetched live because a five-year normal does not change
    between refreshes, and 22 archive calls every cycle would burn the rate
    limit that the live forecast actually needs.
    """
    try:
        with open(CLIMATOLOGY, encoding="utf-8") as fh:
            return json.load(fh).get("locations", {})
    except Exception:
        return {}


@dataclass
class CloudRead:
    """What the cloud column is doing at one place, now and over the horizon."""
    lat: float
    lon: float
    state: str
    state_rule: str
    escalation: int                     # index into STATE_ORDER

    cloud_low: float
    cloud_mid: float
    cloud_high: float
    cape: float
    freezing_level_m: float

    levels_filled: int
    anvil: bool
    warm_cloud_deep: bool

    deepening_pct_per_6h: float
    hours_to_peak: Optional[int]
    peak_state: str

    cape_available: bool = True
    anomaly_available: bool = False
    column_depth: float = 0.0
    column_depth_norm: float = 0.0
    depth_above_norm: float = 0.0
    high_cloud_norm: float = 0.0
    anomaly_note: str = ""
    observations: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "state_rule": self.state_rule,
            "escalation": self.escalation,
            "column": {
                "low_pct": round(self.cloud_low, 0),
                "mid_pct": round(self.cloud_mid, 0),
                "high_pct": round(self.cloud_high, 0),
                "levels_filled": self.levels_filled,
                "anvil_signature": self.anvil,
            },
            "cape_j_kg": round(self.cape, 0) if self.cape_available else None,
            "cape_available": self.cape_available,
            "freezing_level_m": round(self.freezing_level_m, 0),
            "warm_cloud_layer_deep": self.warm_cloud_deep,
            "deepening_pct_per_6h": round(self.deepening_pct_per_6h, 1),
            "hours_to_peak": self.hours_to_peak,
            "peak_state": self.peak_state,
            "anomaly": {
                "available": self.anomaly_available,
                "column_depth_pct": round(self.column_depth, 0),
                "column_depth_normal_pct": round(self.column_depth_norm, 0),
                "points_above_normal": round(self.depth_above_norm, 0),
                "high_cloud_normal_pct": round(self.high_cloud_norm, 0),
                "note": self.anomaly_note,
                "basis": ("Five-year hourly cloud cover from the ERA5 archive, "
                          "averaged for this calendar week at this location. "
                          "CAPE has no archive baseline and is judged against "
                          "absolute instability bands instead."),
            },
            "observations": self.observations,
            "method": ("Vertical cloud structure and convective energy read "
                       "against textbook convective meteorology. No machine "
                       "learning, and not a rainfall forecast."),
        }


def _classify(low: float, mid: float, high: float, cape: float,
              cape_known: bool = True) -> Tuple[str, int, int, bool]:
    """The four states, from stated rules over stated numbers.

    ``cape_known`` is False when replaying a historical day: the ERA5 archive
    does not serve CAPE, and treating a missing value as zero would classify a
    sky filled to 100% at every level as "quiet" - which is how the day Wayanad
    went would read as calm. Without CAPE the column is classified on its
    structure alone, and :attr:`CloudRead.cape_available` says so.
    """
    filled = sum(1 for v in (low, mid, high) if v >= LEVEL_FILLED)
    anvil = (high - low) >= ANVIL_LEAD and high >= LEVEL_FILLED

    if not cape_known:
        if filled >= 3:
            state = "mature system" if anvil else "deep convection"
        elif filled >= 1:
            state = "building"
        else:
            state = "quiet"
        return state, STATE_ORDER.index(state), filled, anvil

    if filled == 0 or cape < CAPE_MODERATE:
        state = "quiet"
    elif filled >= 3 and cape >= CAPE_STRONG:
        state = "mature system" if anvil else "deep convection"
    else:
        state = "building"
    return state, STATE_ORDER.index(state), filled, anvil


def _series(w, name: str) -> np.ndarray:
    arr = getattr(w, name, None)
    if arr is None:
        return np.zeros(0, np.float32)
    return np.nan_to_num(np.asarray(arr, np.float32))


def _has_values(w, name: str) -> bool:
    """Whether a column carries real numbers rather than all-NaN padding.

    The archive returns CAPE as a full column of NaN rather than as an error, so
    "the value is zero" and "there is no value" are only distinguishable here.
    """
    arr = getattr(w, name, None)
    if arr is None or not len(arr):
        return False
    a = np.asarray(arr, np.float32)
    return bool(np.isfinite(a).any() and np.nanmax(np.abs(a)) > 0)


def read(w, norms: Optional[dict] = None) -> CloudRead:
    """Read one location's cloud column from an hourly weather record.

    ``w`` is a :class:`~app.providers.openmeteo.PointWeather` carrying the
    :data:`HOURLY_CLOUD` fields. Missing fields degrade to zero rather than
    raising: a cloud read that fails must leave the rest of the screen standing.
    """
    now = int(getattr(w, "past_hours", 0))
    low = _series(w, "cloud_low")
    mid = _series(w, "cloud_mid")
    high = _series(w, "cloud_high")
    cape = _series(w, "cape")
    frz = _series(w, "freezing_level")

    def at(arr, i, default=0.0):
        if not arr.size:
            return default
        return float(arr[min(max(i, 0), arr.size - 1)])

    l_now, m_now, h_now = at(low, now), at(mid, now), at(high, now)
    c_now, f_now = at(cape, now), at(frz, now, 4000.0)

    cape_known = _has_values(w, "cape")
    state, esc, filled, anvil = _classify(l_now, m_now, h_now, c_now, cape_known)

    # How fast the column is deepening: mean total cover over the last six hours
    # against the six before it. A snapshot cannot distinguish a column that is
    # collapsing from one that is exploding, and the difference is the whole
    # question.
    def window_mean(arr, a, b):
        if not arr.size:
            return 0.0
        a, b = max(a, 0), min(b, arr.size)
        return float(arr[a:b].mean()) if b > a else 0.0

    depth_now = (window_mean(low, now - 6, now) + window_mean(mid, now - 6, now)
                 + window_mean(high, now - 6, now)) / 3.0
    depth_prev = (window_mean(low, now - 12, now - 6)
                  + window_mean(mid, now - 12, now - 6)
                  + window_mean(high, now - 12, now - 6)) / 3.0
    deepening = depth_now - depth_prev

    # Worst state reached over the next 48 hours, and when.
    peak_state, peak_esc, hours_to_peak = state, esc, None
    horizon = min(now + 48, max(low.size, cape.size))
    for i in range(now, horizon):
        s, e, _, _ = _classify(at(low, i), at(mid, i), at(high, i),
                               at(cape, i), cape_known)
        if e > peak_esc:
            peak_state, peak_esc, hours_to_peak = s, e, i - now

    obs: List[str] = []
    if filled >= 3:
        obs.append("cloud filling all three levels (%.0f/%.0f/%.0f%% "
                   "low/mid/high) — a troposphere-deep column"
                   % (l_now, m_now, h_now))
    elif filled:
        obs.append("cloud at %d of 3 levels (%.0f/%.0f/%.0f%%)"
                   % (filled, l_now, m_now, h_now))
    if anvil:
        obs.append("anvil signature — high cloud %.0f points above low, "
                   "cirrus outflow from a topped-out storm" % (h_now - l_now))
    if not cape_known:
        obs.append("CAPE unavailable for this date — column classified on "
                   "structure alone")
    elif c_now >= CAPE_STRONG:
        obs.append("CAPE %.0f J/kg — strong instability" % c_now)
    elif c_now >= CAPE_MODERATE:
        obs.append("CAPE %.0f J/kg — moderate instability" % c_now)
    if f_now >= WARM_CLOUD_M:
        obs.append("freezing level %.0f m — deep warm-cloud layer, the "
                   "cloudburst precondition" % f_now)
    if deepening >= 8.0:
        obs.append("column deepened %.0f points in six hours — organising now"
                   % deepening)
    elif deepening <= -8.0:
        obs.append("column thinning %.0f points in six hours — system decaying"
                   % abs(deepening))
    if hours_to_peak is not None:
        obs.append("escalates to %s in about %d hours"
                   % (peak_state, hours_to_peak))

    rules = STATE_RULES if cape_known else STATE_RULES_NO_CAPE
    out = CloudRead(
        lat=float(getattr(w, "lat", 0.0)), lon=float(getattr(w, "lon", 0.0)),
        state=state, state_rule=rules[state], escalation=esc,
        cloud_low=l_now, cloud_mid=m_now, cloud_high=h_now,
        cape=c_now, freezing_level_m=f_now, cape_available=cape_known,
        levels_filled=filled, anvil=anvil,
        warm_cloud_deep=f_now >= WARM_CLOUD_M,
        deepening_pct_per_6h=deepening,
        hours_to_peak=hours_to_peak, peak_state=peak_state,
        observations=obs)

    if norms:
        _attach_anomaly(out, norms)
    return out


# Percentage points of column depth above the local weekly norm at which the
# column is worth calling unusual. Reported in points rather than as a ratio:
# cloud cover is bounded at 100, so "twice normal" is a meaningless statement
# about a variable that cannot exceed 100 no matter what the sky does.
DEPTH_ANOMALY_PTS = 20.0


def _attach_anomaly(r: CloudRead, norms: dict) -> None:
    """Place the reading against this location's own record for this week.

    Without this the screen fires on every day of the monsoon, and a screen that
    fires every day is a screen nobody reads by July.
    """
    wk = str(_week_of_year())
    weekly = (norms.get("weekly") or {}).get(wk)
    if not weekly:
        r.anomaly_note = "no baked norm for this week at this location"
        return

    r.column_depth = (r.cloud_low + r.cloud_mid + r.cloud_high) / 3.0
    r.column_depth_norm = float(weekly.get("column_depth_mean", 0.0))
    r.high_cloud_norm = float(weekly.get("cloud_high_mean", 0.0))
    if r.column_depth_norm <= 0:
        r.anomaly_note = "no cloud baseline for this week at this location"
        return

    r.anomaly_available = True
    r.depth_above_norm = r.column_depth - r.column_depth_norm

    if r.depth_above_norm >= DEPTH_ANOMALY_PTS:
        r.anomaly_note = ("cloud column %.0f points deeper than normal for this "
                          "week here (%.0f%% against %.0f%%)"
                          % (r.depth_above_norm, r.column_depth,
                             r.column_depth_norm))
        r.observations.append(r.anomaly_note)
    elif r.depth_above_norm >= 8:
        r.anomaly_note = ("cloud column modestly above the local norm for this "
                          "week (%.0f%% against %.0f%%)"
                          % (r.column_depth, r.column_depth_norm))
    else:
        r.anomaly_note = ("normal for this location and week (%.0f%% against "
                          "%.0f%%)" % (r.column_depth, r.column_depth_norm))


def read_many(weather: Sequence, norms_by_key: Optional[Dict[str, dict]] = None
              ) -> List[CloudRead]:
    norms_by_key = norms_by_key or {}
    out = []
    for w in weather:
        key = _norm_key(getattr(w, "lat", 0.0), getattr(w, "lon", 0.0))
        out.append(read(w, norms_by_key.get(key)))
    return out
