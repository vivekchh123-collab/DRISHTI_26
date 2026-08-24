"""Central Water Commission river gauges.

**Status: interface written, not live.** This file exists so that the one thing
missing is a data feed rather than code, and so nobody has to guess what was
tried.

CWC's flood forecasting service at ``ffs.india-water.gov.in`` publishes live
gauge readings against official Warning and Danger levels for roughly 200
stations. Operationally it is the single most valuable flood input in India: a
reading of "48.2 m against a danger level of 47.5 m" is worth more at two in the
morning than any satellite image, and it plugs directly into the HAND model,
because river stage is exactly what HAND converts into an inundation map.

What was tried, and what happened:

* ``ffs.india-water.gov.in`` — serves HTML only. The page is a JavaScript
  application that fetches its data after load; no documented JSON endpoint.
* ``indiawris.gov.in`` and ``arc.indiawris.gov.in`` — connection timed out from
  this network. India-WRIS also requires registration for bulk access.
* No public, documented, keyless REST API for CWC gauge data was found.

So the honest position is: the model is ready for river stage, and the feed is
not available without either an agreement with CWC or a scraper against an
undocumented endpoint that would break without notice. Building the latter and
calling it an integration would be worse than saying this.

Three ways to make it live, in order of how much they should be preferred:

1. **A data-sharing agreement with CWC.** The correct route for anything
   operational, and the one a state authority can actually obtain.
2. **India-WRIS registered access** at ``indiawris.gov.in``.
3. **A state flood-control department feed.** Bihar, Assam and Kerala each run
   their own gauge telemetry, often with fewer access restrictions than the
   national service.

Until one of those exists, :func:`stage_for` returns ``None`` and the flood
model runs on modelled stage, which the provenance block reports as
``modelled``. It never silently substitutes a guess for a measurement.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .base import Provider, ProviderStatus

# Set to a JSON endpoint returning gauge readings and the connector goes live
# without a code change.
ENDPOINT_ENV = "DRISHTI_CWC_ENDPOINT"
TIMEOUT = 20


@dataclass
class GaugeReading:
    """One river gauge, and where it sits against its official thresholds."""
    station: str
    river: str
    lat: float
    lon: float
    level_m: float
    warning_level_m: float
    danger_m: float
    observed_at: str

    @property
    def above_danger_m(self) -> float:
        return self.level_m - self.danger_m

    @property
    def status(self) -> str:
        if self.level_m >= self.danger_m:
            return "above danger"
        if self.level_m >= self.warning_level_m:
            return "above warning"
        return "normal"

    def to_dict(self) -> dict:
        return {
            "station": self.station, "river": self.river,
            "lat": self.lat, "lon": self.lon,
            "level_m": round(self.level_m, 2),
            "warning_level_m": round(self.warning_level_m, 2),
            "danger_level_m": round(self.danger_m, 2),
            "above_danger_m": round(self.above_danger_m, 2),
            "status": self.status,
            "observed_at": self.observed_at,
        }


class CWCProvider(Provider):
    name = "cwc"

    def __init__(self, endpoint: Optional[str] = None) -> None:
        self.endpoint = endpoint or os.environ.get(ENDPOINT_ENV, "")

    def status(self) -> ProviderStatus:
        if not self.endpoint:
            return ProviderStatus(
                name=self.name, available=False,
                reason=("No public keyless API for CWC gauge data was found. "
                        "ffs.india-water.gov.in serves HTML only and loads its "
                        "data from an undocumented endpoint; India-WRIS timed "
                        "out and requires registration. Set %s to a JSON feed, "
                        "or obtain a CWC data-sharing agreement, and this goes "
                        "live with no code change." % ENDPOINT_ENV),
                requires=[ENDPOINT_ENV])
        return ProviderStatus(name=self.name, available=True,
                              reason="Gauge endpoint configured.", requires=[])

    def search_scenes(self, bbox, days: int = 12, limit: int = 10):
        return []          # not an imaging provider

    def gauges(self, bbox: Optional[Tuple[float, float, float, float]] = None
               ) -> Optional[List[GaugeReading]]:
        """Live readings, or None when no feed is configured.

        The expected payload is a list of objects with ``station``, ``river``,
        ``lat``, ``lon``, ``level_m``, ``warning_level_m``, ``danger_level_m``
        and ``observed_at``. Any feed reshaped to that contract works.
        """
        if not self.endpoint:
            return None
        url = self.endpoint
        if bbox:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
                {"bbox": ",".join("%.4f" % v for v in bbox)})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "drishti/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

        rows = payload if isinstance(payload, list) else payload.get("data", [])
        out: List[GaugeReading] = []
        for g in rows:
            try:
                out.append(GaugeReading(
                    station=str(g.get("station", "")),
                    river=str(g.get("river", "")),
                    lat=float(g["lat"]), lon=float(g["lon"]),
                    level_m=float(g["level_m"]),
                    warning_level_m=float(g.get("warning_level_m", 0.0)),
                    danger_m=float(g.get("danger_level_m", 0.0)),
                    observed_at=str(g.get("observed_at", "")),
                ))
            except Exception:
                continue          # a malformed row must not lose the good ones
        return out

    def stage_for(self, lat: float, lon: float,
                  radius_km: float = 60.0) -> Optional[GaugeReading]:
        """The nearest gauge to a district, if one is within reach."""
        from ..core.grid import haversine_km

        rows = self.gauges()
        if not rows:
            return None
        near = [(haversine_km(lat, lon, g.lat, g.lon), g) for g in rows]
        near = [(d, g) for d, g in near if d <= radius_km]
        if not near:
            return None
        return min(near, key=lambda t: t[0])[1]
