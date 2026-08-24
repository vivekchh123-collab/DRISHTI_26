"""Cloudburst hazard.

IMD defines a cloudburst as **100 mm or more of rainfall in one hour over a
small area**. The problem statement names it alongside landslides, floods and
coastal erosion, and it behaves like none of them: the rain falls faster than any
drainage can carry it, over a catchment small enough that the whole basin
responds at once, and the flood arrives in minutes rather than hours.

Two conditions have to coincide, and modelling only one of them is the usual
mistake:

**The atmosphere has to deliver it.** Cloudbursts over India are overwhelmingly
orographic — monsoon moisture forced up a windward slope until it convects
violently. That is why they concentrate in the Himalaya and the Western Ghats
between roughly 1,000 and 3,000 m, and why the plains almost never see one.
Convective available potential energy is the measured quantity, and
:mod:`app.providers.openmeteo` already fetches it live.

**The ground has to convert it into a disaster.** A cloudburst over a broad flat
basin is a very wet afternoon. The same rain over a small, steep, convergent
catchment produces a debris flow that arrives with no warning — Leh 2010,
Kedarnath 2013, the Uttarakhand cloudbursts of October 2021. The terrain terms
below are what separate those two outcomes.

The hazard is therefore **catchment-scale, not cell-scale**: what matters is not
whether one cell is steep but whether the small basin draining through it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from .grid import Grid
from .terrain import Terrain, normalize, smooth

# IMD's definition. Anything at or above this in an hour is a cloudburst.
IMD_CLOUDBURST_MM_HR = 100.0
# IMD also recognises a lesser category that still produces flash response.
IMD_HEAVY_MM_HR = 50.0

# Elevation band where orographic convection concentrates over India. Below the
# lower bound there is no lifting; above the upper bound there is little
# moisture left to lift.
OROGRAPHIC_BAND_M = (800.0, 3200.0)

# Catchment area above which a basin no longer responds as a single flashy unit,
# in square kilometres. Beyond this the flood peak is attenuated by routing and
# the event behaves like an ordinary flood.
FLASH_CATCHMENT_KM2 = 60.0

# CAPE, in J/kg. Below the first value the atmosphere is stable; above the
# second it is capable of violent convection.
CAPE_STABLE = 800.0
CAPE_VIOLENT = 2800.0

# Design cloudburst intensity by return period, mm/hr.
#
# A cloudburst is its own event, not the peak hour of a monsoon storm, and
# conflating the two is an easy mistake to make: a 96-hour design storm spreads
# its rainfall over days and peaks around 10 mm/hr, so reading its maximum hour
# gives a cloudburst hazard of exactly zero everywhere — which is what this
# model first produced. The correct question is not "how hard does the monsoon
# storm rain" but "if a cloudburst occurs over this terrain, which it does on
# roughly this cadence in the Himalaya and the Ghats, what happens".
#
# The 25-year value is IMD's definitional 100 mm/hr; the others bracket it.
DESIGN_CLOUDBURST_MM_HR = {
    "moderate": 60.0,     # ~5-year: very heavy, short of the definition
    "severe": 100.0,      # ~25-year: at the IMD cloudburst threshold
    "extreme": 150.0,     # ~100-year: Leh 2010 / Kedarnath 2013 class
}


@dataclass
class CloudburstHazard:
    susceptibility: np.ndarray      # 0..1, standing terrain property
    initiation: np.ndarray          # bool, cells where a burst would convert
    affected: np.ndarray            # bool, initiation plus the flash path
    orographic: np.ndarray          # 0..1 component
    flashiness: np.ndarray          # 0..1 component
    trigger: dict
    applicable: bool

    def summary(self, cell_km2: float) -> dict:
        if not self.applicable:
            return {
                "applicable": False,
                "reason": ("No terrain in the orographic band; cloudbursts over "
                           "India are an orographic phenomenon and this district "
                           "has no slopes to lift moisture over."),
                "definition_mm_hr": IMD_CLOUDBURST_MM_HR,
            }
        return {
            "applicable": True,
            "definition": ("IMD: 100 mm or more of rainfall in one hour over a "
                           "small area"),
            "definition_mm_hr": IMD_CLOUDBURST_MM_HR,
            "mean_susceptibility": round(float(self.susceptibility.mean()), 3),
            "high_susceptibility_km2": round(
                float((self.susceptibility >= 0.6).sum()) * cell_km2, 2),
            "initiation_cells": int(self.initiation.sum()),
            "affected_cells": int(self.affected.sum()),
            "affected_km2": round(float(self.affected.sum()) * cell_km2, 2),
            "trigger": self.trigger,
            "method": ("Orographic convective potential x catchment flashiness. "
                       "A cloudburst over a broad flat basin is a wet "
                       "afternoon; over a small steep convergent catchment it "
                       "is a debris flow."),
        }


def orographic_potential(terrain: Terrain) -> np.ndarray:
    """How readily this terrain lifts monsoon moisture into violent convection.

    Peaks in the mid-elevation band on slopes that face the monsoon, and falls
    away both on the plains, where there is no lift, and on the high plateau,
    where the air arriving has already been wrung out.
    """
    lo, hi = OROGRAPHIC_BAND_M
    mid = 0.5 * (lo + hi)
    band = np.exp(-0.5 * ((terrain.dem - mid) / (0.42 * (hi - lo))) ** 2)
    band = np.where(terrain.dem < lo * 0.5, 0.0, band)

    # Lift is produced by the gradient, not the height. A steep windward face
    # generates far more uplift than a plateau at the same elevation.
    gradient = normalize(np.clip(terrain.slope, 0, 35))
    return np.clip(smooth(band * (0.35 + 0.85 * gradient), radius=2),
                   0, 1).astype(np.float32)


def catchment_flashiness(terrain: Terrain, grid: Grid) -> np.ndarray:
    """How violently the basin draining through each cell converts rain to flood.

    Small, steep and convergent scores high. Large or gentle scores low: the
    same rainfall on a big basin arrives spread over hours instead of minutes,
    which is the difference between a nuisance and Kedarnath.
    """
    area_km2 = terrain.accum * grid.cell_area_km2
    # Small basins are flashy; the response decays as area grows past the
    # threshold beyond which routing attenuates the peak.
    small = np.exp(-area_km2 / FLASH_CATCHMENT_KM2)
    # Below a handful of cells there is no catchment to speak of.
    small = np.where(terrain.accum < 4, small * 0.3, small)

    steep = normalize(np.clip(terrain.hillslope, 0, 40))
    # Concave ground concentrates flow; curvature is negative where it does.
    convergent = normalize(np.clip(-terrain.curvature, 0, 40))

    out = 0.45 * small + 0.35 * steep + 0.20 * convergent
    return np.clip(smooth(out.astype(np.float32), radius=1), 0, 1)


def trigger_state(rainfall_mm_hr: Optional[float],
                  cape_j_kg: Optional[float]) -> dict:
    """Whether the atmosphere is delivering, and how hard.

    Reported even when it is not, because "the terrain is dangerous but the sky
    is quiet today" is a useful thing for a planner to be told explicitly.
    """
    peak = float(rainfall_mm_hr or 0.0)
    cape = cape_j_kg

    if peak >= IMD_CLOUDBURST_MM_HR:
        band, severity = "cloudburst", 1.0
    elif peak >= IMD_HEAVY_MM_HR:
        band, severity = "very heavy", 0.6
    elif peak >= 20.0:
        band, severity = "heavy", 0.3
    else:
        band, severity = "ordinary", 0.0

    conv = None
    if cape is not None:
        conv = float(np.clip((cape - CAPE_STABLE)
                             / max(CAPE_VIOLENT - CAPE_STABLE, 1.0), 0.0, 1.0))

    return {
        "peak_rainfall_mm_hr": round(peak, 1),
        "imd_band": band,
        "meets_imd_cloudburst_definition": peak >= IMD_CLOUDBURST_MM_HR,
        "severity": severity,
        "cape_j_kg": None if cape is None else round(float(cape), 0),
        "convective_potential": None if conv is None else round(conv, 3),
        "source": ("IMD cloudburst definition (>=100 mm/hr); convective "
                   "potential from CAPE where a live feed is available"),
    }


def assess(grid: Grid, terrain: Terrain,
           rainfall_mm_hr: Optional[float] = None,
           cape_j_kg: Optional[float] = None) -> CloudburstHazard:
    """Full cloudburst assessment for a district."""
    oro = orographic_potential(terrain)
    flash = catchment_flashiness(terrain, grid)

    lo, _ = OROGRAPHIC_BAND_M
    applicable = bool((terrain.dem >= lo * 0.5).any()) and float(oro.max()) > 0.05
    if not applicable:
        z = np.zeros(terrain.dem.shape, np.float32)
        return CloudburstHazard(
            susceptibility=z, initiation=z.astype(bool), affected=z.astype(bool),
            orographic=z, flashiness=flash,
            trigger=trigger_state(rainfall_mm_hr, cape_j_kg), applicable=False)

    susceptibility = np.clip(0.55 * oro + 0.45 * flash, 0, 1).astype(np.float32)

    trig = trigger_state(rainfall_mm_hr, cape_j_kg)
    severity = trig["severity"]
    if trig["convective_potential"] is not None:
        # A live CAPE reading can raise a marginal rainfall band, but never
        # manufacture an event on its own — unstable air with no rain is a
        # thunderstorm watch, not a cloudburst.
        severity = min(1.0, severity * (0.7 + 0.6 * trig["convective_potential"]))

    if severity <= 0.0:
        init = np.zeros(susceptibility.shape, bool)
    else:
        # The more violent the delivery, the further down the susceptibility
        # distribution failure reaches.
        cut = float(np.percentile(susceptibility, 100.0 - 4.0 * severity))
        init = susceptibility >= max(cut, 0.55)

    affected = flash_path(init, terrain) if init.any() else init
    return CloudburstHazard(
        susceptibility=susceptibility, initiation=init, affected=affected,
        orographic=oro, flashiness=flash, trigger=trig, applicable=True)


def flash_path(source: np.ndarray, terrain: Terrain,
               reach_cells: int = 25) -> np.ndarray:
    """Propagate the flood downstream along the drainage network.

    A cloudburst kills people downstream of where it falls, often several
    kilometres away and in a valley that saw no rain at all. Kedarnath is the
    canonical case. Reach is longer than a landslide runout because water keeps
    going where debris stops.
    """
    n = terrain.grid.n
    out = source.copy().ravel()
    recv = terrain.net.receiver
    front = list(np.flatnonzero(source.ravel()))
    for _ in range(reach_cells):
        nxt = []
        for i in front:
            j = recv[i]
            if j >= 0 and not out[j]:
                out[j] = True
                nxt.append(j)
        front = nxt
        if not front:
            break
    return out.reshape(n, n)
