"""Waterlogging: where water sits after the river has gone down.

Riverine flooding and waterlogging are different problems with different
remedies, and conflating them is a common failure in flood dashboards. A flood
arrives from the river and leaves when the river falls. Waterlogging is rain that
lands in a place with nowhere to drain to — it can persist for weeks after the
river is back in bank, and it is what actually keeps people in relief camps in
north Bihar and what drowns Chennai and Mumbai.

Susceptibility is a weighted overlay of four terrain and land-cover controls:

* **Topographic wetness index** — a large upslope area draining into flat ground.
* **Height above nearest drainage** — low ground has nowhere to shed water to.
* **Imperviousness** — concrete infiltrates nothing.
* **Drainage density** — few channels nearby means no route out.

The important property is that this is computable *before it rains*. It is a
standing planning asset: a district can use it to site drainage investment, and a
relief officer can use it to predict which camps will still be occupied in a
fortnight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .exposure import Exposure
from .flood import MIN_DEPTH_M, FloodTimeline
from .grid import Grid, describe_location
from .terrain import Terrain, normalize, smooth

# Weights of the susceptibility overlay. Published as part of the API response
# so the index can be argued with rather than taken on trust.
WEIGHTS: Dict[str, float] = {
    "wetness": 0.34,        # TWI — converging flow onto flat ground
    "low_lying": 0.30,      # inverse HAND — no vertical escape
    "impervious": 0.21,     # built-up fraction — no infiltration
    "poor_drainage": 0.15,  # inverse drainage density — no lateral escape
}

# Linear-reservoir recession constants, in hours, by how built-up a cell is.
# A paved catchment with working storm drains empties fast; a clay-bottomed
# backswamp does not empty at all until it evaporates.
K_URBAN_HOURS = 14.0
K_RURAL_HOURS = 78.0


@dataclass
class WaterloggingHotspot:
    id: str
    label: str
    lat: float
    lon: float
    susceptibility: float        # 0..1
    area_km2: float
    population: int
    drain_down_hours: float
    standing_depth_m: float
    drivers: List[str]           # which controls dominate here

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label,
            "lat": round(self.lat, 5), "lon": round(self.lon, 5),
            "susceptibility": round(self.susceptibility, 3),
            "area_km2": round(self.area_km2, 2),
            "population": self.population,
            "drain_down_hours": round(self.drain_down_hours, 1),
            "drain_down_days": round(self.drain_down_hours / 24.0, 1),
            "standing_depth_m": round(self.standing_depth_m, 2),
            "drivers": self.drivers,
        }


@dataclass
class Waterlogging:
    susceptibility: np.ndarray       # 0..1 per cell
    drain_down_hours: np.ndarray     # hours to fall below the nuisance depth
    hotspots: List[WaterloggingHotspot]
    weights: Dict[str, float]

    def summary(self) -> dict:
        s = self.susceptibility
        return {
            "weights": self.weights,
            "high_susceptibility_fraction": round(float((s >= 0.66).mean()), 4),
            "hotspots": [h.to_dict() for h in self.hotspots],
        }


def susceptibility_grid(terrain: Terrain, exposure: Exposure) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """Weighted-overlay waterlogging susceptibility, 0..1, plus its components."""
    mask = exposure.district_mask

    # Clip each control at its own 2nd/98th percentile before normalising, so a
    # single extreme cell cannot compress the rest of the district into a
    # featureless band.
    def scaled(a: np.ndarray, invert: bool = False) -> np.ndarray:
        inside = a[mask] if mask.any() else a
        lo, hi = np.percentile(inside, 2), np.percentile(inside, 98)
        v = np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1)
        return (1.0 - v if invert else v).astype(np.float32)

    parts = {
        "wetness": scaled(terrain.twi),
        "low_lying": scaled(terrain.hand, invert=True),
        "impervious": np.clip(exposure.built_up, 0, 1).astype(np.float32),
        "poor_drainage": scaled(terrain.drainage_density, invert=True),
    }

    s = sum(WEIGHTS[k] * v for k, v in parts.items())
    # Open water and sea are not "waterlogged" — they are water.
    s = np.where(terrain.streams | terrain.sea_mask, 0.0, s)
    s = np.where(mask, smooth(s, radius=1), 0.0)
    return np.clip(s, 0, 1).astype(np.float32), parts


def drain_down(terrain: Terrain, exposure: Exposure, susceptibility: np.ndarray,
               standing_depth: np.ndarray) -> np.ndarray:
    """Hours for standing water to fall below the nuisance threshold.

    Linear-reservoir recession, ``d(t) = d0 * exp(-t / k)``, inverted for the
    time to reach :data:`~app.core.flood.MIN_DEPTH_M`. The residence time ``k``
    is short where the ground is paved and drained and long where it is a flat
    clay basin, and is stretched further by high susceptibility — a cell with
    nowhere to drain to has, by definition, a long residence time.

    This is the number that decides how long a relief camp stays open, which is
    why it is reported per hotspot rather than as a raster alone.
    """
    k = K_RURAL_HOURS + (K_URBAN_HOURS - K_RURAL_HOURS) * np.clip(exposure.built_up, 0, 1)
    k = k * (0.55 + 1.35 * susceptibility)
    d0 = np.maximum(standing_depth, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        hours = np.where(d0 > MIN_DEPTH_M,
                         k * np.log(np.maximum(d0, 1e-6) / MIN_DEPTH_M), 0.0)
    return np.clip(np.nan_to_num(hours), 0, 24 * 60).astype(np.float32)


def _dominant_drivers(parts: Dict[str, np.ndarray], sel: np.ndarray,
                      top: int = 2) -> List[str]:
    """Which controls make this place a trap — the 'why', not just the score."""
    pretty = {
        "wetness": "converging drainage onto flat ground",
        "low_lying": "low-lying, no fall to any channel",
        "impervious": "paved surface, no infiltration",
        "poor_drainage": "few drainage channels nearby",
    }
    contrib = {k: float(WEIGHTS[k] * v[sel].mean()) for k, v in parts.items()}
    ranked = sorted(contrib, key=lambda k: -contrib[k])[:top]
    return [pretty[k] for k in ranked]


def find_hotspots(grid: Grid, terrain: Terrain, exposure: Exposure,
                  susceptibility: np.ndarray, drain_hours: np.ndarray,
                  standing_depth: np.ndarray, hq_lat: float, hq_lon: float,
                  hq_name: str, parts: Dict[str, np.ndarray],
                  count: int = 8, min_sep_cells: Optional[int] = None) -> List[WaterloggingHotspot]:
    """Pick separated local maxima of susceptibility that also hold water."""
    if min_sep_cells is None:
        min_sep_cells = max(8, grid.n // 12)

    # Rank on susceptibility weighted by how much water is actually standing —
    # a theoretically bad spot with nothing in it is not today's problem.
    score = susceptibility * (0.35 + 0.65 * normalize(np.minimum(standing_depth, 2.0)))
    score = np.where(exposure.district_mask, score, 0.0)

    picked: List[Tuple[int, int]] = []
    for flat in np.argsort(score.ravel())[::-1]:
        r, c = divmod(int(flat), grid.n)
        if score[r, c] <= 0:
            break
        if all(math.hypot(r - rr, c - cc) >= min_sep_cells for rr, cc in picked):
            picked.append((r, c))
        if len(picked) >= count:
            break

    out: List[WaterloggingHotspot] = []
    half = max(min_sep_cells // 2, 2)
    for i, (r, c) in enumerate(picked):
        r0, r1 = max(0, r - half), min(grid.n, r + half + 1)
        c0, c1 = max(0, c - half), min(grid.n, c + half + 1)
        sel = np.zeros(susceptibility.shape, bool)
        sel[r0:r1, c0:c1] = True
        sel &= exposure.district_mask & (susceptibility >= 0.5)
        if not sel.any():
            sel = np.zeros(susceptibility.shape, bool)
            sel[r, c] = True
        lat, lon = grid.latlon(r, c)
        out.append(WaterloggingHotspot(
            id="W%02d" % i,
            label=describe_location(grid, r, c, hq_lat, hq_lon, hq_name),
            lat=lat, lon=lon,
            susceptibility=float(susceptibility[r, c]),
            area_km2=float(sel.sum()) * grid.cell_area_km2,
            population=int(round(float(exposure.population[sel].sum()))),
            drain_down_hours=float(np.median(drain_hours[sel])),
            standing_depth_m=float(np.median(standing_depth[sel])),
            drivers=_dominant_drivers(parts, sel),
        ))
    out.sort(key=lambda h: -(h.population * h.drain_down_hours))
    return out


def analyse(grid: Grid, terrain: Terrain, exposure: Exposure,
            timeline: Optional[FloodTimeline], hq_lat: float, hq_lon: float,
            hq_name: str) -> Waterlogging:
    """Full waterlogging assessment.

    Works with or without a flood event. Without one it reports the standing
    susceptibility map — the peacetime planning product. With one it also
    reports how long the water that fell will actually take to clear.
    """
    s, parts = susceptibility_grid(terrain, exposure)
    if timeline is not None:
        standing = timeline.depth[-1].copy()      # what is left at the end
        residual = timeline.peak_depth * 0.30 * s
        standing = np.maximum(standing, residual)
    else:
        standing = s * 0.45                        # notional design ponding
    hours = drain_down(terrain, exposure, s, standing)
    spots = find_hotspots(grid, terrain, exposure, s, hours, standing,
                          hq_lat, hq_lon, hq_name, parts)
    return Waterlogging(susceptibility=s, drain_down_hours=hours,
                        hotspots=spots, weights=dict(WEIGHTS))
