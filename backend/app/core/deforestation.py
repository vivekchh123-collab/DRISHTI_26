"""Deforestation: how losing forest raises both landslide and flood hazard.

Forest is the cheapest flood-control and slope-stabilisation infrastructure a
district has, and it is the only one that can be removed in a fortnight. This
module quantifies what that removal costs, through the two mechanisms that
actually matter.

**Slope stability — root cohesion.** Tree roots add mechanical reinforcement to
the soil mantle, typically 1-10 kPa of apparent cohesion on a forested hillslope.
Felling does not remove it immediately: roots decay over years, so the hazard
*rises after* the logging rather than during it, and peaks somewhere between
three and fifteen years later (Sidle & Ochiai, 2006). That lag is why the
landslide that kills people often follows the clearance by a decade, and why
nobody connects the two. Here it enters the BIS LHEF land-use rating, which is
the correct place for it: the standard already rates thickly-vegetated forest
better than scrub and scrub better than barren ground.

**Runoff — curve number.** Removing canopy and litter raises the SCS curve
number, so a larger share of the same rainfall becomes runoff and it arrives
faster. Handbook values for hydrologic soil group C: woodland in good condition
around 70, degraded scrub around 80, bare ground around 86. Catchment
experiments consistently show water yield rising after forest removal
(Bosch & Hewlett, 1982).

Forest cover prefers **ESA WorldCover's observed tree-cover class** when it has
been baked for a district (``scripts/fetch_worldcover.py``), and falls back to
the terrain model otherwise. A full canopy-loss trajectory — Forest Survey of
India, Hansen/GFW tree-cover loss, or an NDVI time series — still loads through
:func:`load_forest_raster` and replaces the baseline entirely when supplied;
WorldCover only improves what the model falls back to when nothing more
specific is available.

The headline output is a counterfactual: run the hazard assessment with the
forest, run it without, and report the difference in red-zone area. That turns
"deforestation is bad" into "clearing this ridge adds N square kilometres of
uninhabitable land and M people to the relocation list".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .exposure import Exposure
from .grid import Grid
from .terrain import Terrain, normalize, smooth

# Curve numbers, hydrologic soil group C, from USDA-NRCS NEH-630 Table 2-2c.
CN_FOREST_GOOD = 70.0
CN_FOREST_POOR = 77.0
CN_SCRUB = 80.0
CN_BARREN = 86.0

# BIS IS 14496 (Part 2) land-use ratings, reused so the landslide effect enters
# through the standard rather than beside it.
LHEF_FOREST = 0.80
LHEF_SCRUB = 1.20
LHEF_BARREN = 1.60

# Root cohesion decays after felling; hazard peaks several years later.
ROOT_DECAY_PEAK_YEARS = 8.0


@dataclass
class ForestState:
    """Forest cover now, what has been lost, and what that costs."""
    canopy: np.ndarray             # 0..1 current canopy fraction
    baseline_canopy: np.ndarray    # 0..1 canopy before the modelled loss
    loss: np.ndarray               # 0..1 fraction lost
    years_since_loss: float
    root_cohesion_factor: np.ndarray   # 1 = full reinforcement, 0 = none
    source: str

    @property
    def forest_area_fraction(self) -> float:
        return float((self.canopy >= 0.4).mean())

    def summary(self, cell_km2: float) -> dict:
        lost = self.loss >= 0.25
        return {
            "source": self.source,
            "canopy_mean": round(float(self.canopy.mean()), 3),
            "forest_area_km2": round(float((self.canopy >= 0.4).sum()) * cell_km2, 1),
            "baseline_forest_area_km2": round(
                float((self.baseline_canopy >= 0.4).sum()) * cell_km2, 1),
            "area_lost_km2": round(float(lost.sum()) * cell_km2, 1),
            "loss_fraction_of_forest": round(
                float(lost.sum()) / max(float((self.baseline_canopy >= 0.4).sum()), 1.0), 3),
            "years_since_loss": self.years_since_loss,
            "mean_root_cohesion_remaining": round(
                float(self.root_cohesion_factor[lost].mean()), 3) if lost.any() else 1.0,
            "note": ("Root reinforcement decays over years, so landslide hazard "
                     "peaks roughly %.0f years after felling rather than "
                     "immediately." % ROOT_DECAY_PEAK_YEARS),
        }


def model_canopy(terrain: Terrain, exposure: Exposure) -> np.ndarray:
    """Estimate canopy fraction from terrain and land use.

    Forest survives where farming and building are hardest: steep, high, wet,
    away from settlement. This is a stand-in for a real cover raster, and it is
    replaced wholesale by :func:`load_forest_raster` when one is supplied.
    """
    steep = np.clip((terrain.slope - 8.0) / 25.0, 0, 1)
    wet = normalize(np.clip(terrain.twi, np.percentile(terrain.twi, 10),
                            np.percentile(terrain.twi, 90)))
    high = normalize(terrain.dem)
    away = np.clip(1.0 - exposure.built_up * 2.2, 0, 1)
    not_farmed = np.clip(1.0 - exposure.cropland * 1.5, 0, 1)

    canopy = (0.45 * steep + 0.20 * high + 0.15 * wet) * away * not_farmed
    canopy = np.where(terrain.sea_mask | terrain.streams, 0.0, canopy)
    return np.clip(smooth(canopy, radius=2) * 1.6, 0, 1).astype(np.float32)


def load_worldcover_canopy(code: str, grid: Grid) -> Optional[np.ndarray]:
    """Observed tree-cover fraction from the baked ESA WorldCover bake, or None.

    A real land-cover class beats a terrain-suitability model of where forest
    "should" be, but it is not a loss trajectory - it is one snapshot (2021),
    so it becomes the *baseline* :func:`build` starts from, not a substitute
    for :func:`load_forest_raster`'s canopy-loss inputs when those exist.
    """
    worldcover_dir = os.environ.get("DRISHTI_WORLDCOVER") or os.path.join(
        os.path.dirname(__file__), "..", "data", "worldcover")
    path = os.path.join(worldcover_dir, "%s.npz" % code)
    if not os.path.exists(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as z:
            arr = z["tree_cover"].astype(np.float32)
    except Exception:
        return None
    if arr.shape != (grid.n, grid.n):
        return None
    return arr


def load_forest_raster(path: str, grid: Grid) -> Optional[np.ndarray]:
    """Load a real canopy-cover raster (Hansen, FSI, or an NDVI composite).

    Values are interpreted as a percentage if they exceed 1, otherwise as a
    fraction, so both Hansen tree-cover percent and a normalised NDVI product
    load without a converter.
    """
    from .terrain import load_dem_geotiff
    raw = load_dem_geotiff(path, grid)
    if raw is None:
        return None
    v = raw.astype(np.float32)
    if float(np.nanmax(v)) > 1.5:
        v = v / 100.0
    return np.clip(np.nan_to_num(v), 0, 1)


def root_cohesion(loss: np.ndarray, years_since: float) -> np.ndarray:
    """Remaining root reinforcement after felling, 1 = intact, 0 = gone.

    Roots hold for a while and then let go. Reinforcement follows a decay that
    is near-total by a decade, so the minimum stability — and the maximum
    landslide hazard — arrives years after the chainsaws, long after anyone is
    still connecting the two events.
    """
    decay = 1.0 - np.exp(-max(years_since, 0.0) / (ROOT_DECAY_PEAK_YEARS / 2.0))
    return np.clip(1.0 - loss * float(np.clip(decay, 0, 1)), 0, 1).astype(np.float32)


def apply_to_landslide_rating(base_rating: np.ndarray, forest: ForestState,
                              slope_deg: np.ndarray) -> np.ndarray:
    """Worsen the BIS land-use rating where roots have been lost.

    Only on ground steep enough for the reinforcement to have been doing
    anything: root cohesion is irrelevant on a floodplain.
    """
    relevant = slope_deg >= 12.0
    degraded = LHEF_SCRUB + (LHEF_BARREN - LHEF_SCRUB) * np.clip(forest.loss, 0, 1)
    lost = np.clip(1.0 - forest.root_cohesion_factor, 0, 1)
    out = base_rating + (degraded - base_rating) * lost
    return np.where(relevant, np.maximum(out, base_rating), base_rating).astype(np.float32)


def apply_to_curve_number(cn: np.ndarray, forest: ForestState) -> np.ndarray:
    """Raise the curve number where canopy has gone.

    Canopy and litter are what let rain infiltrate rather than run off. The
    shift is applied in proportion to canopy lost, bounded by the handbook
    values for bare ground.
    """
    target = CN_SCRUB + (CN_BARREN - CN_SCRUB) * np.clip(forest.loss, 0, 1)
    shift = np.clip(forest.loss, 0, 1) * np.clip(forest.baseline_canopy, 0, 1)
    out = cn + (target - cn) * shift
    return np.clip(np.maximum(out, cn), 30.0, 98.0).astype(np.float32)


def build(terrain: Terrain, exposure: Exposure, *,
          loss_fraction: float = 0.0,
          years_since_loss: float = 8.0,
          canopy_raster: Optional[np.ndarray] = None,
          loss_raster: Optional[np.ndarray] = None,
          seed: int = 0) -> ForestState:
    """Assemble a forest state, with either a real or a scenario loss.

    ``loss_fraction`` clears that share of the district's forest, choosing the
    most accessible stands first — near roads, on gentler ground, close to
    settlement — because that is the order in which forest is actually cleared,
    and it is emphatically not random.
    """
    baseline = (canopy_raster if canopy_raster is not None
                else model_canopy(terrain, exposure))
    source = ("supplied canopy raster" if canopy_raster is not None
              else "modelled from terrain and land use")

    if loss_raster is not None:
        loss = np.clip(loss_raster, 0, 1).astype(np.float32)
        source += " with supplied loss raster"
    elif loss_fraction > 0:
        forest = baseline >= 0.4
        # Accessibility: gentle, low, near existing clearance.
        access = (normalize(np.clip(30.0 - terrain.slope, 0, 30))
                  + normalize(smooth(exposure.built_up + exposure.cropland, radius=4)))
        access = np.where(forest, normalize(access), -1.0)
        n_clear = int(round(float(forest.sum()) * float(np.clip(loss_fraction, 0, 1))))
        loss = np.zeros(baseline.shape, np.float32)
        if n_clear > 0:
            flat = np.argsort(access.ravel())[::-1][:n_clear]
            loss.ravel()[flat] = 1.0
        source += " with a %.0f%% clearance scenario" % (loss_fraction * 100)
    else:
        loss = np.zeros(baseline.shape, np.float32)

    canopy = np.clip(baseline * (1.0 - loss), 0, 1).astype(np.float32)
    return ForestState(canopy=canopy, baseline_canopy=baseline, loss=loss,
                       years_since_loss=years_since_loss,
                       root_cohesion_factor=root_cohesion(loss, years_since_loss),
                       source=source)


@dataclass
class DeforestationImpact:
    """The counterfactual: what the forest was worth."""
    loss_fraction: float
    forest_area_lost_km2: float
    landslide_tehd_delta: float
    landslide_area_added_km2: float
    curve_number_delta: float
    runoff_increase_pct: float          # over the cleared land itself
    runoff_increase_district_pct: float # over the whole district
    extra_runoff_million_m3: float
    notes: List[str]

    def to_dict(self) -> dict:
        return {
            "clearance_scenario": round(self.loss_fraction, 3),
            "forest_area_lost_km2": round(self.forest_area_lost_km2, 1),
            "mean_landslide_tehd_increase": round(self.landslide_tehd_delta, 3),
            "landslide_hazard_area_added_km2": round(self.landslide_area_added_km2, 1),
            "mean_curve_number_increase_on_cleared_land": round(self.curve_number_delta, 2),
            "runoff_increase_pct_on_cleared_land": round(self.runoff_increase_pct, 1),
            "runoff_increase_pct_district_total": round(
                self.runoff_increase_district_pct, 2),
            "extra_runoff_million_m3_per_storm": round(
                self.extra_runoff_million_m3, 2),
            "notes": self.notes,
        }


def impact(grid: Grid, terrain: Terrain, exposure: Exposure,
           urban_fraction: float, cropland_fraction: float,
           loss_fraction: float = 0.30,
           years_since_loss: float = 8.0,
           design_rain_mm: float = 200.0) -> DeforestationImpact:
    """Quantify a clearance scenario against the intact-forest baseline."""
    from .hydrology import curve_number, runoff_depth
    from .landslide import rate_land_use

    intact = build(terrain, exposure, loss_fraction=0.0)
    cleared = build(terrain, exposure, loss_fraction=loss_fraction,
                    years_since_loss=years_since_loss)

    base_rating = rate_land_use(exposure.built_up, exposure.cropland, terrain.slope)
    r_intact = apply_to_landslide_rating(base_rating, intact, terrain.slope)
    r_cleared = apply_to_landslide_rating(base_rating, cleared, terrain.slope)

    steep = terrain.slope >= 12.0
    tehd_delta = float((r_cleared - r_intact)[steep].mean()) if steep.any() else 0.0
    # A rating rise of this size moves cells across the BIS hazard boundary.
    added = float(((r_cleared - r_intact) >= 0.3).sum()) * grid.cell_area_km2

    cn0 = curve_number(terrain, urban_fraction, cropland_fraction, 0)
    cn1 = apply_to_curve_number(cn0, cleared)
    rain = np.full(cn0.shape, design_rain_mm, np.float32)
    q0 = runoff_depth(rain, cn0)
    q1 = runoff_depth(rain, cn1)

    # Two different numbers, and reporting only the second is how a real effect
    # gets buried. Averaging the change over an entire district dilutes it by
    # every cell that never had trees on it: clearing a tenth of a district
    # cannot move a district-wide mean much, however severe it is where it
    # happened. The number that means something to a catchment engineer is the
    # change *on the land that was cleared*; the district total is what matters
    # for the flood peak downstream.
    cleared_mask = cleared.loss >= 0.25
    if cleared_mask.any():
        runoff_pct = 100.0 * (float(q1[cleared_mask].mean())
                              - float(q0[cleared_mask].mean())
                              ) / max(float(q0[cleared_mask].mean()), 1e-6)
        cn_delta = float((cn1 - cn0)[cleared_mask].mean())
    else:
        runoff_pct, cn_delta = 0.0, 0.0
    district_pct = 100.0 * (float(q1.mean()) - float(q0.mean())
                            ) / max(float(q0.mean()), 1e-6)
    extra_m3 = (float((q1 - q0).sum()) * 1e-3
                * grid.cell_area_km2 * 1e6) / 1e6

    lost_km2 = float((cleared.loss >= 0.25).sum()) * grid.cell_area_km2
    notes = [
        "Clearance is applied to the most accessible stands first — gentle "
        "ground near roads and existing fields — because that is the order "
        "forest is actually lost in.",
        "Landslide hazard is evaluated %.0f years after felling, when root "
        "reinforcement has largely decayed. The effect is smaller immediately "
        "after clearance and larger later." % years_since_loss,
        "Runoff change is evaluated on a %.0f mm design storm using SCS-CN. "
        "The headline figure is the change on the cleared land itself; the "
        "district-total figure is smaller because most of a district was never "
        "forested." % design_rain_mm,
    ]
    return DeforestationImpact(
        loss_fraction=loss_fraction, forest_area_lost_km2=lost_km2,
        landslide_tehd_delta=tehd_delta, landslide_area_added_km2=added,
        curve_number_delta=cn_delta,
        runoff_increase_pct=runoff_pct,
        runoff_increase_district_pct=district_pct,
        extra_runoff_million_m3=extra_m3, notes=notes)
