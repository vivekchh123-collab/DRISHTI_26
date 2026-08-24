"""Open-Meteo: live rainfall, cloud, convective energy and soil moisture.

The workhorse of the live system, and the reason a live national screen is
possible today rather than after a month of account approvals: Open-Meteo is
free, needs **no API key and no registration**, serves global hourly data, and
accepts many coordinates in a single request.

Four variables carry the hazard screen:

``precipitation``
    Hourly depth. Past week and forecast week in one call, so an antecedent
    index and a forecast can be built from the same response.
``cape``
    Convective Available Potential Energy, J/kg. The proper measure of
    atmospheric instability — better than cloud cover, which tells you a sky is
    grey without telling you whether it is about to collapse. Above roughly
    1000 J/kg is moderate instability; above 2500 is strong.
``cloud_cover``
    Percentage. Used for the anomaly signal rather than on its own.
``soil_moisture_0_to_7cm`` and ``soil_moisture_7_to_28cm``
    Volumetric water content. This is the variable that actually triggers
    landslides and decides whether rain runs off or soaks away, and until now we
    were approximating it with an antecedent precipitation index.

Everything degrades to ``None`` on failure. A weather API being unreachable must
leave the dashboard on modelled data, never take it down.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .base import Provider, ProviderStatus

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

HOURLY = ("precipitation", "cloud_cover", "cape",
          "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm")

# The subset the national screen needs. Dropping cloud cover and the deep soil
# layer, and shortening the past window, takes a 532-square national sweep from
# 79 seconds to 17 — the difference between a screen that can refresh every half
# hour and one that cannot.
HOURLY_SCREEN = ("precipitation", "cape", "soil_moisture_0_to_7cm")

# Points per HTTP request. Open-Meteo accepts long coordinate lists; batching
# this way turns a 400-cell national sweep into four requests instead of four
# hundred, which is the difference between a screen that can run every half hour
# and one that cannot.
# 200 coordinates per request. Measured: 100 per request takes 79 s for a
# national sweep, 200 takes 49 s with the full variable set and 17 s with the
# lean one. Above roughly 500 the query string exceeds the server's URI limit
# and the request is rejected outright.
BATCH = 200
TIMEOUT = 60
CACHE_TTL = 900.0          # 15 minutes; the model behind this updates hourly

# Open-Meteo's free tier bills **per coordinate**, not per HTTP request. A
# 532-square national sweep therefore costs 532 calls even though it travels as
# three requests, and the hourly allowance is a few thousand. That is ample for
# the intended half-hourly refresh (~1,064/hour) and easy to blow through during
# development, which is exactly what happened while this was being built.
#
# Two consequences are designed for rather than discovered later:
#   * a 429 is remembered, and further calls are suppressed until the limit
#     resets rather than hammering a closed door;
#   * the failure is reported in plain words, because a dashboard that silently
#     serves an hour-old forecast during a storm is worse than one that says it
#     cannot reach the feed.
RATE_LIMIT_BACKOFF = 600.0     # seconds to stand down after a 429
_RATE_LIMITED_UNTIL = 0.0

_CACHE: Dict[str, Tuple[float, object]] = {}
_LOCK = threading.Lock()

# Last transport failure, kept so the provider can say *why* it fell back to
# modelled data. Swallowing the exception and returning None silently is how a
# dashboard ends up quietly stale during the one event it exists for.
LAST_ERROR: Dict[str, object] = {"when": None, "error": None, "url": None}


def _cached(key: str):
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < CACHE_TTL:
        return hit[1]
    return None


def _store(key: str, value) -> None:
    with _LOCK:
        _CACHE[key] = (time.time(), value)


def rate_limited() -> bool:
    """True while we are standing down after a 429."""
    return time.time() < _RATE_LIMITED_UNTIL


def rate_limit_status() -> dict:
    remaining = max(0.0, _RATE_LIMITED_UNTIL - time.time())
    return {
        "rate_limited": remaining > 0,
        "retry_in_seconds": int(remaining),
        "note": ("Open-Meteo's free tier is billed per coordinate, not per "
                 "request. A national sweep of 532 squares costs 532 calls."),
    }


def _get(url: str, params: dict) -> Optional[object]:
    global _RATE_LIMITED_UNTIL
    qs = urllib.parse.urlencode(params, doseq=True)
    full = "%s?%s" % (url, qs)
    cached = _cached(full)
    if cached is not None:
        return cached
    if rate_limited():
        LAST_ERROR.update({
            "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "error": ("rate limited; standing down for %d more seconds"
                      % int(_RATE_LIMITED_UNTIL - time.time())),
            "url": full[:180]})
        return None
    try:
        req = urllib.request.Request(full, headers={"User-Agent": "drishti/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        detail = "%s: %s" % (type(exc).__name__, exc)
        body = getattr(exc, "read", None)
        if callable(body):
            try:
                detail += " | " + body().decode("utf-8", "replace")[:300]
            except Exception:
                pass
        if getattr(exc, "code", None) == 429 or "429" in detail:
            _RATE_LIMITED_UNTIL = time.time() + RATE_LIMIT_BACKOFF
            detail += (" | standing down for %ds; the free tier bills per "
                       "coordinate and a national sweep costs one call per "
                       "square" % int(RATE_LIMIT_BACKOFF))
        LAST_ERROR.update({"when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "error": detail, "url": full[:180]})
        return None
    _store(full, data)
    return data


@dataclass
class PointWeather:
    """Hourly weather at one location, past and forecast."""
    lat: float
    lon: float
    elevation: float
    times: List[str]
    precipitation: np.ndarray          # mm per hour
    cloud_cover: np.ndarray            # percent
    cape: np.ndarray                   # J/kg
    soil_moisture: np.ndarray          # m3/m3, 0-7 cm
    soil_moisture_deep: np.ndarray     # m3/m3, 7-28 cm
    past_hours: int

    # Vertical cloud structure. Empty unless the caller asked for it: the
    # national sweep deliberately does not, because four extra columns over 532
    # squares is four extra requests, while over 22 district centroids it is
    # none. See core/cloudwatch.py for what these are read for.
    cloud_low: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    cloud_mid: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    cloud_high: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    freezing_level: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))

    # ---- derived hazard indicators ----

    @property
    def rain_24h(self) -> float:
        """Rain in the last 24 hours, up to now."""
        a = max(self.past_hours - 24, 0)
        return float(np.nansum(self.precipitation[a:self.past_hours]))

    @property
    def rain_72h(self) -> float:
        a = max(self.past_hours - 72, 0)
        return float(np.nansum(self.precipitation[a:self.past_hours]))

    @property
    def rain_next_24h(self) -> float:
        return float(np.nansum(
            self.precipitation[self.past_hours:self.past_hours + 24]))

    @property
    def rain_next_72h(self) -> float:
        return float(np.nansum(
            self.precipitation[self.past_hours:self.past_hours + 72]))

    @property
    def peak_hourly_next_24h(self) -> float:
        w = self.precipitation[self.past_hours:self.past_hours + 24]
        return float(np.nanmax(w)) if w.size else 0.0

    @property
    def cape_now(self) -> float:
        i = min(self.past_hours, len(self.cape) - 1)
        return float(self.cape[i]) if len(self.cape) else 0.0

    @property
    def cape_max_next_24h(self) -> float:
        w = self.cape[self.past_hours:self.past_hours + 24]
        return float(np.nanmax(w)) if w.size else 0.0

    @property
    def soil_saturation(self) -> float:
        """Current near-surface soil wetness, 0-1.

        Volumetric water content around 0.45 is saturation for most soils, so
        the raw value is scaled against that.
        """
        i = min(self.past_hours, len(self.soil_moisture) - 1)
        if not len(self.soil_moisture):
            return 0.0
        return float(np.clip(self.soil_moisture[i] / 0.45, 0, 1))

    @property
    def antecedent_index_mm(self) -> float:
        """Decay-weighted rain over the past week — the API the models expect."""
        past = self.precipitation[:self.past_hours]
        if not past.size:
            return 0.0
        days = max(1, min(7, len(past) // 24))
        daily = [float(np.nansum(past[max(0, len(past) - 24 * (d + 1)):
                                      len(past) - 24 * d])) for d in range(days)]
        w = 0.87 ** np.arange(len(daily))
        return float(np.nansum(np.array(daily) * w))

    def to_dict(self) -> dict:
        return {
            "lat": round(self.lat, 4), "lon": round(self.lon, 4),
            "elevation_m": round(self.elevation, 1),
            "rain_24h_mm": round(self.rain_24h, 1),
            "rain_72h_mm": round(self.rain_72h, 1),
            "rain_next_24h_mm": round(self.rain_next_24h, 1),
            "rain_next_72h_mm": round(self.rain_next_72h, 1),
            "peak_hourly_next_24h_mm": round(self.peak_hourly_next_24h, 1),
            "cape_now": round(self.cape_now, 0),
            "cape_max_next_24h": round(self.cape_max_next_24h, 0),
            "soil_saturation": round(self.soil_saturation, 3),
            "antecedent_index_mm": round(self.antecedent_index_mm, 1),
        }


def _arr(block: dict, key: str, n: int) -> np.ndarray:
    v = block.get(key)
    if not v:
        return np.zeros(n, np.float32)
    return np.array([np.nan if x is None else x for x in v], np.float32)


class OpenMeteoProvider(Provider):
    """Live weather with no credentials of any kind."""

    name = "open-meteo"

    def status(self) -> ProviderStatus:
        base = ("Live. Open-Meteo requires no API key and no registration; "
                "rainfall, CAPE and soil moisture are real forecast model "
                "output, not modelled by us.")
        if rate_limited():
            return ProviderStatus(
                name=self.name, available=False,
                reason=("Rate limited by Open-Meteo; retrying in %ds. The free "
                        "tier bills per coordinate, so a 532-square national "
                        "sweep costs 532 calls. Cached results are still served."
                        % rate_limit_status()["retry_in_seconds"]),
                requires=[])
        if LAST_ERROR.get("error"):
            base += (" Last transport failure at %s: %s"
                     % (LAST_ERROR["when"], LAST_ERROR["error"]))
        return ProviderStatus(name=self.name, available=True, reason=base,
                              requires=[])

    def search_scenes(self, bbox, days: int = 12, limit: int = 10):
        return []          # not an imaging provider

    # ---- the useful part ----

    def fetch(self, points: Sequence[Tuple[float, float]],
              past_days: int = 7,
              forecast_days: int = 7,
              variables: Sequence[str] = HOURLY
              ) -> Optional[List[PointWeather]]:
        """Hourly weather for many points. Returns None if unreachable."""
        if not points:
            return []
        out: List[PointWeather] = []

        for i in range(0, len(points), BATCH):
            chunk = list(points[i:i + BATCH])
            params = {
                "latitude": ",".join("%.4f" % p[0] for p in chunk),
                "longitude": ",".join("%.4f" % p[1] for p in chunk),
                "hourly": ",".join(variables),
                "past_days": past_days,
                "forecast_days": forecast_days,
                "timezone": "UTC",
            }
            data = _get(FORECAST_URL, params)
            if data is None:
                return None
            # A single coordinate returns an object; several return a list.
            blocks = data if isinstance(data, list) else [data]
            for b in blocks:
                h = b.get("hourly") or {}
                times = h.get("time") or []
                n = len(times)
                out.append(PointWeather(
                    lat=float(b.get("latitude", 0.0)),
                    lon=float(b.get("longitude", 0.0)),
                    elevation=float(b.get("elevation", 0.0) or 0.0),
                    times=times,
                    precipitation=_arr(h, "precipitation", n),
                    cloud_cover=_arr(h, "cloud_cover", n),
                    cape=_arr(h, "cape", n),
                    soil_moisture=_arr(h, "soil_moisture_0_to_7cm", n),
                    soil_moisture_deep=_arr(h, "soil_moisture_7_to_28cm", n),
                    past_hours=past_days * 24,
                    cloud_low=_arr(h, "cloud_cover_low", n),
                    cloud_mid=_arr(h, "cloud_cover_mid", n),
                    cloud_high=_arr(h, "cloud_cover_high", n),
                    freezing_level=_arr(h, "freezing_level_height", n),
                ))
        return out

    def fetch_archive(self, points: Sequence[Tuple[float, float]],
                      start: str, end: str, past_hours: int,
                      variables: Sequence[str] = HOURLY
                      ) -> Optional[List[PointWeather]]:
        """The same hourly record, for a date that has already happened.

        This is what makes the live board demonstrable and, more usefully,
        reviewable. A control room wants to ask "what did the screen show the
        morning Wayanad went" and get the real answer, not a re-enactment: the
        archive serves the identical variables the forecast does, so the whole
        pipeline downstream runs unchanged and unaware.

        ``past_hours`` marks which hour in the window counts as "now", so a
        replayed reading has the same shape as a live one - a past window to
        measure a deepening rate against, and a forward window to escalate into.
        """
        out: List[PointWeather] = []
        for i in range(0, len(points), BATCH):
            chunk = list(points[i:i + BATCH])
            data = _get(ARCHIVE_URL, {
                "latitude": ",".join("%.4f" % p[0] for p in chunk),
                "longitude": ",".join("%.4f" % p[1] for p in chunk),
                "start_date": start, "end_date": end,
                "hourly": ",".join(variables), "timezone": "UTC"})
            if data is None:
                return None
            blocks = data if isinstance(data, list) else [data]
            for b in blocks:
                h = b.get("hourly") or {}
                times = h.get("time") or []
                n = len(times)
                out.append(PointWeather(
                    lat=float(b.get("latitude", 0.0)),
                    lon=float(b.get("longitude", 0.0)),
                    elevation=float(b.get("elevation", 0.0) or 0.0),
                    times=times,
                    precipitation=_arr(h, "precipitation", n),
                    cloud_cover=_arr(h, "cloud_cover", n),
                    cape=_arr(h, "cape", n),
                    soil_moisture=_arr(h, "soil_moisture_0_to_7cm", n),
                    soil_moisture_deep=_arr(h, "soil_moisture_7_to_28cm", n),
                    past_hours=past_hours,
                    cloud_low=_arr(h, "cloud_cover_low", n),
                    cloud_mid=_arr(h, "cloud_cover_mid", n),
                    cloud_high=_arr(h, "cloud_cover_high", n),
                    freezing_level=_arr(h, "freezing_level_height", n),
                ))
        return out

    def climatology(self, lat: float, lon: float,
                    years: int = 5) -> Optional[Dict[str, float]]:
        """Long-run daily rainfall statistics, for the anomaly baseline.

        An anomaly needs a normal to be anomalous against. Without this, a
        cold-cloud screen fires every day of the monsoon and nobody looks at the
        dashboard by July.
        """
        end = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * 5))
        start = time.strftime("%Y-%m-%d",
                              time.gmtime(time.time() - 86400 * 365 * years))
        data = _get(ARCHIVE_URL, {
            "latitude": "%.4f" % lat, "longitude": "%.4f" % lon,
            "start_date": start, "end_date": end,
            "daily": "precipitation_sum", "timezone": "UTC"})
        if data is None:
            return None
        vals = (data.get("daily") or {}).get("precipitation_sum") or []
        arr = np.array([np.nan if v is None else v for v in vals], np.float32)
        arr = arr[np.isfinite(arr)]
        if not arr.size:
            return None
        return {
            "years": years,
            "daily_mean_mm": float(arr.mean()),
            "daily_p90_mm": float(np.percentile(arr, 90)),
            "daily_p99_mm": float(np.percentile(arr, 99)),
            "wet_day_fraction": float((arr >= 1.0).mean()),
            "annual_mm": float(arr.mean() * 365.25),
        }
