"""Inundation: river stage and sea level into depth on the ground.

The model is HAND-based. Every cell knows its height above the drainage cell it
drains into (``terrain.hand``) and which cell that is (``terrain.nearest``), so
the depth of water standing on it is

    depth = stage at its own outlet  -  its height above that outlet

This is the operational form of the method behind FwDET (Cohen et al., 2018) and
the reason HAND is worth computing: inundation becomes a subtraction rather than
a hydrodynamic solve, one river can be in flood while the next is not, and the
result stays physically anchored to the terrain.

What this deliberately does not do is conserve volume or model momentum. A cell
floods when its own outlet rises past it, which can wet a pocket that in reality
is protected by an embankment the DEM does not resolve. Stated in
docs/ASSUMPTIONS.md; the mitigation is that embankments, once digitised, enter as
a barrier mask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .grid import Grid
from .hydrology import Hydrograph
from .terrain import Terrain

# Depth bands that change what a responder actually does. These are the
# thresholds the action playbook keys on, so they live here, once.
DEPTH_BANDS: Tuple[Tuple[float, str, str], ...] = (
    (0.15, "nuisance", "Standing water. Roads passable with care."),
    (0.50, "wadeable", "Knee deep. Passable on foot; small vehicles stalling."),
    (1.00, "impassable", "Waist deep. Not passable on foot. Light vehicles lost."),
    (2.00, "boat-only", "Above head height. Boat access only."),
    (99.0, "severe", "Deep inundation. Structural damage likely."),
)

# Below this, water is not treated as flooding at all — it is wet ground. Without
# a floor the model reports 90% of a delta as "flooded" at 2 cm and the number
# stops meaning anything.
MIN_DEPTH_M = 0.15


def band_for(depth_m: float) -> str:
    for limit, name, _ in DEPTH_BANDS:
        if depth_m < limit:
            return name
    return DEPTH_BANDS[-1][1]


def inundation_depth(terrain: Terrain, stage: np.ndarray) -> np.ndarray:
    """Water depth (m) implied by a stage field on the drainage network.

    ``stage`` is metres above the channel bed at every stream cell. Each land
    cell reads the stage at its own outlet and subtracts its HAND.
    """
    n = terrain.grid.n
    flat_stage = stage.ravel()
    nearest = terrain.nearest.ravel()
    ref = np.where(nearest >= 0, flat_stage[np.maximum(nearest, 0)], 0.0)
    depth = ref.reshape(n, n) - terrain.hand
    depth = np.where(nearest.reshape(n, n) >= 0, depth, 0.0)
    return np.maximum(depth, 0.0).astype(np.float32)


def surge_reach(terrain: Terrain, surge_m: float) -> np.ndarray:
    """Cells the sea can actually reach when it rises to ``surge_m``.

    Surge floods by absolute elevation rather than by river stage — seawater
    does not care which catchment a field belongs to. But elevation alone is not
    enough: an inland hollow sitting below the surge height is only flooded if
    water has a path to it. Without the connectivity test the model wets every
    low pocket in the district the moment the sea rises, which is both wrong and
    obviously wrong on the map.
    """
    if surge_m <= 0 or not terrain.sea_mask.any():
        return np.zeros(terrain.dem.shape, bool)

    passable = (terrain.dem <= surge_m) | terrain.sea_mask
    reach = terrain.sea_mask.copy()
    for _ in range(terrain.grid.n * 2):
        grown = reach.copy()
        grown[:-1, :] |= reach[1:, :]
        grown[1:, :] |= reach[:-1, :]
        grown[:, :-1] |= reach[:, 1:]
        grown[:, 1:] |= reach[:, :-1]
        grown &= passable
        if grown.sum() == reach.sum():
            break
        reach = grown
    return reach & ~terrain.sea_mask


def surge_depth(terrain: Terrain, surge_m: float,
                reach: Optional[np.ndarray] = None) -> np.ndarray:
    """Inundation depth from a sea level of ``surge_m``.

    ``reach`` may be supplied to skip the flood fill. The reachable set is
    monotonic in surge height, so the set computed at the peak is a valid
    superset for every earlier and later hour — which turns a fill per timestep
    into one fill per event.
    """
    if surge_m <= 0 or not terrain.sea_mask.any():
        return np.zeros(terrain.dem.shape, np.float32)
    if reach is None:
        reach = surge_reach(terrain, surge_m)
    depth = np.where(reach, surge_m - terrain.dem, 0.0)
    return np.maximum(depth, 0.0).astype(np.float32)


def surge_curve(hours: int, peak_hour: int, peak_m: float,
                width_hours: float = 8.0) -> np.ndarray:
    """Storm-surge height through time — a short, sharp pulse at landfall."""
    t = np.arange(hours, dtype=np.float64)
    return (peak_m * np.exp(-0.5 * ((t - peak_hour) / max(width_hours, 1.0)) ** 2)
            ).astype(np.float32)


@dataclass
class FloodTimeline:
    """Inundation through the whole event."""
    hours: int
    depth: np.ndarray                 # (T, n, n) metres
    peak_hour: int
    peak_depth: np.ndarray            # (n, n) maximum depth reached
    duration_hours: np.ndarray        # (n, n) hours above MIN_DEPTH_M
    area_km2: np.ndarray              # (T,) flooded area per hour
    cell_area_km2: float

    def extent(self, t: int) -> np.ndarray:
        return self.depth[int(np.clip(t, 0, self.hours - 1))] >= MIN_DEPTH_M

    @property
    def peak_extent(self) -> np.ndarray:
        return self.peak_depth >= MIN_DEPTH_M

    @property
    def peak_area_km2(self) -> float:
        return float(self.area_km2.max())

    def band_areas(self) -> Dict[str, float]:
        """Flooded area by depth band at the peak, in km^2."""
        out: Dict[str, float] = {}
        # Start below the flood threshold so the sub-threshold band reports the
        # merely-wet ground rather than an empty range. Anything under 2 cm is
        # numerical noise, not water.
        lo = 0.02
        for limit, name, _ in DEPTH_BANDS:
            sel = (self.peak_depth >= lo) & (self.peak_depth < limit)
            out[name] = round(float(sel.sum()) * self.cell_area_km2, 2)
            lo = limit
        return out

    def summary(self) -> dict:
        wet = self.peak_extent
        return {
            "peak_hour": self.peak_hour,
            "peak_flooded_area_km2": round(self.peak_area_km2, 1),
            "max_depth_m": round(float(self.peak_depth.max()), 2),
            "mean_depth_m": round(float(self.peak_depth[wet].mean()), 2) if wet.any() else 0.0,
            "longest_inundation_hours": int(self.duration_hours.max()),
            "area_by_band_km2": self.band_areas(),
        }


def build_timeline(terrain: Terrain, hydro: Hydrograph,
                   surge_peak_m: float = 0.0) -> FloodTimeline:
    """Turn a hydrograph (and optional surge) into an inundation time series."""
    n = terrain.grid.n
    cell_km2 = terrain.grid.cell_area_km2
    depth = np.zeros((hydro.hours, n, n), np.float32)

    surge = (surge_curve(hydro.hours, hydro.peak_hour, surge_peak_m)
             if surge_peak_m > 0 else None)
    # One fill at peak level, reused for every hour (see surge_depth).
    reach = surge_reach(terrain, surge_peak_m) if surge is not None else None

    for t in range(hydro.hours):
        d = inundation_depth(terrain, hydro.stage[t])
        if surge is not None and surge[t] > 0.05:
            # Surge and riverine flooding are not additive — the deeper of the
            # two governs. Adding them would double-count the same water.
            d = np.maximum(d, surge_depth(terrain, float(surge[t]), reach))
        depth[t] = d

    wet = depth >= MIN_DEPTH_M
    area = wet.reshape(hydro.hours, -1).sum(axis=1) * cell_km2
    peak_depth = depth.max(axis=0)

    return FloodTimeline(
        hours=hydro.hours,
        depth=depth,
        peak_hour=int(np.argmax(area)),
        peak_depth=peak_depth,
        duration_hours=wet.sum(axis=0).astype(np.int16),
        area_km2=area.astype(np.float32),
        cell_area_km2=cell_km2,
    )
