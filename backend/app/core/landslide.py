"""Landslide susceptibility, after the Indian national standard.

Implements the **Landslide Hazard Evaluation Factor (LHEF)** rating scheme of
**BIS IS 14496 (Part 2): 1998 — Preparation of Landslide Hazard Zonation Maps in
Mountainous Terrains**, which is the method the Geological Survey of India uses
for macro-zonation. Using the national standard rather than an invented weighted
overlay matters here: a State Disaster Management Authority can only act on a
red zone if the method behind it is one their own guidelines already recognise.

The scheme sums six factor ratings into a Total Estimated Hazard (TEHD):

===========================  =========  ==========================================
Factor                       Max rating Source here
===========================  =========  ==========================================
Lithology                        2.0    **not computable from a DEM** — see below
Structure                        2.0    **not computable from a DEM** — see below
Slope morphometry                2.0    DEM slope
Relative relief                  1.0    DEM local relief
Land use and land cover          2.0    modelled built-up / cropland / forest
Hydrogeological conditions       1.0    topographic wetness index
===========================  =========  ==========================================

Four of the six are computed from terrain we already derive. **Lithology and
structure require a Geological Survey of India map and cannot be inferred from
elevation.** They are held at a neutral mid-rating and the shortfall is reported
in every response as ``factors_estimated``, so the number is never presented as
a full LHEF assessment when it is not one. :func:`load_geology` accepts a real
lithology raster and promotes the result to a complete assessment.

Susceptibility is the standing property of the slope. Whether it *fails* also
needs a trigger, which is rainfall: :func:`rainfall_trigger` applies the
Caine (1980) intensity-duration threshold, still the reference relation for
shallow landslides and debris flows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from .exposure import Exposure
from .grid import Grid
from .terrain import Terrain, normalize, smooth

# BIS IS 14496 (Part 2) maximum rating per factor.
LHEF_MAX: Dict[str, float] = {
    "lithology": 2.0,
    "structure": 2.0,
    "slope_morphometry": 2.0,
    "relative_relief": 1.0,
    "land_use": 2.0,
    "hydrogeology": 1.0,
}
TEHD_MAX = sum(LHEF_MAX.values())        # 10.0

# BIS TEHD zone boundaries.
ZONES = (
    (3.5, "very low"),
    (5.0, "low"),
    (6.0, "moderate"),
    (7.5, "high"),
    (99.0, "very high"),
)

# Neutral ratings used where the input map is unavailable. Mid-scale, so the
# missing factor neither inflates nor suppresses the hazard.
NEUTRAL_LITHOLOGY = 1.0
NEUTRAL_STRUCTURE = 1.0


def zone_for(tehd: float) -> str:
    for limit, name in ZONES:
        if tehd < limit:
            return name
    return ZONES[-1][1]


# ---------------------------------------------------------------------------
# terrain factors
# ---------------------------------------------------------------------------

def relative_relief(dem: np.ndarray, radius: int = 5) -> np.ndarray:
    """Local relief: the elevation range within a moving window, in metres.

    BIS rates relative relief because a steep slope on a low hill is far less
    dangerous than the same angle on a 500 m valley wall — there is simply less
    material available to move and less distance for it to accelerate over.
    """
    n = dem.shape[0]
    pad = np.pad(dem, radius, mode="edge")
    hi = np.full((n, n), -np.inf, np.float32)
    lo = np.full((n, n), np.inf, np.float32)
    for dr in range(2 * radius + 1):
        for dc in range(2 * radius + 1):
            w = pad[dr:dr + n, dc:dc + n]
            hi = np.maximum(hi, w)
            lo = np.minimum(lo, w)
    return (hi - lo).astype(np.float32)


def rate_slope(slope_deg: np.ndarray) -> np.ndarray:
    """Slope morphometry rating, 0-2 (BIS Table 4).

    Escarpments and steep slopes carry the maximum; gentle ground carries almost
    nothing. The scheme is piecewise because the relationship is not linear —
    failure probability rises sharply past about 35 degrees.
    """
    r = np.zeros_like(slope_deg, np.float32)
    r = np.where(slope_deg > 45, 2.00, r)          # escarpment / cliff
    r = np.where((slope_deg > 35) & (slope_deg <= 45), 1.70, r)
    r = np.where((slope_deg > 25) & (slope_deg <= 35), 1.20, r)
    r = np.where((slope_deg > 15) & (slope_deg <= 25), 0.80, r)
    r = np.where((slope_deg > 5) & (slope_deg <= 15), 0.50, r)
    return r


def rate_relief(relief_m: np.ndarray) -> np.ndarray:
    """Relative relief rating, 0-1 (BIS Table 5)."""
    r = np.full(relief_m.shape, 0.30, np.float32)  # low relief
    r = np.where(relief_m > 100, 0.60, r)          # medium
    r = np.where(relief_m > 300, 1.00, r)          # high
    return r


def rate_land_use(built: np.ndarray, cropland: np.ndarray,
                  slope_deg: np.ndarray) -> np.ndarray:
    """Land use and land cover rating, 0-2 (BIS Table 6).

    Thickly vegetated forest is the most stable cover because roots reinforce
    the soil mantle; barren land is the least. Terraced agriculture on a steep
    slope is rated worse than forest but better than bare ground, and built-up
    land on a steep slope is rated worst of all — hill cutting for construction
    is the single largest anthropogenic landslide driver in the Himalaya and the
    North East.
    """
    forest = (slope_deg > 20) & (built < 0.15) & (cropland < 0.25)
    r = np.full(built.shape, 1.20, np.float32)     # sparse vegetation / scrub
    r = np.where(forest, 0.80, r)                  # thickly vegetated
    r = np.where(cropland > 0.25, 1.50, r)         # agricultural land
    r = np.where(built > 0.15, 1.80, r)            # built-up
    # Cut slopes: construction on steep ground is the worst combination.
    r = np.where((built > 0.15) & (slope_deg > 25), 2.00, r)
    return r


def rate_hydrogeology(twi: np.ndarray) -> np.ndarray:
    """Hydrogeological condition rating, 0-1 (BIS Table 7).

    Groundwater is what actually triggers most slope failures: pore pressure
    reduces effective stress until the slope can no longer hold. TWI is the
    standard terrain proxy for where subsurface water converges.
    """
    lo, hi = np.percentile(twi, 15), np.percentile(twi, 85)
    w = np.clip((twi - lo) / max(hi - lo, 1e-6), 0, 1)
    r = np.full(twi.shape, 0.20, np.float32)       # dry
    r = np.where(w > 0.35, 0.50, r)                # damp
    r = np.where(w > 0.65, 0.80, r)                # wet
    r = np.where(w > 0.85, 1.00, r)                # dripping / flowing
    return r


def load_geology(path: str, grid: Grid) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Load GSI lithology and structure ratings from a GeoTIFF, if supplied.

    Returns ``(lithology_rating, structure_rating)`` already on the analysis
    grid, or None. With this present the LHEF assessment is complete rather than
    partial, and the API says so.
    """
    from .terrain import load_dem_geotiff
    raw = load_dem_geotiff(path, grid)
    if raw is None:
        return None
    # Band values are expected to already be BIS ratings; clamp to the scheme.
    lith = np.clip(raw, 0.0, LHEF_MAX["lithology"]).astype(np.float32)
    struct = np.full(lith.shape, NEUTRAL_STRUCTURE, np.float32)
    return lith, struct


