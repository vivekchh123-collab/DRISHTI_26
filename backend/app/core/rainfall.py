"""Rainfall: design storms, live forecasts, spatial pattern, antecedent wetness.

Two ways to build the same object, and everything downstream consumes it
unchanged — which is exactly why the seam is here.

:func:`build_event` generates a **design storm**: the standard engineering
construct of a rainfall depth for a stated duration and return period,
distributed over time by a hyetograph and over space by an orographic weight.
Depth-duration-frequency values are derived from the district's IMD annual
normal rather than looked up per station, and that approximation is recorded in
docs/ASSUMPTIONS.md.

:func:`build_live_event` builds the same object from **observed and forecast
rainfall**, sampled on a grid of points across the district. The runoff,
routing and flood models cannot tell the difference and do not need to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from .grid import Grid
from .terrain import Terrain, fbm, normalize, smooth

# Return period in years for each named severity. These drive the whole scenario.
SEVERITY: Dict[str, float] = {
    "moderate": 5.0,
    "severe": 25.0,
    "extreme": 100.0,
}


def design_depth_24h(annual_normal_mm: float, return_period_years: float) -> float:
    """24-hour rainfall depth (mm) for a return period.

    The 100-year 24-hour depth is estimated from the annual normal, then scaled
    down for shorter return periods with Gumbel frequency factors. A real
    deployment reads these from IMD's published DDF atlas per station; this
    keeps the same shape so that substitution is a data change, not a code
    change.
    """
    p100 = 0.13 * annual_normal_mm + 120.0

    def gumbel_k(t: float) -> float:
        return -math.sqrt(6.0) / math.pi * (0.5772 + math.log(math.log(t / (t - 1.0))))

    k100, kt = gumbel_k(100.0), gumbel_k(max(return_period_years, 1.05))
    # Express both as a fraction of the 100-year value; cv ~ 0.35 is typical of
    # Indian 1-day annual maximum series.
    cv = 0.35
    ratio = (1.0 + cv * kt) / (1.0 + cv * k100)
    return float(p100 * max(ratio, 0.15))


def hyetograph(hours: int, kind: str, seed: int) -> np.ndarray:
    """Fraction of total depth falling in each hour; sums to 1.

    ``kind`` selects the storm's temporal shape, which matters more than the
    total for flashy catchments: the same 300 mm produces a very different peak
    depending on whether it arrives over six hours or three days.
    """
    t = np.arange(hours, dtype=np.float64)
    rng = np.random.default_rng(seed)

    if kind == "convective":
        # Short, violent, single peak - the Chennai / Mumbai cloudburst case.
        centre = hours * 0.35
        w = np.exp(-0.5 * ((t - centre) / max(hours * 0.09, 1.0)) ** 2)
    elif kind == "orographic":
        # Ghats and Himalaya: sustained high intensity with embedded bursts.
        w = 0.55 + 0.45 * np.sin(2 * math.pi * t / max(hours * 0.42, 1.0)) ** 2
        w *= np.exp(-0.5 * ((t - hours * 0.45) / max(hours * 0.33, 1.0)) ** 2) + 0.35
    else:
        # Monsoon depression: broad, multi-day, twin-peaked.
        w = (np.exp(-0.5 * ((t - hours * 0.30) / max(hours * 0.16, 1.0)) ** 2)
             + 0.85 * np.exp(-0.5 * ((t - hours * 0.66) / max(hours * 0.20, 1.0)) ** 2))

    w = w * (0.85 + 0.3 * rng.random(hours))     # hour-to-hour variability
    w = np.maximum(w, 0.0)
    return (w / max(w.sum(), 1e-9)).astype(np.float64)


def spatial_weight(grid: Grid, terrain: Terrain, seed: int,
                   orographic: bool) -> np.ndarray:
    """Normalised spatial rainfall multiplier, mean 1.0.

    Rain is never uniform over a district. Over hills it is strongly orographic —
    depth rises with elevation, which is why the headwaters flood before the
    valley does. Over plains the pattern is a broad, slowly varying cell.
    """
    base = 0.72 + 0.56 * fbm(grid.n, seed + 5, octaves=3, persistence=0.55)
    if orographic:
        lift = normalize(smooth(terrain.dem, radius=3))
        base = base * (0.62 + 0.95 * lift)
    base = smooth(base, radius=2)
    return (base / max(float(base.mean()), 1e-9)).astype(np.float32)


@dataclass
class RainfallEvent:
    """A rainfall event on the district grid.

    Stored factorised as ``depth_mm[t] * weight[r, c]`` rather than as a full
    (T, n, n) cube — same information, a fraction of the memory, and the
    per-hour field is one multiply away.
    """
    hours: int
    depth_mm: np.ndarray        # (T,) basin-mean depth per hour
    weight: np.ndarray          # (n, n) spatial multiplier, mean 1
    antecedent_mm: float        # API over the 7 days before the event
    return_period_years: float
    severity: str
    kind: str

    # Set only when the event came from a live feed. Defaults leave the
    # synthetic path byte-identical to what it was before live mode existed.
    live: bool = False
    soil_saturation: Optional[float] = None
    recurrence: Optional[dict] = None
    observed_hours: int = 0
    sample_points: int = 0
    antecedent_source: str = "synthetic antecedent rainfall"

    @property
    def total_mm(self) -> float:
        return float(self.depth_mm.sum())

    @property
    def peak_mm_hr(self) -> float:
        return float(self.depth_mm.max())

    def field(self, t: int) -> np.ndarray:
        """Rainfall depth (mm) during hour ``t``."""
        return self.depth_mm[int(np.clip(t, 0, self.hours - 1))] * self.weight

    def cumulative(self, t: int) -> np.ndarray:
        """Rainfall depth (mm) accumulated up to and including hour ``t``."""
        return float(self.depth_mm[:int(np.clip(t, 0, self.hours - 1)) + 1].sum()) * self.weight

    def summary(self) -> dict:
        return {
            "severity": self.severity,
            "return_period_years": self.return_period_years,
            "storm_type": self.kind,
            "duration_hours": self.hours,
            "total_mm": round(self.total_mm, 1),
            "peak_mm_per_hour": round(self.peak_mm_hr, 1),
            "antecedent_index_mm": round(self.antecedent_mm, 1),
            "live": self.live,
            "observed_hours": self.observed_hours,
            "forecast_hours": max(self.hours - self.observed_hours, 0),
            "soil_saturation": (None if self.soil_saturation is None
                                else round(self.soil_saturation, 3)),
            "sample_points": self.sample_points,
            "antecedent_source": self.antecedent_source,
            "recurrence": self.recurrence,
        }


def antecedent_index(daily_mm: np.ndarray, decay: float = 0.87) -> float:
    """Antecedent Precipitation Index — how full the ground already is.

    A decay-weighted sum of recent daily rainfall. It is the difference between
    a storm that runs off and one that soaks away: Kerala 2018 was catastrophic
    because the catchment was already saturated when the heaviest rain arrived.
    Recent days count almost fully; a week ago counts for about a third.
    """
    n = len(daily_mm)
    w = decay ** np.arange(n - 1, -1, -1, dtype=np.float64)
    return float((np.asarray(daily_mm, dtype=np.float64) * w).sum())


def build_event(grid: Grid, terrain: Terrain, *, annual_normal_mm: float,
                flood_driver: str, severity: str = "severe",
                hours: int = 96, seed: int = 0,
                wet_antecedent: bool = True) -> RainfallEvent:
    """Assemble a design storm appropriate to the district's flood mechanism."""
    rp = SEVERITY.get(severity, 25.0)
    kind = {"flash": "orographic", "pluvial": "convective"}.get(flood_driver, "monsoon")

    d24 = design_depth_24h(annual_normal_mm, rp)
    # Total event depth exceeds the 24-hour depth for multi-day monsoon storms
    # and roughly equals it for a single convective burst.
    multiplier = {"convective": 0.95, "orographic": 1.55}.get(kind, 1.75)
    total = d24 * multiplier

    shape = hyetograph(hours, kind, seed)
    depth = shape * total

    rng = np.random.default_rng(seed + 17)
    if wet_antecedent:
        daily = rng.uniform(6, 26, 7) * (annual_normal_mm / 1500.0)
    else:
        daily = rng.uniform(0, 4, 7)
    api = antecedent_index(daily)

    return RainfallEvent(
        hours=hours,
        depth_mm=depth,
        weight=spatial_weight(grid, terrain, seed, orographic=(kind == "orographic")),
        antecedent_mm=api,
        return_period_years=rp,
        severity=severity,
        kind=kind,
    )


