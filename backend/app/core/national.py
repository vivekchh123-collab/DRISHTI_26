"""National monitoring grid: the whole of India, in squares, on live weather.

The screen that makes the rest affordable. A full district assessment costs
several seconds; running that continuously for every district in India is not
possible at any sane budget. So the country is tessellated into squares, each
square is scored every cycle from **live** weather, and the expensive physics is
spent only where a square fires.

**Thresholds are IMD's own.** Rainfall bands come from the India Meteorological
Department's official warning categories rather than from anything invented
here — heavy, very heavy, extremely heavy. A District Magistrate already knows
what those words mean and already has standing orders attached to them, which is
worth far more than a bespoke index nobody can act on.

Four live signals, all from Open-Meteo with no credentials:

* **Forecast rainfall** over 24 and 72 hours, against the IMD bands.
* **CAPE** — convective available potential energy. The atmosphere's appetite
  for a cloudburst, and a much sharper signal than cloud cover, which merely
  says the sky is grey.
* **Soil saturation** — the variable that decides whether rain runs off or soaks
  away, and the one that turns a survivable storm into Kerala 2018.
* **Antecedent rainfall** over the past week.

Squares containing a modelled district are additionally tagged with that
district's physics-derived red-zone fraction. Everything else carries a
**terrain-blind** score, and says so: without a DEM there is no HAND, and
without HAND the screen can say a storm is coming but not where the water will
go.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..data import districts as districts_data
from ..providers.openmeteo import (HOURLY_SCREEN, OpenMeteoProvider,
                                   PointWeather)

# India Meteorological Department 24-hour rainfall warning categories, mm.
# https://mausam.imd.gov.in — these are the bands IMD issues colour warnings on.
IMD_RAINFALL = (
    (0.0, "no warning", "green"),
    (64.5, "heavy", "yellow"),
    (115.6, "very heavy", "orange"),
    (204.5, "extremely heavy", "red"),
)

# CAPE bands, J/kg. Standard convective-instability interpretation.
CAPE_BANDS = ((1000.0, "marginal"), (2500.0, "moderate"),
              (4000.0, "strong"), (1e9, "extreme"))

# A simplified outline of mainland India, used to keep the grid on land.
# Deliberately coarse: it exists to reject ocean cells, not to draw a boundary,
# and it is never rendered as one. Island territories are outside it and are
# noted as an exclusion rather than silently dropped.
INDIA_OUTLINE: Tuple[Tuple[float, float], ...] = (
    (68.2, 23.7), (68.0, 24.7), (70.0, 27.5), (72.0, 29.0), (74.0, 31.5),
    (75.5, 32.5), (77.0, 35.0), (78.5, 34.5), (79.5, 33.0), (81.0, 30.5),
    (83.0, 29.2), (85.0, 27.0), (88.0, 27.0), (89.0, 26.5), (92.0, 27.5),
    (95.0, 28.0), (97.0, 28.3), (97.3, 27.0), (96.5, 25.5), (94.5, 24.0),
    (93.5, 22.5), (92.5, 21.0), (91.0, 22.0), (89.0, 21.7), (87.0, 21.5),
    (85.0, 19.5), (82.0, 17.0), (80.3, 15.8), (80.0, 13.5), (79.8, 11.5),
    (79.3, 10.3), (78.2, 8.9), (77.5, 8.1), (76.5, 9.0), (75.8, 11.5),
    (74.8, 13.5), (73.8, 15.5), (72.8, 18.5), (72.6, 21.0), (72.0, 21.5),
    (70.0, 20.8), (69.0, 22.0), (68.2, 23.7),
)

CACHE_TTL = 1800.0        # half an hour, matching the intended screen cycle
_CACHE: Dict[str, Tuple[float, "NationalScreen"]] = {}
_LOCK = threading.Lock()


def rainfall_band(mm: float) -> Tuple[str, str]:
    label, colour = IMD_RAINFALL[0][1], IMD_RAINFALL[0][2]
    for limit, name, col in IMD_RAINFALL:
        if mm >= limit:
            label, colour = name, col
    return label, colour


def cape_band(j: float) -> str:
    for limit, name in CAPE_BANDS:
        if j < limit:
            return name
    return CAPE_BANDS[-1][1]


def in_india(lon: float, lat: float) -> bool:
    """Even-odd ray casting against the simplified outline."""
    inside = False
    n = len(INDIA_OUTLINE)
    for i in range(n - 1):
        x1, y1 = INDIA_OUTLINE[i]
        x2, y2 = INDIA_OUTLINE[i + 1]
        if (y1 > lat) != (y2 > lat):
            xint = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < xint:
                inside = not inside
    return inside


@dataclass
class GridCell:
    """One square of the national screen."""
    id: str
    lat: float
    lon: float
    size_deg: float
    # live signals
    rain_24h: float = 0.0
    rain_next_24h: float = 0.0
    rain_next_72h: float = 0.0
    peak_hourly: float = 0.0
    cape: float = 0.0
    soil_saturation: float = 0.0
    antecedent_mm: float = 0.0
    elevation: float = 0.0
    # scoring
    score: float = 0.0
    band: str = "no warning"
    colour: str = "green"
    drivers: List[str] = field(default_factory=list)
    # linkage to the modelled districts
    districts: List[str] = field(default_factory=list)
    red_zone_fraction: Optional[float] = None

    @property
    def bounds(self) -> List[List[float]]:
        h = self.size_deg / 2.0
        return [[self.lat - h, self.lon - h], [self.lat + h, self.lon + h]]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lat": round(self.lat, 3), "lon": round(self.lon, 3),
            "bounds": self.bounds,
            "score": round(self.score, 1),
            "band": self.band, "colour": self.colour,
            "rain_24h_mm": round(self.rain_24h, 1),
            "rain_next_24h_mm": round(self.rain_next_24h, 1),
            "rain_next_72h_mm": round(self.rain_next_72h, 1),
            "peak_hourly_mm": round(self.peak_hourly, 1),
            "cape": round(self.cape, 0),
            "cape_band": cape_band(self.cape),
            "soil_saturation": round(self.soil_saturation, 2),
            "antecedent_mm": round(self.antecedent_mm, 1),
            "elevation_m": round(self.elevation, 0),
            "drivers": self.drivers,
            "districts": self.districts,
            "red_zone_fraction": (None if self.red_zone_fraction is None
                                  else round(self.red_zone_fraction, 3)),
            "modelled": bool(self.districts),
        }


def score_cell(c: GridCell) -> None:
    """Score one square, and record why.

    Rainfall carries the most weight because it is the proximate cause; soil
    saturation is second because it decides how much of that rain becomes
    runoff. CAPE is a shorter-fuse signal and is weighted for the cloudburst
    case rather than the seasonal one.
    """
    band, colour = rainfall_band(max(c.rain_next_24h, c.rain_24h))
    drivers: List[str] = []

    # Rainfall against the IMD bands, normalised on the extremely-heavy limit.
    rain = float(np.clip(max(c.rain_next_24h, c.rain_24h) / 204.5, 0, 1))
    rain72 = float(np.clip(c.rain_next_72h / 350.0, 0, 1))
    cape = float(np.clip((c.cape - 800.0) / 3200.0, 0, 1))
    soil = float(np.clip(c.soil_saturation, 0, 1))
    burst = float(np.clip(c.peak_hourly / 50.0, 0, 1))

    if band != "no warning":
        drivers.append("IMD %s rainfall forecast (%.0f mm/24h)"
                       % (band, max(c.rain_next_24h, c.rain_24h)))
    if soil >= 0.85:
        drivers.append("soil near saturation (%.0f%%) — rain will run off, not soak in"
                       % (soil * 100))
    if c.cape >= 2500:
        drivers.append("CAPE %.0f J/kg — %s convective instability"
                       % (c.cape, cape_band(c.cape)))
    if c.peak_hourly >= 20:
        drivers.append("peak %.0f mm in a single hour — cloudburst risk"
                       % c.peak_hourly)
    if c.antecedent_mm >= 80:
        drivers.append("%.0f mm antecedent index — catchment already wet"
                       % c.antecedent_mm)
    if c.red_zone_fraction and c.red_zone_fraction >= 0.25:
        drivers.append("%.0f%% of the modelled district is standing red zone"
                       % (c.red_zone_fraction * 100))

    score = 100.0 * (0.34 * rain + 0.18 * rain72 + 0.22 * soil
                     + 0.14 * cape + 0.12 * burst)
    # A square containing known red-zone land is not more likely to be rained
    # on, but the same rain does more harm there.
    if c.red_zone_fraction:
        score *= 1.0 + 0.25 * float(np.clip(c.red_zone_fraction, 0, 1))

    c.score = float(np.clip(score, 0, 100))
    c.band, c.colour = band, colour
    c.drivers = drivers


@dataclass
class NationalScreen:
    cells: List[GridCell]
    size_deg: float
    generated_at: str
    live: bool
    source: str
    seconds: float

    @property
    def watchlist(self) -> List[GridCell]:
        return sorted([c for c in self.cells if c.score >= 45.0],
                      key=lambda c: -c.score)

    def summary(self) -> dict:
        scores = [c.score for c in self.cells] or [0.0]
        return {
            # Named `cell_count`, not `cells`: the API response spreads this
            # summary and then adds the cell list, and a key collision silently
            # replaced the count with the array.
            "cell_count": len(self.cells),
            "cell_size_deg": self.size_deg,
            "cell_size_km": round(self.size_deg * 111.0, 1),
            "live": self.live,
            "source": self.source,
            "generated_at": self.generated_at,
            "build_seconds": round(self.seconds, 1),
            "mean_score": round(float(np.mean(scores)), 1),
            "max_score": round(float(np.max(scores)), 1),
            "watchlist_size": len(self.watchlist),
            "imd_bands": [{"threshold_mm": t, "label": n, "colour": c}
                          for t, n, c in IMD_RAINFALL],
            "coverage_note": (
                "Mainland India only. Island territories fall outside the "
                "simplified land outline used to place the grid."),
            "terrain_note": (
                "Squares that contain one of the modelled districts carry a "
                "physics-derived red-zone fraction. All others are scored on "
                "weather alone — without a DEM there is no HAND, so the screen "
                "can say a storm is coming but not where the water will go."),
        }


def build_grid(size_deg: float = 0.75) -> List[GridCell]:
    """Tessellate mainland India into squares."""
    cells: List[GridCell] = []
    lat = 6.0
    i = 0
    while lat <= 37.0:
        lon = 68.0
        while lon <= 98.0:
            if in_india(lon, lat):
                cells.append(GridCell(id="G%04d" % i, lat=round(lat, 3),
                                      lon=round(lon, 3), size_deg=size_deg))
                i += 1
            lon += size_deg
        lat += size_deg
    return cells


def _attach_districts(cells: Sequence[GridCell]) -> None:
    """Tag each square with any modelled district falling inside it."""
    from . import scenario as scenario_mod

    for d in districts_data.DISTRICTS:
        best, bestd = None, 1e9
        for c in cells:
            dd = (c.lat - d.lat) ** 2 + (c.lon - d.lon) ** 2
            if dd < bestd:
                best, bestd = c, dd
        if best is not None and bestd <= (best.size_deg ** 2):
            best.districts.append(d.code)

    for c in cells:
        if not c.districts:
            continue
        # Only read assessments already computed. Building twenty-two of them
        # to draw the opening screen would defeat the purpose of a cheap screen.
        fracs = []
        for code in c.districts:
            with scenario_mod._LOCK:
                a = scenario_mod._REDZONE_CACHE.get(code)
            if a is not None:
                mask = a.exposure.district_mask
                red = a.redzones.is_red & mask
                fracs.append(float(red.sum()) / max(int(mask.sum()), 1))
        if fracs:
            c.red_zone_fraction = float(np.mean(fracs))


def build(size_deg: float = 0.75, live: bool = True) -> NationalScreen:
    """Score every square of India. Falls back to a dry screen if offline."""
    t0 = time.time()
    cells = build_grid(size_deg)
    _attach_districts(cells)

    source = "modelled (no live connection)"
    got_live = False
    if live:
        wx = OpenMeteoProvider().fetch([(c.lat, c.lon) for c in cells],
                                       past_days=5, forecast_days=3,
                                       variables=HOURLY_SCREEN)
        if wx and len(wx) == len(cells):
            got_live = True
            source = ("Open-Meteo live forecast (no API key); "
                      "IMD rainfall warning bands")
            for c, w in zip(cells, wx):
                c.rain_24h = w.rain_24h
                c.rain_next_24h = w.rain_next_24h
                c.rain_next_72h = w.rain_next_72h
                c.peak_hourly = w.peak_hourly_next_24h
                c.cape = w.cape_max_next_24h
                c.soil_saturation = w.soil_saturation
                c.antecedent_mm = w.antecedent_index_mm
                c.elevation = w.elevation

    for c in cells:
        score_cell(c)

    return NationalScreen(
        cells=cells, size_deg=size_deg,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        live=got_live, source=source, seconds=time.time() - t0)


def get(size_deg: float = 0.75, live: bool = True) -> NationalScreen:
    key = "%.3f-%s" % (size_deg, live)
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < CACHE_TTL:
        return hit[1]
    built = build(size_deg, live)
    with _LOCK:
        _CACHE[key] = (time.time(), built)
    return built


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