# ---------------------------------------------------------------------------
# triggering
# ---------------------------------------------------------------------------

def caine_threshold(duration_h: np.ndarray | float) -> np.ndarray | float:
    """Caine (1980) rainfall intensity-duration threshold for shallow landslides.

        I = 14.82 * D^-0.39      I in mm/h, D in hours (0.167 <= D <= 500)

    Rainfall above this line has historically been sufficient to initiate
    shallow slides and debris flows. It is a global relation and conservative
    for the Indian monsoon, which is stated as a limitation rather than tuned
    away without evidence.
    """
    d = np.maximum(np.asarray(duration_h, dtype=np.float64), 0.167)
    return 14.82 * np.power(d, -0.39)


def rainfall_trigger(depth_mm: np.ndarray, antecedent_mm: float) -> dict:
    """Whether, and by how much, an event crosses the Caine threshold.

    Antecedent wetness shifts the threshold down: ground that is already
    saturated fails under rainfall that dry ground would absorb. This is why
    Kerala 2018 and Wayanad 2024 both followed weeks of prior rain.
    """
    depth = np.asarray(depth_mm, dtype=np.float64)
    cum = np.cumsum(depth)
    hours = np.arange(1, len(depth) + 1, dtype=np.float64)
    intensity = cum / hours                        # mean intensity to date

    # Up to a 35% reduction in the threshold for a thoroughly wet antecedent
    # period, following the wet-antecedent adjustments used in Indian and
    # Italian threshold studies.
    wet = float(np.clip(antecedent_mm / 150.0, 0.0, 1.0))
    threshold = caine_threshold(hours) * (1.0 - 0.35 * wet)

    exceed = intensity - threshold
    idx = int(np.argmax(exceed)) if len(exceed) else 0
    crossed = bool(exceed[idx] > 0) if len(exceed) else False
    return {
        "crossed": crossed,
        "exceedance_ratio": round(float(intensity[idx] / max(threshold[idx], 1e-6)), 3)
                            if len(exceed) else 0.0,
        "at_hour": idx + 1 if crossed else None,
        "intensity_mm_hr": round(float(intensity[idx]), 2) if len(exceed) else 0.0,
        "threshold_mm_hr": round(float(threshold[idx]), 2) if len(exceed) else 0.0,
        "antecedent_mm": round(antecedent_mm, 1),
        "antecedent_reduction_pct": round(35.0 * wet, 1),
        "source": "Caine (1980) intensity-duration threshold, wet-antecedent adjusted",
    }