# ---------------------------------------------------------------------------
# live rainfall
# ---------------------------------------------------------------------------

# Points sampled across a district. Open-Meteo's underlying model grid is around
# 11 km, so a 4x4 sample over a typical district (~19 km spacing) matches the
# resolution the data actually has. Sampling finer would interpolate detail the
# forecast does not contain and present it as spatial structure.
LIVE_SAMPLES = 4

# How much of the window is already-observed rain rather than forecast. Both
# matter: what has already fallen is what the ground is holding, what is coming
# is what there is still time to act on.
LIVE_PAST_HOURS = 24


def _bilinear_to_grid(coarse: np.ndarray, n: int) -> np.ndarray:
    """Resample a small sample grid up to the analysis grid."""
    k = coarse.shape[0]
    if k == 1:
        return np.full((n, n), float(coarse[0, 0]), np.float32)
    src = np.linspace(0, k - 1, n)
    i0 = np.clip(np.floor(src).astype(int), 0, k - 1)
    i1 = np.clip(i0 + 1, 0, k - 1)
    t = (src - i0).astype(np.float32)
    rows = coarse[i0] * (1 - t)[:, None] + coarse[i1] * t[:, None]
    out = rows[:, i0] * (1 - t)[None, :] + rows[:, i1] * t[None, :]
    return out.astype(np.float32)


