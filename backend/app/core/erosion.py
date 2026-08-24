"""Coastal erosion: land being permanently lost to the sea.

Erosion belongs in a relocation assessment in a way it does not belong in a flood
response tool. A flooded village is a village you evacuate and return to. An
eroding shoreline is land that will not exist in twenty years, and the only
mitigation is to move — which is exactly the decision this system is being asked
to support.

The retreat estimate follows the **Bruun rule** (Bruun, 1962), the standard
first-order relation between sea-level rise and shoreline recession:

    R = S / tan(beta)

where ``S`` is the rate of relative sea-level rise and ``tan(beta)`` is the
nearshore profile slope. Gentle profiles retreat far more for the same rise,
which is why the deltaic coasts of Odisha and the Sundarbans lose land fastest.

Three modifiers are applied on top, each for a documented physical reason:

* **Wave exposure** — a shore facing the open Bay of Bengal takes the full
  monsoon and cyclone wave climate; a sheltered creek does not.
* **Sediment supply** — coasts near an active river mouth are nourished and may
  accrete; those starved of sediment erode. Damming of Indian rivers has cut
  sediment delivery sharply and is a principal driver of erosion on this coast.
* **Natural protection** — mangrove and dense coastal vegetation dissipate wave
  energy and hold sediment.

The National Centre for Coastal Research reports roughly a third of the Indian
mainland coastline as eroding; a district assessment that returns nothing
anywhere should be treated as a bug, not as good news.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from .exposure import Exposure
from .grid import Grid
from .terrain import COAST_BEARING, Terrain, normalize, smooth

# Relative sea-level rise, metres per year. IPCC AR6 north Indian Ocean rates
# are 3.5-4 mm/yr; deltaic districts add subsidence on top.
SLR_M_PER_YEAR = 0.0038
SUBSIDENCE_M_PER_YEAR = {"deltaic": 0.0025, "default": 0.0005}

# Bruun retreat is unbounded as the profile flattens; cap at a rate beyond which
# the relation is no longer physically meaningful.
MAX_RETREAT_M_PER_YEAR = 12.0


def shoreline(terrain: Terrain) -> np.ndarray:
    """Land cells directly adjacent to the sea."""
    sea = terrain.sea_mask
    if not sea.any():
        return np.zeros(sea.shape, bool)
    nbr = np.zeros(sea.shape, bool)
    nbr[:-1, :] |= sea[1:, :]
    nbr[1:, :] |= sea[:-1, :]
    nbr[:, :-1] |= sea[:, 1:]
    nbr[:, 1:] |= sea[:, :-1]
    return nbr & ~sea


def distance_to_sea(terrain: Terrain, max_cells: int = 60) -> np.ndarray:
    """Cell distance inland from the shoreline, by iterative dilation."""
    sea = terrain.sea_mask
    out = np.full(sea.shape, np.inf, np.float32)
    if not sea.any():
        return out
    front = sea.copy()
    out[sea] = 0.0
    for d in range(1, max_cells + 1):
        grown = front.copy()
        grown[:-1, :] |= front[1:, :]
        grown[1:, :] |= front[:-1, :]
        grown[:, :-1] |= front[:, 1:]
        grown[:, 1:] |= front[:, :-1]
        new = grown & ~front
        if not new.any():
            break
        out[new] = float(d)
        front = grown
    return out


def nearshore_slope(terrain: Terrain, band_cells: int = 6) -> np.ndarray:
    """Profile slope across the shore zone, as tan(beta).

    Measured over a band rather than cell-to-cell: the Bruun rule refers to the
    whole active profile, and a single-cell gradient on a near-flat delta is
    dominated by DEM noise.
    """
    dist = distance_to_sea(terrain, max_cells=band_cells + 2)
    band = np.isfinite(dist) & (dist <= band_cells) & ~terrain.sea_mask
    elev = np.where(band, terrain.dem, np.nan)
    run_m = max(band_cells, 1) * terrain.grid.cell_m
    rise = smooth(np.nan_to_num(elev, nan=0.0), radius=3)
    tanb = np.clip(rise / run_m, 0.0008, 0.15)
    return tanb.astype(np.float32)


def wave_exposure(terrain: Terrain, code: str) -> np.ndarray:
    """How much open-ocean wave energy each shore cell receives, 0..1.

    Shores whose seaward aspect aligns with the dominant swell direction take
    the most energy; those turned away or tucked inside a creek take less.
    """
    bearing = math.radians(COAST_BEARING.get(code, 95.0))
    aspect = np.radians(terrain.aspect)
    align = np.cos(aspect - bearing)
    return np.clip((align + 1.0) / 2.0, 0, 1).astype(np.float32)


def sediment_supply(terrain: Terrain) -> np.ndarray:
    """Proximity to an active river mouth, 0..1.

    River mouths deliver sediment that nourishes the adjacent coast. Away from
    them the shore is sediment-starved and erodes.
    """
    mouths = terrain.streams & ~terrain.sea_mask
    big = mouths & (terrain.accum >= np.percentile(terrain.accum, 99.0))
    if not big.any():
        return np.zeros(terrain.dem.shape, np.float32)
    return normalize(smooth(big.astype(np.float32), radius=8))


@dataclass
class CoastalErosion:
    retreat_m_per_year: np.ndarray
    shoreline: np.ndarray
    land_lost: np.ndarray           # bool, land expected to be gone within the horizon
    applicable: bool
    horizon_years: int = 50

    def summary(self, cell_km2: float, cell_m: float) -> dict:
        if not self.applicable:
            return {"applicable": False,
                    "reason": "inland district; no coastline in the analysis frame"}
        shore = self.shoreline
        rates = self.retreat_m_per_year[shore]
        return {
            "applicable": True,
            "method": "Bruun (1962) shoreline retreat, wave- and sediment-modulated",
            "sea_level_rise_m_per_year": SLR_M_PER_YEAR,
            "shoreline_cells": int(shore.sum()),
            "shoreline_length_km": round(float(shore.sum()) * cell_m / 1000.0, 1),
            "mean_retreat_m_per_year": round(float(rates.mean()), 2) if rates.size else 0.0,
            "max_retreat_m_per_year": round(float(rates.max()), 2) if rates.size else 0.0,
            "eroding_fraction_of_shore": round(
                float((rates >= 1.5).mean()), 3) if rates.size else 0.0,
            "projection_horizon_years": self.horizon_years,
            "land_lost_in_horizon_km2": round(
                float(self.land_lost.sum()) * cell_km2, 3),
            "resolution_note": (
                "At %d m cell size, a few hundred metres of retreat is sub-pixel. "
                "The shoreline retreat RATE is the meaningful output here; the "
                "area figure is resolution-limited." % round(cell_m)),
        }


def assess(grid: Grid, terrain: Terrain, code: str,
           deltaic: bool = False, horizon_years: int = 50) -> CoastalErosion:
    """Shoreline retreat rate and the land expected to be lost."""
    shore = shoreline(terrain)
    if not shore.any():
        z = np.zeros(terrain.dem.shape, np.float32)
        return CoastalErosion(retreat_m_per_year=z, shoreline=shore,
                              land_lost=np.zeros(z.shape, bool),
                              applicable=False, horizon_years=horizon_years)

    slr = SLR_M_PER_YEAR + SUBSIDENCE_M_PER_YEAR[
        "deltaic" if deltaic else "default"]
    tanb = nearshore_slope(terrain)
    bruun = slr / np.maximum(tanb, 1e-4)

    expo = wave_exposure(terrain, code)
    sed = sediment_supply(terrain)
    # Wave energy roughly doubles the rate at full exposure; sediment supply can
    # cancel it entirely where a river is actively building the coast.
    rate = bruun * (0.55 + 0.90 * expo) * (1.0 - 0.75 * sed)
    rate = np.clip(rate, 0.0, MAX_RETREAT_M_PER_YEAR).astype(np.float32)

    # Land lost is the band within the projected retreat distance of the shore.
    dist_cells = distance_to_sea(terrain)
    dist_m = dist_cells * grid.cell_m
    shore_rate = float(np.median(rate[shore])) if shore.any() else 0.0
    lost = (np.isfinite(dist_m) & ~terrain.sea_mask
            & (dist_m <= shore_rate * horizon_years))

    # The rate is a property of the shore, not of the coastal plain. Painting it
    # across a wide inland band made erosion the dominant hazard over 1,800 km2
    # of Puri, which is nonsense: erosion takes a strip, not a district. Only
    # cells actually within the projected retreat distance carry a rate.
    band = np.isfinite(dist_cells) & (dist_cells <= 3)
    rate = np.where(band | lost, rate, 0.0)
    return CoastalErosion(retreat_m_per_year=rate.astype(np.float32),
                          shoreline=shore, land_lost=lost,
                          applicable=True, horizon_years=horizon_years)