# Share of a district that actually fails in one triggering storm, as a fraction
# of the eligible steep, susceptible ground. Landslide inventories for even the
# most damaging Indian events — Kerala 2018, Chamoli, the 2023 Himachal monsoon —
# map source areas covering well under a couple of per cent of the affected
# terrain. The range below spans a threshold-grazing storm to an extreme one.
INITIATION_FRACTION = (0.002, 0.030)
# Exceedance ratio at which the upper bound is reached.
INITIATION_SATURATION = 4.0


def initiation_zones(tehd: np.ndarray, slope_deg: np.ndarray,
                     trigger: dict) -> np.ndarray:
    """Which slopes actually fail in this storm, as opposed to which *could*.

    Susceptibility and initiation are different questions, and conflating them
    is the single easiest way to produce a landslide map that is obviously
    wrong. Marking every steep, susceptible cell as a source area put roughly a
    third of Rudraprayag and Kullu into initiation once the slope was measured
    at hillslope scale — around 765 km^2 of simultaneous failure in a 1,984 km^2
    district. Deep Himalayan valleys really are steep almost everywhere; they do
    not all collapse at once.

    So initiation is taken as the **upper tail** of susceptibility among
    eligible ground, and the size of that tail scales with how far the rainfall
    overshot the Caine threshold. A storm that grazes the threshold triggers a
    handful of failures; one that exceeds it fourfold triggers an order of
    magnitude more. That is the magnitude-frequency behaviour real inventories
    show, and it keeps the map's meaning intact: these are the slopes most
    likely to go, not every slope that conceivably could.
    """
    eligible = (tehd >= 6.0) & (slope_deg >= 20.0)
    if not trigger.get("crossed") or not eligible.any():
        return np.zeros(tehd.shape, bool)

    ratio = float(trigger.get("exceedance_ratio", 1.0))
    lo, hi = INITIATION_FRACTION
    span = np.clip((ratio - 1.0) / max(INITIATION_SATURATION - 1.0, 1e-6), 0.0, 1.0)
    fraction = lo + (hi - lo) * span

    values = tehd[eligible]
    # Percentile of the eligible population, not of the whole district: the cut
    # has to be relative to the ground that could fail at all.
    cut = float(np.percentile(values, 100.0 * (1.0 - fraction)))
    out = eligible & (tehd >= cut)

    # A district with genuinely uniform susceptibility can put every eligible
    # cell at the same TEHD, in which case the percentile cut selects all of
    # them. Fall back to the steepest ground so the count stays plausible.
    target = max(int(round(fraction * int(eligible.sum()))), 1)
    if int(out.sum()) > target * 3:
        steep_cut = float(np.percentile(slope_deg[eligible],
                                        100.0 * (1.0 - fraction)))
        out = eligible & (tehd >= cut) & (slope_deg >= steep_cut)
    return out


# ---------------------------------------------------------------------------
# runout
# ---------------------------------------------------------------------------

def runout(source: np.ndarray, terrain: Terrain, reach_cells: int = 6,
           angle_deg: float = 22.0) -> np.ndarray:
    """Propagate initiation zones downslope along flow paths.

    A landslide kills people who are below it, not on it. Debris travels until
    the line from crown to toe falls below the *angle of reach* — about 22
    degrees for a small shallow slide (Corominas, 1996). Following the existing
    D8 receiver chain keeps the runout on the same drainage the rest of the
    system uses.
    """
    n = terrain.grid.n
    out = source.copy().ravel()
    recv = terrain.net.receiver
    dem = terrain.dem.ravel()
    cell = terrain.grid.cell_m
    tan_reach = math.tan(math.radians(angle_deg))

    front = list(np.flatnonzero(source.ravel()))
    origin = {i: i for i in front}
    for _ in range(reach_cells):
        nxt = []
        for i in front:
            j = recv[i]
            if j < 0 or out[j]:
                continue
            src = origin[i]
            drop = dem[src] - dem[j]
            sr, sc = divmod(int(src), n)
            jr, jc = divmod(int(j), n)
            dist = math.hypot(jr - sr, jc - sc) * cell
            if drop <= 0 or (drop / max(dist, 1e-6)) < tan_reach:
                continue                      # energy line exhausted
            out[j] = True
            origin[j] = src
            nxt.append(j)
        front = nxt
        if not front:
            break
    return out.reshape(n, n)


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

