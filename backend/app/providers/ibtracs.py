"""IBTrACS — observed tropical cyclone tracks.

The International Best Track Archive for Climate Stewardship is NOAA's merged
record of every tropical cyclone observed worldwide, assembled from the official
warning centres. The North Indian Ocean basin file holds **1,858 storms and
62,850 track points**, is free, needs no authentication, and covers 1842 to the
present.

This is the only genuinely live historical hazard source available to the
project. Everything else in :mod:`app.data.disasters` had to be curated by hand
because no free API carries Indian flood or landslide history; cyclones are the
exception, and the archive names precisely the storms the districts are
calibrated to — Amphan, Fani, Phailin, Hudhud.

IBTrACS carries **geometry and intensity, not impact**: it knows where a storm
went and how hard it blew, not how many people it killed. Death and displacement
figures live in the curated register and are joined by name and year.

``scripts/fetch_ibtracs.py`` bakes the tracks to a small local file the same way
``fetch_real_dem.py`` bakes elevation, so the shipped application needs no
network to draw the timeline.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .base import Provider, ProviderStatus

CSV_URL = ("https://www.ncei.noaa.gov/data/"
           "international-best-track-archive-for-climate-stewardship-ibtracs/"
           "v04r01/access/csv/ibtracs.NI.list.v04r01.csv")

BAKED = os.environ.get("DRISHTI_TRACKS") or os.path.join(
    os.path.dirname(__file__), "..", "data", "tracks", "ibtracs_ni.json")

# The window the map cares about: the Arabian Sea and Bay of Bengal either side
# of the Indian peninsula. A storm is kept if any of its track points fall here.
INDIA_BBOX = (5.0, 30.0, 65.0, 95.0)        # south, north, west, east

# Track points thin out badly before satellite coverage, and a track drawn from
# four points a day apart is a misleading straight line. 1990 keeps the record
# dense enough to animate honestly.
EARLIEST_SEASON = 1990

# Saffir-Simpson-like bands on 1-minute sustained wind in knots, using the IMD
# classification names that an Indian audience will recognise.
IMD_CATEGORIES = (
    (34, "depression"),
    (48, "cyclonic storm"),
    (64, "severe cyclonic storm"),
    (90, "very severe cyclonic storm"),
    (120, "extremely severe cyclonic storm"),
    (999, "super cyclonic storm"),
)


def category_for(wind_kt: float) -> str:
    for limit, name in IMD_CATEGORIES:
        if wind_kt < limit:
            return name
    return IMD_CATEGORIES[-1][1]


@dataclass
class TrackPoint:
    time: str                  # ISO
    lat: float
    lon: float
    wind_kt: Optional[float]
    pressure_mb: Optional[float]
    dist_to_land_km: Optional[float]


@dataclass
class CycloneTrack:
    sid: str
    name: str
    season: int
    points: List[TrackPoint] = field(default_factory=list)

    @property
    def peak_wind_kt(self) -> float:
        return max((p.wind_kt or 0.0) for p in self.points) if self.points else 0.0

    @property
    def category(self) -> str:
        return category_for(self.peak_wind_kt)

    @property
    def start(self) -> str:
        return self.points[0].time[:10] if self.points else ""

    @property
    def end(self) -> str:
        return self.points[-1].time[:10] if self.points else ""

    @property
    def made_landfall(self) -> bool:
        """Whether the track ever came ashore, by IBTrACS' own distance field."""
        return any((p.dist_to_land_km is not None and p.dist_to_land_km <= 0)
                   for p in self.points)

    def to_dict(self) -> dict:
        return {
            "sid": self.sid, "name": self.name, "season": self.season,
            "start": self.start, "end": self.end,
            "peak_wind_kt": round(self.peak_wind_kt, 1),
            "category": self.category,
            "landfall": self.made_landfall,
            "points": [[p.time, round(p.lat, 3), round(p.lon, 3),
                        p.wind_kt, p.pressure_mb] for p in self.points],
            "provenance": "IBTrACS v04r01 (NOAA NCEI)",
        }