def estimate_return_period(depth_24h_mm: float,
                           clim: Optional[Dict[str, float]]) -> dict:
    """Place a live rainfall total against the local climatological record.

    Reported as an exceedance band rather than dressed up as a return period in
    years, and that restraint is deliberate. A five-year daily archive contains
    five annual maxima, which is far too few to fit an extreme-value
    distribution: quoting "a 1-in-80-year storm" from it would be a fabrication
    with a decimal point on it. What the record can honestly support is how
    often a day this wet has occurred in it.
    """
    if not clim:
        return {"available": False,
                "reason": "no climatology retrieved for this location"}
    p90 = float(clim.get("daily_p90_mm", 0.0))
    p99 = float(clim.get("daily_p99_mm", 0.0))
    mean = float(clim.get("daily_mean_mm", 0.0))
    years = int(clim.get("years", 0))

    if depth_24h_mm >= p99:
        band, approx = "above the 99th percentile of the local daily record", 100.0
    elif depth_24h_mm >= p90:
        band, approx = "between the 90th and 99th percentile", 10.0
    elif depth_24h_mm >= mean:
        band, approx = "above the local daily mean", 3.0
    else:
        band, approx = "below the local daily mean", 1.0

    return {
        "available": True,
        "depth_24h_mm": round(depth_24h_mm, 1),
        "local_daily_mean_mm": round(mean, 1),
        "local_daily_p90_mm": round(p90, 1),
        "local_daily_p99_mm": round(p99, 1),
        "band": band,
        "approx_recurrence_days": approx,
        "record_years": years,
        "caveat": ("A %d-year daily record holds only %d annual maxima - too "
                   "few to fit an extreme-value distribution. This is an "
                   "exceedance band against the observed record, not a fitted "
                   "return period." % (years, years)),
    }