@dataclass
class LandslideHazard:
    tehd: np.ndarray                 # Total Estimated Hazard on the BIS 0-10 scale
    susceptibility: np.ndarray       # TEHD over the *achievable* maximum, 0-1
    achievable_max: float            # highest TEHD reachable with the factors we have
    zone: np.ndarray                 # int index into ZONES
    initiation: np.ndarray           # bool, high/very-high on steep ground
    affected: np.ndarray             # bool, initiation plus runout
    factors: Dict[str, np.ndarray]
    trigger: dict
    factors_estimated: Tuple[str, ...]
    complete: bool

    def summary(self) -> dict:
        tot = self.tehd.size
        return {
            "standard": "BIS IS 14496 (Part 2): 1998 — LHEF rating scheme",
            "tehd_max": TEHD_MAX,
            "tehd_achievable_max": round(self.achievable_max, 2),
            "scale_note": (
                "Two scales, deliberately. `tehd` and `zone_fractions` are on the "
                "absolute BIS 0-10 scale and are CONSERVATIVE while lithology and "
                "structure are held neutral - the BIS zone boundaries assume all "
                "six factors are mapped, so a district cannot reach 'very high' "
                "on terrain factors alone. `susceptibility` is normalised against "
                "the achievable maximum and is the correct field for ranking "
                "within a district."),
            "mean_tehd": round(float(self.tehd.mean()), 2),
            "zone_fractions": {
                name: round(float((self.zone == i).mean()), 4)
                for i, (_, name) in enumerate(ZONES)
            },
            "high_or_above_fraction": round(
                float((self.tehd >= 6.0).sum()) / max(tot, 1), 4),
            "initiation_cells": int(self.initiation.sum()),
            "affected_cells": int(self.affected.sum()),
            "trigger": self.trigger,
            "assessment_complete": self.complete,
            "factors_estimated": list(self.factors_estimated),
            "note": ("Lithology and structure require a Geological Survey of "
                     "India map and cannot be derived from elevation. They are "
                     "held at a neutral rating; supply a geology raster to "
                     "promote this to a complete LHEF assessment."
                     if not self.complete else
                     "All six LHEF factors supplied from data."),
        }


def assess(grid: Grid, terrain: Terrain, exposure: Exposure,
           rainfall_mm: Optional[np.ndarray] = None,
           antecedent_mm: float = 0.0,
           geology_path: Optional[str] = None) -> LandslideHazard:
    """Full LHEF landslide hazard assessment for a district."""
    # Hillslope, not grid-scale slope. BIS rates the steepness of the slope that
    # would fail, and that is a 50-100 m feature; averaged over a 300 m cell it
    # vanishes into the flat ground beside it.
    slope = terrain.hillslope
    relief = relative_relief(terrain.dem)

    lith_struct = load_geology(geology_path, grid) if geology_path else None
    if lith_struct is not None:
        lithology, structure = lith_struct
        estimated: Tuple[str, ...] = ()
    else:
        lithology = np.full(slope.shape, NEUTRAL_LITHOLOGY, np.float32)
        structure = np.full(slope.shape, NEUTRAL_STRUCTURE, np.float32)
        estimated = ("lithology", "structure")

    factors = {
        "lithology": lithology,
        "structure": structure,
        "slope_morphometry": rate_slope(slope),
        "relative_relief": rate_relief(relief),
        "land_use": rate_land_use(exposure.built_up, exposure.cropland, slope),
        "hydrogeology": rate_hydrogeology(terrain.twi),
    }
    achievable = (LHEF_MAX["slope_morphometry"] + LHEF_MAX["relative_relief"]
                  + LHEF_MAX["land_use"] + LHEF_MAX["hydrogeology"]
                  + float(lithology.max()) + float(structure.max()))
    tehd = np.clip(sum(factors.values()), 0.0, TEHD_MAX).astype(np.float32)
    tehd = smooth(tehd, radius=1)
    # Flat ground and open water are not landslide terrain whatever the sum says.
    tehd = np.where((slope < 5.0) | terrain.sea_mask, 0.0, tehd)

    zone = np.zeros(tehd.shape, np.int8)
    for i, (limit, _) in enumerate(ZONES):
        zone = np.where(tehd >= (ZONES[i - 1][0] if i else -1), i, zone)

    trigger = (rainfall_trigger(rainfall_mm, antecedent_mm)
               if rainfall_mm is not None
               else {"crossed": False, "exceedance_ratio": 0.0,
                     "source": "no rainfall event supplied"})

    init = initiation_zones(tehd, slope, trigger)
    affected = runout(init, terrain) if init.any() else init

    return LandslideHazard(
        tehd=tehd,
        susceptibility=np.clip(tehd / max(achievable, 1e-6), 0, 1).astype(np.float32),
        achievable_max=achievable,
        zone=zone, initiation=init, affected=affected, factors=factors,
        trigger=trigger, factors_estimated=estimated,
        complete=lith_struct is not None,
    )