def _f(value: str) -> Optional[float]:
    value = (value or "").strip()
    if not value or value in ("", " ", "NaN"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_csv(text: str, earliest: int = EARLIEST_SEASON,
              bbox: Tuple[float, float, float, float] = INDIA_BBOX
              ) -> List[CycloneTrack]:
    """Parse the IBTrACS CSV into tracks that pass near India.

    Row 0 is the header and **row 1 is a units row**, not data — reading it as a
    record yields a storm at latitude "degrees_north".
    """
    lines = text.splitlines()
    if len(lines) < 3:
        return []
    header = lines[0].split(",")
    ix = {k: i for i, k in enumerate(header)}
    need = ("SID", "SEASON", "NAME", "ISO_TIME", "LAT", "LON")
    if any(k not in ix for k in need):
        return []

    south, north, west, east = bbox
    tracks: Dict[str, CycloneTrack] = {}
    near: Dict[str, bool] = {}

    for line in lines[2:]:
        parts = line.split(",")
        if len(parts) <= ix["LON"]:
            continue
        season = _f(parts[ix["SEASON"]])
        lat = _f(parts[ix["LAT"]])
        lon = _f(parts[ix["LON"]])
        if season is None or lat is None or lon is None or season < earliest:
            continue

        sid = parts[ix["SID"]]
        track = tracks.get(sid)
        if track is None:
            name = (parts[ix["NAME"]] or "").strip()
            track = CycloneTrack(sid=sid, season=int(season),
                                 name="" if name in ("NOT_NAMED", "UNNAMED") else name)
            tracks[sid] = track
            near[sid] = False

        track.points.append(TrackPoint(
            time=parts[ix["ISO_TIME"]].strip(),
            lat=lat, lon=lon,
            wind_kt=_f(parts[ix["WMO_WIND"]]) if "WMO_WIND" in ix else None,
            pressure_mb=_f(parts[ix["WMO_PRES"]]) if "WMO_PRES" in ix else None,
            dist_to_land_km=_f(parts[ix["DIST2LAND"]]) if "DIST2LAND" in ix else None,
        ))
        if south <= lat <= north and west <= lon <= east:
            near[sid] = True

    keep = [t for sid, t in tracks.items() if near.get(sid) and len(t.points) >= 4]
    keep.sort(key=lambda t: t.start)
    return keep


# ---------------------------------------------------------------------------
# baked access
# ---------------------------------------------------------------------------

_CACHE: Optional[List[dict]] = None


def load_baked() -> List[dict]:
    """Tracks from the baked file. Empty if it has not been baked."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(BAKED, encoding="utf-8") as fh:
            blob = json.load(fh)
        _CACHE = blob.get("tracks", [])
    except Exception:
        _CACHE = []
    return _CACHE


def baked_meta() -> dict:
    try:
        with open(BAKED, encoding="utf-8") as fh:
            blob = json.load(fh)
        return {k: v for k, v in blob.items() if k != "tracks"}
    except Exception:
        return {}


def tracks_in_window(start: str, end: str) -> List[dict]:
    return [t for t in load_baked() if t["end"] >= start and t["start"] <= end]


def fetch_remote(timeout: float = 90.0) -> Optional[str]:
    """Download the raw CSV. Used at bake time only."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": "drishti/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


class IbtracsProvider(Provider):
    name = "ibtracs"

    def status(self) -> ProviderStatus:
        tracks = load_baked()
        if tracks:
            meta = baked_meta()
            return ProviderStatus(
                name=self.name, available=True,
                reason=("%d cyclone tracks baked from IBTrACS v04r01 (%s). "
                        "Free, no credentials; impact figures come from the "
                        "curated register."
                        % (len(tracks), meta.get("baked_at", "date unknown"))),
                requires=[])
        return ProviderStatus(
            name=self.name, available=False,
            reason="tracks not baked yet — run scripts/fetch_ibtracs.py",
            requires=[])
