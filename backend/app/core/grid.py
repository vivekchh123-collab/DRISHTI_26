"""Analysis grid: the common raster frame every layer is computed on.

One district = one square, north-up, plate-carree grid centred on the district
centroid. Everything downstream (DEM, SAR, rainfall, population, flood depth) is
a ``float32`` array on this exact frame, so layers can be combined by simple
array arithmetic without resampling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

# Grid side in cells. 160 keeps a district analysis fast while still resolving a
# 130 m cell in a metro district.
DEFAULT_N = 160

# Steep districts get a finer grid, because slope is scale-dependent and the
# landslide model depends on it. At 160 cells a Himalayan district lands on
# 550 m cells, which measure *regional* slope: the hillslopes that actually fail
# are 50-100 m across and get averaged flat. Wayanad came out at a mean 4.9
# degrees with 1.3% of cells above 25, which is a plateau statistic for a
# district whose 2024 disaster happened on a steep escarpment the grid could not
# see. Doubling the side costs about four times the routing work and still runs
# in a few seconds.
HILL_N = 320
N_BY_TERRAIN = {"steep-mountain": HILL_N}

_DEG_LAT_KM = 110.574  # metres per degree of latitude, WGS84 mean


@dataclass(frozen=True)
class Grid:
    """A north-up square raster frame."""

    lat0: float      # south edge
    lat1: float      # north edge
    lon0: float      # west edge
    lon1: float      # east edge
    n: int           # cells per side

    # ---------- construction ----------

    @classmethod
    def for_district(cls, lat: float, lon: float, area_km2: float,
                     n: Optional[int] = None, pad: float = 1.18,
                     terrain: Optional[str] = None) -> "Grid":
        """Square frame whose area is ``pad^2`` times the district's area.

        The pad gives room for the district envelope plus the upstream/offshore
        context the hazard models need (a flood plume arrives from outside the
        district; a surge arrives from outside the coastline).
        """
        if n is None:
            n = N_BY_TERRAIN.get(terrain or "", DEFAULT_N)
        side_km = math.sqrt(max(area_km2, 1.0)) * pad
        dlat = side_km / _DEG_LAT_KM / 2.0
        dlon = side_km / (_DEG_LAT_KM * max(math.cos(math.radians(lat)), 0.15)) / 2.0
        return cls(lat - dlat, lat + dlat, lon - dlon, lon + dlon, n)

    # ---------- geometry ----------

    @property
    def height_km(self) -> float:
        return (self.lat1 - self.lat0) * _DEG_LAT_KM

    @property
    def width_km(self) -> float:
        mid = math.radians((self.lat0 + self.lat1) / 2.0)
        return (self.lon1 - self.lon0) * _DEG_LAT_KM * math.cos(mid)

    @property
    def cell_m(self) -> float:
        """Nominal cell size in metres (mean of the two axes)."""
        return (self.height_km + self.width_km) / 2.0 * 1000.0 / self.n

    @property
    def cell_area_km2(self) -> float:
        return (self.height_km / self.n) * (self.width_km / self.n)

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        """(west, south, east, north) - the order Leaflet and GeoJSON use."""
        return (self.lon0, self.lat0, self.lon1, self.lat1)

    @property
    def leaflet_bounds(self) -> List[List[float]]:
        """[[south, west], [north, east]] - Leaflet's LatLngBounds order."""
        return [[self.lat0, self.lon0], [self.lat1, self.lon1]]

    # ---------- coordinate transforms ----------

    def lats(self) -> np.ndarray:
        """Cell-centre latitudes, north-first (row 0 = north edge, image order)."""
        step = (self.lat1 - self.lat0) / self.n
        return (self.lat1 - (np.arange(self.n) + 0.5) * step).astype(np.float64)

    def lons(self) -> np.ndarray:
        step = (self.lon1 - self.lon0) / self.n
        return (self.lon0 + (np.arange(self.n) + 0.5) * step).astype(np.float64)

    def meshgrid(self) -> Tuple[np.ndarray, np.ndarray]:
        """(lat_grid, lon_grid), both (n, n)."""
        return np.meshgrid(self.lats(), self.lons(), indexing="ij")

    def rowcol(self, lat: float, lon: float) -> Tuple[int, int]:
        """Nearest cell (row, col) for a coordinate; clamped to the grid."""
        r = int((self.lat1 - lat) / (self.lat1 - self.lat0) * self.n)
        c = int((lon - self.lon0) / (self.lon1 - self.lon0) * self.n)
        return (min(max(r, 0), self.n - 1), min(max(c, 0), self.n - 1))

    def latlon(self, row: int, col: int) -> Tuple[float, float]:
        """Cell-centre coordinate for a (row, col)."""
        lat = self.lat1 - (row + 0.5) * (self.lat1 - self.lat0) / self.n
        lon = self.lon0 + (col + 0.5) * (self.lon1 - self.lon0) / self.n
        return (float(lat), float(lon))

    def cell_bounds(self, row: int, col: int) -> List[List[float]]:
        """[[south, west], [north, east]] for one cell - for Leaflet rectangles."""
        dlat = (self.lat1 - self.lat0) / self.n
        dlon = (self.lon1 - self.lon0) / self.n
        north = self.lat1 - row * dlat
        west = self.lon0 + col * dlon
        return [[north - dlat, west], [north, west + dlon]]

    def cell_polygon(self, row: int, col: int) -> List[List[float]]:
        """GeoJSON-order ring [[lon, lat], ...] for one cell."""
        (s, w), (nn, e) = self.cell_bounds(row, col)
        return [[w, s], [e, s], [e, nn], [w, nn], [w, s]]


# ---------------------------------------------------------------------------
# distance / bearing helpers used by the narrative and routing layers
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def compass(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """16-point compass direction from point 1 to point 2."""
    return _COMPASS[int((bearing_deg(lat1, lon1, lat2, lon2) + 11.25) % 360 // 22.5)]


def describe_location(grid: Grid, row: int, col: int,
                      ref_lat: float, ref_lon: float, ref_name: str) -> str:
    """Human-readable position of a cell relative to a named reference point.

    Deliberately avoids inventing settlement names: it states a measured distance
    and bearing from a real, named place (the district headquarters).
    """
    lat, lon = grid.latlon(row, col)
    d = haversine_km(ref_lat, ref_lon, lat, lon)
    if d < 1.5:
        return "%s town centre" % ref_name
    return "%.0f km %s of %s" % (d, compass(ref_lat, ref_lon, lat, lon), ref_name)
