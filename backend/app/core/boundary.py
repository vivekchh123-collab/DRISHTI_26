"""District boundaries.

Real Survey-of-India / LGD boundaries are loaded from
``data/boundaries/districts.geojson`` when that file is present. When it is not —
which is the default, because we do not redistribute that data — an *approximate
envelope* is synthesised: a smooth star-shaped polygon centred on the district
centroid, scaled to the district's true geographic area.

The envelope is honest scaffolding, not a claim. It carries the right centroid
and the right area, and every response that uses it is tagged
``boundary_source: "approximate-envelope"`` so nothing downstream can mistake it
for a surveyed boundary. Its job is to scope population and area statistics to
something district-sized instead of to the analysis frame, which is 39% larger.

Because the polygon is star-shaped about the centroid, the polygon and its raster
mask are generated from the same radial function and cannot disagree — no
marching squares, no rasterisation mismatch.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .grid import Grid

_HARMONICS = (2, 3, 5, 7)          # lobe counts; coprime-ish so they don't align


@dataclass
class Boundary:
    """A district outline plus its raster mask on the analysis grid."""
    ring: List[Tuple[float, float]]     # (lon, lat), closed
    mask: np.ndarray                    # bool, True inside the district
    source: str
    area_km2: float

    def geojson(self, props: Optional[dict] = None) -> dict:
        return {
            "type": "Feature",
            "properties": {"boundary_source": self.source, **(props or {})},
            "geometry": {"type": "Polygon",
                         "coordinates": [[list(p) for p in self.ring]]},
        }


def _radii(angles: np.ndarray, seed: int) -> np.ndarray:
    """Smooth random radial profile with mean 1.0."""
    rng = np.random.default_rng(seed)
    r = np.ones_like(angles)
    for k in _HARMONICS:
        amp = rng.uniform(0.05, 0.15) / (1.0 + 0.35 * (k - 2))
        phase = rng.uniform(0, 2 * math.pi)
        r += amp * np.cos(k * angles + phase)
    return r


def envelope(grid: Grid, centre_lat: float, centre_lon: float,
             area_km2: float, seed: int, vertices: int = 180,
             must_include: Optional[Tuple[float, float]] = None) -> Boundary:
    """Synthesise an approximate district envelope of the correct area.

    ``must_include`` is a (lat, lon) the polygon has to contain — in practice the
    district headquarters. Without it a randomly-shaped envelope can exclude the
    district's own principal town, which for a compact district like Mumbai
    Suburban put Bandra outside the boundary and gave the headquarters a
    population of zero.
    """
    ang = np.linspace(0, 2 * math.pi, vertices, endpoint=False)
    shape = _radii(ang, seed)

    km_lat = 110.574
    km_lon = 111.320 * math.cos(math.radians(centre_lat))
    if must_include is not None:
        hy = (must_include[0] - centre_lat) * km_lat
        hx = (must_include[1] - centre_lon) * km_lon
        h_ang = math.atan2(hx, hy) % (2 * math.pi)
        h_r = math.hypot(hx, hy)
        unit = 0.5 * float(np.mean(shape ** 2) * 2 * math.pi)
        r_km0 = math.sqrt(max(area_km2, 1.0) / unit)
        needed = h_r * 1.08 / max(r_km0, 1e-6)
        here = float(np.interp(h_ang, ang, shape, period=2 * math.pi))
        if needed > here:
            # Smooth local bulge toward the town, then renormalise below so the
            # polygon still encloses the district's true area.
            d = np.abs((ang - h_ang + math.pi) % (2 * math.pi) - math.pi)
            shape = shape + (needed - here) * np.exp(-0.5 * (d / 0.9) ** 2)

    # Scale so the polygon's area matches the district's. For a star-shaped
    # polygon, A = 1/2 * integral r^2 dtheta, so the required radius scale is
    # sqrt(target / unit-area).
    unit_area = 0.5 * float(np.mean(shape ** 2) * 2 * math.pi)
    r_km = math.sqrt(max(area_km2, 1.0) / unit_area)
    radii_km = shape * r_km

    km_per_deg_lat, km_per_deg_lon = km_lat, km_lon

    ring: List[Tuple[float, float]] = []
    for a, rk in zip(ang, radii_km):
        dlat = (rk * math.cos(a)) / km_per_deg_lat
        dlon = (rk * math.sin(a)) / max(km_per_deg_lon, 1e-6)
        ring.append((round(centre_lon + dlon, 6), round(centre_lat + dlat, 6)))
    ring.append(ring[0])

    # Same radial function evaluated per cell, so mask and ring agree exactly.
    lat_g, lon_g = grid.meshgrid()
    dy_km = (lat_g - centre_lat) * km_per_deg_lat
    dx_km = (lon_g - centre_lon) * km_per_deg_lon
    cell_ang = np.arctan2(dx_km, dy_km) % (2 * math.pi)
    cell_r = np.hypot(dx_km, dy_km)
    limit = np.interp(cell_ang, ang, radii_km, period=2 * math.pi)

    return Boundary(ring=ring, mask=cell_r <= limit,
                    source="approximate-envelope", area_km2=float(area_km2))


def _ring_from_geometry(geom: dict) -> Optional[List[Tuple[float, float]]]:
    t = geom.get("type")
    if t == "Polygon":
        return [tuple(p[:2]) for p in geom["coordinates"][0]]
    if t == "MultiPolygon":
        best = max(geom["coordinates"], key=lambda poly: len(poly[0]))
        return [tuple(p[:2]) for p in best[0]]
    return None


def _mask_from_ring(grid: Grid, ring: Sequence[Tuple[float, float]]) -> np.ndarray:
    """Even-odd ray casting, vectorised over the grid.

    Used only for loaded boundaries, which are arbitrary polygons rather than
    star-shaped, so the radial shortcut does not apply.
    """
    lat_g, lon_g = grid.meshgrid()
    inside = np.zeros(lat_g.shape, bool)
    xs = np.array([p[0] for p in ring])
    ys = np.array([p[1] for p in ring])
    for i in range(len(ring) - 1):
        x1, y1, x2, y2 = xs[i], ys[i], xs[i + 1], ys[i + 1]
        if y1 == y2:
            continue
        straddles = ((y1 > lat_g) != (y2 > lat_g))
        xint = x1 + (lat_g - y1) * (x2 - x1) / (y2 - y1)
        inside ^= straddles & (lon_g < xint)
    return inside


def load(grid: Grid, code: str, centre_lat: float, centre_lon: float,
         area_km2: float, seed: int,
         path: Optional[str] = None,
         must_include: Optional[Tuple[float, float]] = None) -> Boundary:
    """Real boundary if one is available for ``code``, else a synthetic envelope.

    A loaded feature is matched on a ``code`` / ``district_code`` property. The
    file format is plain GeoJSON so any published district layer can be dropped
    in without a converter.
    """
    path = path or os.environ.get(
        "DRISHTI_BOUNDARIES",
        os.path.join(os.path.dirname(__file__), "..", "..", "..",
                     "data", "boundaries", "districts.geojson"))
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                fc = json.load(fh)
            for feat in fc.get("features", []):
                props = feat.get("properties", {})
                if code in (props.get("code"), props.get("district_code")):
                    ring = _ring_from_geometry(feat.get("geometry", {}))
                    if ring:
                        return Boundary(ring=ring, mask=_mask_from_ring(grid, ring),
                                        source=os.path.basename(path),
                                        area_km2=float(area_km2))
    except Exception:
        # A malformed or unreadable boundary file must not take the district
        # offline — fall through to the envelope and keep the label honest.
        pass
    return envelope(grid, centre_lat, centre_lon, area_km2, seed,
                    must_include=must_include)
