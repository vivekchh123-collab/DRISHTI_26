"""Synthetic-aperture radar: detecting standing water through cloud.

This is the observational half of the system and the reason the pitch works.
Floods happen under storm cloud, and an optical satellite sees only the cloud.
Sentinel-1's C-band radar does not care: microwaves pass through weather and
work at night. Open water is specularly smooth, so it reflects the pulse away
from the sensor and returns almost nothing — water is *black* in a radar image,
and that is the whole detection principle.

The processing chain here is the standard one:

1. **Lee filter** to suppress speckle, the multiplicative noise inherent to
   coherent imaging.
2. **Otsu thresholding** on the dB histogram to split the bimodal
   water/land distribution without a hand-tuned cut-off.
3. **Change detection** against a pre-event baseline, so permanent water is not
   reported as flood.
4. **Terrain masking** to remove the three well-known false positives — dry
   sand, smooth tarmac and radar shadow — which are radiometrically
   indistinguishable from water and only separable using the DEM.

In demo mode :func:`simulate_scene` generates the backscatter from a known flood
state, and the detector then runs on it *without being told the answer*. That
makes the detection genuinely testable: :mod:`tests.test_sar` checks recovered
extent against the truth it was generated from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .grid import Grid
from .terrain import Terrain, normalize, smooth

# Typical Sentinel-1 IW GRD VV backscatter, dB. Values from the published
# land-cover signature literature for C-band at moderate incidence.
SIGMA0_DB = {
    "open_water": -20.5,     # specular; the darkest thing in the scene
    "flooded_veg": -12.0,    # double-bounce off stems lifts it back up
    "bare_soil": -12.5,
    "cropland": -9.5,
    "forest": -7.5,
    "urban": -4.0,           # corner reflectors; the brightest
}

# Standard deviation of speckle in dB for a multi-looked GRD product.
SPECKLE_DB = 1.6

# Physical ceiling on what a water threshold may be. Otsu maximises between-class
# variance whether or not the histogram has a water mode at all: on a dry scene
# it happily splits cropland from built-up and declares half the district to be
# water. Constraining the cut to the range open water actually occupies in VV is
# what stops that, and it is the same guard used in operational rapid-mapping
# chains that pair Otsu with a fixed physical bound.
WATER_CEILING_DB = -13.0

# Separability bands for reporting confidence.
#
# The floor is not zero. A single Gaussian split at its median scores
# 0.6366 analytically (see tests/test_algorithms.py), so *any* scene — dry,
# cloudy, or entirely land — scores about 0.64. Bands set below that figure
# would label a dry scene "moderate confidence", which is worse than useless.
SEPARABILITY_UNIMODAL = 0.637
SEPARABILITY_HIGH = 0.80
SEPARABILITY_MODERATE = 0.70


@dataclass
class SarScene:
    """One radar acquisition on the analysis grid."""
    vv_db: np.ndarray
    incidence_deg: float
    acquired: str
    source: str

    @property
    def shape(self) -> Tuple[int, int]:
        return self.vv_db.shape


@dataclass
class WaterDetection:
    """The output of the detection chain."""
    water: np.ndarray            # bool, water present now
    flood: np.ndarray            # bool, water that was not there before
    permanent: np.ndarray        # bool, reference water
    threshold_db: float          # the Otsu cut
    separability: float          # between-class variance ratio, 0..1
    rejected_by_mask: int        # cells the terrain masks removed
    confidence: str

    def summary(self) -> dict:
        return {
            "threshold_db": round(self.threshold_db, 2),
            "separability": round(self.separability, 3),
            "water_cells": int(self.water.sum()),
            "flood_cells": int(self.flood.sum()),
            "permanent_water_cells": int(self.permanent.sum()),
            "rejected_by_terrain_mask": self.rejected_by_mask,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# speckle filtering
# ---------------------------------------------------------------------------

def lee_filter(img: np.ndarray, size: int = 5,
               looks: float = 4.4) -> np.ndarray:
    """Lee (1980) adaptive speckle filter.

    Speckle is multiplicative, so a plain blur destroys edges to remove it. The
    Lee filter interpolates between the local mean and the observed value
    according to how much of the local variance can be explained by speckle
    alone: flat regions are smoothed hard, edges are left almost untouched.

    ``looks`` is the equivalent number of looks of the product; 4.4 is the
    nominal figure for Sentinel-1 IW GRDH.
    """
    r = max(size // 2, 1)
    mean = smooth(img, radius=r)
    sq_mean = smooth(img * img, radius=r)
    var = np.maximum(sq_mean - mean * mean, 0.0)

    cu2 = 1.0 / looks                      # speckle coefficient of variation^2
    ci2 = np.divide(var, np.maximum(mean * mean, 1e-9))
    weight = np.clip((ci2 - cu2) / np.maximum(ci2, 1e-9), 0.0, 1.0)
    return (mean + weight * (img - mean)).astype(np.float32)


# ---------------------------------------------------------------------------
# thresholding
# ---------------------------------------------------------------------------

def otsu_threshold(values: np.ndarray, bins: int = 256) -> Tuple[float, float]:
    """Otsu (1979) threshold, plus a separability measure.

    Chooses the cut that maximises between-class variance, which for a bimodal
    water/land histogram lands in the trough between the two modes with no
    tuning. Returns ``(threshold, separability)`` where separability is the
    between-class variance as a fraction of the total — near 1 when the classes
    are cleanly split, near 0 when the histogram has only one mode and the
    threshold is therefore meaningless.

    Reporting that number matters. A scene with no flood in it still yields a
    threshold; separability is what tells you not to trust it.
    """
    v = values[np.isfinite(values)]
    if v.size == 0:
        return 0.0, 0.0
    hist, edges = np.histogram(v, bins=bins)
    centres = 0.5 * (edges[:-1] + edges[1:])
    total = hist.sum()
    if total == 0:
        return float(centres[0]), 0.0

    p = hist.astype(np.float64) / total
    omega = np.cumsum(p)
    mu = np.cumsum(p * centres)
    mu_t = mu[-1]

    denom = omega * (1.0 - omega)
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma_b = (mu_t * omega - mu) ** 2 / np.where(denom > 0, denom, np.nan)
    sigma_b = np.nan_to_num(sigma_b)
    k = int(np.argmax(sigma_b))
    total_var = float(((centres - mu_t) ** 2 * p).sum())
    sep = float(sigma_b[k] / total_var) if total_var > 0 else 0.0
    return float(centres[k]), float(np.clip(sep, 0.0, 1.0))


# ---------------------------------------------------------------------------
# scene simulation (demo mode only)
# ---------------------------------------------------------------------------

def simulate_scene(terrain: Terrain, built_up: np.ndarray,
                   cropland: np.ndarray, water: np.ndarray,
                   seed: int, acquired: str = "T+0",
                   incidence_deg: float = 38.0) -> SarScene:
    """Generate a plausible Sentinel-1 VV scene for a known water state.

    Used only in demo mode. The detector is never given ``water``; it has to
    recover it from the backscatter, exactly as it would from a real granule.
    """
    rng = np.random.default_rng(seed)
    n = terrain.grid.n

    db = np.full((n, n), SIGMA0_DB["bare_soil"], np.float32)
    db = np.where(cropland > 0.25, SIGMA0_DB["cropland"], db)
    db = np.where(terrain.slope > 18, SIGMA0_DB["forest"], db)
    db = np.where(built_up > 0.25, SIGMA0_DB["urban"], db)

    # Water, including the permanent channel and the sea.
    wet = water | terrain.streams | terrain.sea_mask
    db = np.where(wet, SIGMA0_DB["open_water"], db)
    # Flooded vegetation returns more than open water because the stems produce
    # a double bounce — the classic reason SAR under-maps flood in cropland.
    veg_flood = water & (cropland > 0.72)
    db = np.where(veg_flood, SIGMA0_DB["flooded_veg"], db)

    # Radar shadow and layover on terrain facing away from the sensor. This is
    # what makes hill districts hard and is deliberately reproduced.
    lookdir = np.radians(285.0)
    aspect = np.radians(terrain.aspect)
    facing = np.cos(aspect - lookdir)
    shadow = (terrain.slope > 12) & (facing < -0.55)
    db = np.where(shadow, db - 7.5, db)

    db = db + rng.normal(0.0, SPECKLE_DB, (n, n)).astype(np.float32)
    return SarScene(vv_db=db.astype(np.float32), incidence_deg=incidence_deg,
                    acquired=acquired, source="simulated-sentinel1-vv")


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def detect_water(post: SarScene, terrain: Terrain,
                 pre: Optional[SarScene] = None,
                 hand_ceiling_m: float = 25.0,
                 slope_ceiling_deg: float = 12.0,
                 drop_db: float = 2.5) -> WaterDetection:
    """Run the full detection chain on a scene.

    ``pre`` is a pre-event baseline. With it, permanent water is separated from
    new flooding by requiring the backscatter to have *fallen* — a cell that was
    already dark stays classified as permanent water, not as flood.

    The terrain masks are not optional polish. Dry sand, fresh tarmac and radar
    shadow all return as little energy as water does, and no amount of
    thresholding separates them; only the DEM can. Ground steeper than
    ``slope_ceiling_deg`` cannot hold standing water, and ground more than
    ``hand_ceiling_m`` above its nearest drainage cannot be reached by a river.
    """
    filt = lee_filter(post.vv_db)
    otsu, sep = otsu_threshold(filt)
    thresh = min(otsu, WATER_CEILING_DB)
    dark = filt <= thresh

    if pre is not None:
        pre_filt = lee_filter(pre.vv_db)
        pre_otsu, _ = otsu_threshold(pre_filt)
        pre_dark = pre_filt <= min(pre_otsu, WATER_CEILING_DB)

        # Permanent water must be dark *and* sit on the drainage network.
        #
        # Darkness alone is not enough, for exactly the reason the flood mask
        # needs terrain context: dry sand, smooth tarmac and a wet field all
        # scatter like water. Left unconstrained on real 30 m elevation this
        # called 19% of Darbhanga a permanent water body, and because the
        # permanent mask is subtracted from the flood, three quarters of what it
        # swallowed was genuine inundation. A standing water body lies in a
        # channel or a depression that the flow network already found.
        permanent = pre_dark & (terrain.streams | terrain.sea_mask)

        # Change detection: a real inundation darkens the cell.
        fell = (pre_filt - filt) >= drop_db
        new_water = dark & fell & ~permanent
    else:
        permanent = terrain.streams | terrain.sea_mask
        new_water = dark & ~permanent

    before = int(new_water.sum())
    plausible = (terrain.slope <= slope_ceiling_deg) & (terrain.hand <= hand_ceiling_m)
    flood = new_water & plausible & ~terrain.sea_mask
    rejected = before - int(flood.sum())

    if otsu > WATER_CEILING_DB:
        conf = ("low — no water mode in the histogram; the Otsu cut was above "
                "the physical ceiling and has been clamped")
    elif sep >= SEPARABILITY_HIGH:
        conf = "high"
    elif sep >= SEPARABILITY_MODERATE:
        conf = "moderate"
    else:
        conf = ("low — separability %.2f is at or below the %.2f a single-mode "
                "histogram scores by construction; threshold unreliable"
                % (sep, SEPARABILITY_UNIMODAL))

    return WaterDetection(
        water=(flood | permanent), flood=flood, permanent=permanent,
        threshold_db=thresh, separability=sep,
        rejected_by_mask=rejected, confidence=conf,
    )