def build_live_event(grid: Grid, terrain: Terrain, provider, *,
                     hours: int = 96,
                     past_hours: int = LIVE_PAST_HOURS,
                     samples: int = LIVE_SAMPLES,
                     climatology: Optional[Dict[str, float]] = None
                     ) -> Optional[RainfallEvent]:
    """A :class:`RainfallEvent` built from observed and forecast rainfall.

    Satisfies exactly the same interface as the synthetic design storm, so
    runoff, routing and the flood model consume it unchanged - which is the
    whole reason the seam was put here.

    Two things are genuinely better than the synthetic version rather than
    merely real:

    * The **spatial pattern is sampled, not invented.** A grid of points across
      the district is interpolated onto the analysis grid, so a storm sitting
      over one half of a district is represented as such instead of as a smooth
      synthetic field.
    * **Antecedent wetness comes from modelled soil moisture** rather than a
      decay-weighted rainfall proxy. Soil moisture is the variable that decides
      whether rain runs off, and approximating it has been the largest
      simplification in the chain.

    Returns None when the provider cannot be reached, so the caller falls back
    to a design storm and reports that it did.
    """
    n = grid.n
    lats = np.linspace(grid.lat1, grid.lat0, samples)   # north-first
    lons = np.linspace(grid.lon0, grid.lon1, samples)
    points = [(float(la), float(lo)) for la in lats for lo in lons]

    wx = provider.fetch(points, past_days=7, forecast_days=7)
    if not wx or len(wx) != len(points):
        return None

    # Align every point on one window: `past_hours` observed, rest forecast.
    start = max(wx[0].past_hours - past_hours, 0)
    stop = start + hours
    series = []
    for w in wx:
        p = w.precipitation
        if len(p) < stop:
            p = np.pad(p, (0, stop - len(p)), mode="constant")
        series.append(np.nan_to_num(p[start:stop], nan=0.0))
    arr = np.array(series, np.float32)                  # (samples^2, hours)

    basin_mean = arr.mean(axis=0)
    totals = arr.sum(axis=1).reshape(samples, samples)
    mean_total = float(totals.mean())
    if mean_total <= 1e-6:
        weight = np.ones((n, n), np.float32)
    else:
        weight = _bilinear_to_grid(totals / mean_total, n)
        weight = np.clip(smooth(weight, radius=1), 0.05, 5.0).astype(np.float32)

    # Antecedent wetness.
    #
    # Soil moisture is not one input among several here — it is the actual state
    # variable that the antecedent precipitation index has always been a proxy
    # for. Given the real thing, using the proxy as well is a step backwards.
    #
    # An earlier version took max(rainfall_index, saturation * 60), which looked
    # cautious and was in fact broken: a week of steady rain drives the index to
    # several hundred millimetres, so it swamped the soil term completely and a
    # bone-dry catchment and a saturated one produced identical numbers. The
    # test that caught it is test_wetter_soil_floods_more_for_the_same_rain.
    #
    # So saturation is mapped directly onto NEH-630's antecedent moisture scale.
    # amc_adjust reads f = (antecedent_mm - 13) / 40, where f = 0 is AMC I
    # (dry), 1 is AMC II (average) and 2 is AMC III (saturated); inverting that
    # gives antecedent_mm = 13 + 80 * saturation, so the full 0-1 saturation
    # range spans the full AMC range instead of pinning to one end of it.
    sat = float(np.mean([w.soil_saturation for w in wx]))
    api_rain = float(np.mean([w.antecedent_index_mm for w in wx]))
    if np.isfinite(sat) and sat > 0:
        antecedent = 13.0 + 80.0 * float(np.clip(sat, 0.0, 1.0))
        antecedent_source = "modelled soil moisture, 0-7 cm"
    else:
        antecedent = api_rain
        antecedent_source = "decay-weighted antecedent rainfall (no soil moisture)"

    wettest_24h = float(np.sort(basin_mean)[::-1][:24].sum())
    rp = estimate_return_period(wettest_24h, climatology)

    return RainfallEvent(
        hours=hours,
        depth_mm=basin_mean.astype(np.float64),
        weight=weight,
        antecedent_mm=antecedent,
        # Kept only so downstream code that reads it has a number; the honest
        # statement lives in `recurrence`.
        return_period_years=max(float(rp.get("approx_recurrence_days", 1.0)) / 365.0,
                                0.01),
        severity="observed",
        kind="live",
        live=True,
        soil_saturation=sat,
        recurrence=rp,
        observed_hours=past_hours,
        sample_points=len(points),
        antecedent_source=antecedent_source,
    )
