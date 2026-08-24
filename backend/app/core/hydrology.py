"""Rainfall to runoff to river stage.

Three published methods chained together:

* **SCS Curve Number** (USDA-NRCS, National Engineering Handbook Part 630) turns
  a rainfall depth into the fraction that runs off rather than infiltrating,
  given land cover, soil and how wet the ground already was.
* **Flow accumulation** over the D8 network already built in :mod:`terrain`
  collects that runoff downstream into a discharge.
* **Manning's equation** with downstream hydraulic geometry (Leopold & Maddock,
  1953) converts discharge into a river stage, which is what the flood model
  actually consumes.

Everything here is deliberately lumped and quasi-steady. A full unsteady
hydrodynamic solve (HEC-RAS, LISFLOOD-FP) is the right tool for a design study
and the wrong one for a district dashboard that must answer in under a second;
the simplification is recorded in docs/ASSUMPTIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np

from .grid import Grid
from .rainfall import RainfallEvent
from .terrain import FlowNetwork, Terrain, normalize, smooth

MANNING_N = 0.035          # natural channel, some vegetation (Chow, 1959)


# ---------------------------------------------------------------------------
# runoff generation
# ---------------------------------------------------------------------------

def curve_number(terrain: Terrain, urban_fraction: float,
                 cropland_fraction: float, seed: int) -> np.ndarray:
    """Curve number grid, 30-98.

    Higher CN means more of the rain runs off. Built-up land is near 90 because
    concrete infiltrates nothing; forest on sandy soil can be below 50. The
    spatial pattern follows built-up likelihood, which in turn follows the
    terrain — settlements sit on the higher, drier ground.
    """
    n = terrain.grid.n
    # Built-up is likeliest on flat, well-drained ground away from the channel.
    dryness = normalize(np.clip(terrain.hand, 0, np.percentile(terrain.hand, 92)))
    flatness = 1.0 - normalize(np.clip(terrain.slope, 0, 12))
    urban_p = normalize(smooth(dryness * flatness, radius=2))

    built = urban_p >= np.quantile(urban_p, max(1.0 - urban_fraction, 0.0)) \
        if urban_fraction > 0 else np.zeros((n, n), bool)
    crop_rank = normalize(smooth(1.0 - dryness, radius=2))
    crop = (~built) & (crop_rank >= np.quantile(crop_rank,
                                                max(1.0 - cropland_fraction, 0.0)))

    cn = np.full((n, n), 66.0, np.float32)      # mixed / scrub baseline
    cn[crop] = 78.0                             # row crops, straight row, group C
    cn[built] = 90.0                            # commercial + dense residential
    cn[terrain.slope > 22] = 58.0               # steep forest, group B
    cn[terrain.streams] = 98.0                  # open water
    if terrain.sea_mask.any():
        cn[terrain.sea_mask] = 98.0
    return smooth(cn, radius=1).astype(np.float32)


def amc_adjust(cn2: np.ndarray, antecedent_mm: float) -> np.ndarray:
    """Shift the curve number for antecedent moisture.

    NEH-630 defines three antecedent moisture conditions. AMC I is dry soil with
    room to absorb, AMC III is saturated soil that sheds almost everything. The
    conversions below are the handbook's. Interpolating between them on the API
    avoids a discontinuous jump in flood extent as the index crosses a threshold.
    """
    cn1 = 4.2 * cn2 / (10.0 - 0.058 * cn2)
    cn3 = 23.0 * cn2 / (10.0 + 0.13 * cn2)
    # dry below 13 mm, saturated above 53 mm (NEH growing-season limits)
    f = float(np.clip((antecedent_mm - 13.0) / 40.0, 0.0, 2.0))
    if f <= 1.0:
        out = cn1 + (cn2 - cn1) * f
    else:
        out = cn2 + (cn3 - cn2) * (f - 1.0)
    return np.clip(out, 30.0, 98.0).astype(np.float32)


def runoff_depth(rain_mm: np.ndarray, cn: np.ndarray,
                 ia_ratio: float = 0.2) -> np.ndarray:
    """SCS-CN direct runoff depth (mm).

        S  = 25400 / CN - 254            potential maximum retention
        Ia = ia_ratio * S                initial abstraction
        Q  = (P - Ia)^2 / (P - Ia + S)   for P > Ia, else 0

    Runoff can never exceed rainfall, which the test suite asserts.
    """
    s = 25400.0 / np.clip(cn, 1e-3, 100.0) - 254.0
    ia = ia_ratio * s
    excess = np.maximum(rain_mm - ia, 0.0)
    return (excess ** 2 / np.maximum(excess + s, 1e-6)).astype(np.float32)


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------

def accumulate(net: FlowNetwork, weights: np.ndarray) -> np.ndarray:
    """Weighted downstream accumulation over the existing D8 network.

    The unweighted version in :mod:`terrain` counts cells; this carries any
    quantity — runoff volume here — down the same receiver chain.

    Accepts either a single ``(n, n)`` field or a whole ``(T, n, n)`` time
    series, and that matters for speed rather than convenience: the traversal
    must visit cells one at a time in topological order, so routing 96 hours
    separately costs 96 passes over every cell in Python. Carrying the time axis
    as a vector through a single pass turns 2.5 million interpreted iterations
    into 25,600, each doing one vectorised add.
    """
    single = weights.ndim == 2
    n = weights.shape[-1]
    flat = weights.reshape(1, -1) if single else weights.reshape(weights.shape[0], -1)
    acc = flat.astype(np.float32).copy()
    recv = net.receiver
    for i in net.order[::-1]:                 # descending elevation: upstream first
        j = recv[i]
        if j >= 0:
            acc[:, j] += acc[:, i]
    return acc.reshape((n, n) if single else (-1, n, n))


def lag_matrix(uh: np.ndarray, hours: int) -> np.ndarray:
    """Lower-triangular convolution operator for a unit hydrograph.

    ``M[t, s] = uh[t - s]``, so ``M @ series`` convolves every column at once
    through BLAS instead of looping in Python.
    """
    m = np.zeros((hours, hours), np.float32)
    for s in range(hours):
        k = min(hours - s, len(uh))
        m[s:s + k, s] = uh[:k]
    return m


def unit_hydrograph(tp_hours: float, length: int) -> np.ndarray:
    """Single-peaked gamma unit hydrograph, normalised to unit volume.

    Runoff does not arrive at the outlet the instant it lands. The catchment
    delays and smears it, and the delay grows with catchment size — which is why
    a headwater tributary peaks hours before the trunk it feeds.
    """
    t = np.arange(1, length + 1, dtype=np.float64)
    k = 3.0                                    # shape; k=3 is a typical basin
    scale = max(tp_hours, 0.5) / (k - 1.0)
    u = (t ** (k - 1)) * np.exp(-t / scale)
    return u / max(u.sum(), 1e-12)


def time_to_peak(area_km2: np.ndarray, slope_pct: float) -> np.ndarray:
    """Catchment lag from area, after the SCS lag relation.

    Time to peak scales roughly with the square root of area and inversely with
    slope. Exponent 0.3 keeps a 5 km^2 headwater near an hour and a 2000 km^2
    trunk near a day, which is the right order for Indian basins.
    """
    return np.clip(1.6 * np.power(np.maximum(area_km2, 0.05), 0.30)
                   / max(slope_pct, 0.05) ** 0.15, 0.6, 48.0)


@dataclass
class Hydrograph:
    """Discharge and stage through time on the stream network."""
    hours: int
    discharge: np.ndarray      # (T, n, n) m^3/s, non-zero on stream cells
    stage: np.ndarray          # (T, n, n) metres above the local channel bed
    peak_hour: int
    catchment_km2: np.ndarray  # (n, n) upstream area
    channel_width_m: np.ndarray
    bankfull_m: np.ndarray     # (n, n) depth at which the channel spills


def channel_geometry(catchment_km2: np.ndarray) -> np.ndarray:
    """Channel width from catchment area (downstream hydraulic geometry).

    Leopold & Maddock (1953): width scales as a power of discharge, and
    discharge with catchment area. The combined exponent near 0.5 reproduces the
    familiar result that a river draining 100 times more land is about 10 times
    wider.
    """
    return np.clip(2.6 * np.power(np.maximum(catchment_km2, 0.01), 0.50), 3.0, 2500.0)


def stage_from_discharge(q: np.ndarray, width_m: np.ndarray,
                         slope: np.ndarray) -> np.ndarray:
    """Invert Manning's equation for depth in a wide channel.

        Q = (1/n) * W * h^(5/3) * S^(1/2)   ->   h = (Q n / (W sqrt(S)))^(3/5)

    Valid while flow stays roughly channel-shaped. Once it spills onto the
    floodplain the true hydraulics are two-dimensional and this over-predicts
    depth, so the flood model caps how far stage may exceed bankfull.
    """
    s = np.clip(np.tan(np.radians(slope)), 1e-4, 0.2)
    denom = np.maximum(width_m * np.sqrt(s), 1e-6)
    return np.power(np.maximum(q, 0.0) * MANNING_N / denom, 0.6).astype(np.float32)


def bankfull_depth(catchment_km2: np.ndarray) -> np.ndarray:
    """Depth at which the channel fills and water starts spilling over the banks.

    The depth limb of downstream hydraulic geometry: depth grows with catchment
    area far more slowly than width does, which is why big rivers are broad
    rather than deep.
    """
    return np.clip(0.30 * np.power(np.maximum(catchment_km2, 0.01), 0.30), 0.5, 14.0)


MANNING_N_FLOODPLAIN = 0.06     # crops and scrub, higher drag (Chow, 1959)
MAX_FLOODPLAIN_WIDTH_M = 6000.0  # valley width bound, not an unbounded sheet


def slope_term(slope: np.ndarray) -> np.ndarray:
    """sqrt of the energy slope — the part of Manning that does not vary with depth."""
    return np.sqrt(np.clip(np.tan(np.radians(slope)), 1e-4, 0.2))


def conveyance(h: np.ndarray, width_m: np.ndarray, sqrt_s: np.ndarray,
               bankfull_m: np.ndarray, spread: float) -> np.ndarray:
    """Discharge a compound section carries at depth ``h`` (m^3/s).

    Two Manning terms summed: the in-bank channel, plus a floodplain that only
    exists once the water is above bankfull and whose width grows as it deepens.
    The floodplain is rougher than the channel, so it carries far less per unit
    area — which is why overbank flow spreads rather than accelerates.

    Takes ``sqrt_s`` already evaluated rather than the slope, because the
    bisection calls this twenty-odd times per solve and the trigonometry does
    not depend on depth.
    """
    h = np.maximum(h, 0.0)
    q = (width_m / MANNING_N) * np.power(h, 5.0 / 3.0) * sqrt_s
    over = np.maximum(h - bankfull_m, 0.0)
    w_fp = np.minimum(spread * over, MAX_FLOODPLAIN_WIDTH_M)
    q += (w_fp / MANNING_N_FLOODPLAIN) * np.power(over, 5.0 / 3.0) * sqrt_s
    return q


def stage_compound(q: np.ndarray, width_m: np.ndarray, slope: np.ndarray,
                   bankfull_m: np.ndarray, spread: float,
                   iterations: int = 22, h_max: float = 30.0) -> np.ndarray:
    """Invert the compound rating curve for stage, by bisection.

    Solving Manning against the channel width alone sends every extra cubic
    metre into depth, which on a district as flat as Darbhanga produced a 15 m
    stage — physically impossible. The obvious fix, iterating the width against
    the depth, does not work either: with a floodplain that widens by hundreds
    of metres per metre of depth the map is not a contraction, and successive
    substitution oscillates between wildly overbank and comfortably in-bank
    rather than converging.

    Conveyance is, however, strictly increasing in depth. That makes bisection
    both unconditionally convergent and trivially vectorisable over the grid,
    which is why it is used here in preference to anything cleverer.
    """
    sqrt_s = slope_term(slope)
    lo = np.zeros(q.shape, np.float32)
    hi = np.full(q.shape, h_max, np.float32)
    for _ in range(iterations):
        mid = 0.5 * (lo + hi)
        too_small = conveyance(mid, width_m, sqrt_s, bankfull_m, spread) < q
        lo = np.where(too_small, mid, lo)
        hi = np.where(too_small, hi, mid)
    return (0.5 * (lo + hi)).astype(np.float32)


def floodplain_spread(mean_slope_deg: float) -> float:
    """How far water spreads sideways per metre of overbank depth."""
    return float(np.clip(120.0 / max(mean_slope_deg, 0.05), 25.0, 2500.0))


def simulate(terrain: Terrain, event: RainfallEvent, *, urban_fraction: float,
             cropland_fraction: float, seed: int,
             area_bins: int = 6) -> Hydrograph:
    """Run the rainfall event through runoff, routing and channel hydraulics."""
    grid: Grid = terrain.grid
    n = grid.n
    cell_area_m2 = grid.cell_area_km2 * 1e6

    cn = amc_adjust(curve_number(terrain, urban_fraction, cropland_fraction, seed),
                    event.antecedent_mm)

    # Runoff is computed on the cumulative rainfall curve and differenced, which
    # is what makes initial abstraction behave correctly: the first millimetres
    # of a storm soak in, later ones do not.
    cum_prev = np.zeros((n, n), np.float32)
    q_prev = np.zeros((n, n), np.float32)
    hourly_runoff_m3 = np.zeros((event.hours, n, n), np.float32)
    for t in range(event.hours):
        cum = cum_prev + event.field(t)
        q_now = runoff_depth(cum, cn)
        hourly_runoff_m3[t] = np.maximum(q_now - q_prev, 0.0) * 1e-3 * cell_area_m2
        cum_prev, q_prev = cum, q_now

    catchment_km2 = terrain.accum * grid.cell_area_km2
    width = channel_geometry(catchment_km2)
    slope_pct = max(float(np.mean(terrain.slope)), 0.02)

    # Route: accumulate each hour's runoff downstream, then delay it by a unit
    # hydrograph whose lag depends on the local catchment size. Cells are grouped
    # into a few area classes so this costs a handful of convolutions rather than
    # one per cell, while still letting headwaters peak before the trunk.
    routed = accumulate(terrain.net, hourly_runoff_m3)      # (T, n, n), one pass

    tp = time_to_peak(catchment_km2, slope_pct)
    edges = np.quantile(tp, np.linspace(0, 1, area_bins + 1))
    edges[0], edges[-1] = tp.min() - 1e-6, tp.max() + 1e-6

    delayed = np.zeros_like(routed)
    for b in range(area_bins):
        sel = (tp >= edges[b]) & (tp < edges[b + 1])
        if not sel.any():
            continue
        uh = unit_hydrograph(float(np.median(tp[sel])), event.hours)
        delayed[:, sel] = lag_matrix(uh, event.hours) @ routed[:, sel]

    discharge = (delayed / 3600.0).astype(np.float32)  # m^3 per hour -> m^3/s
    bankfull = bankfull_depth(catchment_km2)
    spread = floodplain_spread(slope_pct)

    # Stage is solved on the drainage network only. Every cell's inundation is
    # read from the stage at *its own* outlet cell (terrain.nearest), and those
    # are always stream cells — so solving the rating curve over the whole grid
    # spends 96% of the work on values nothing ever reads. Restricting it turns
    # roughly twelve seconds per district into a fraction of one.
    # Sea cells are in the stream mask so that drainage routes off the coast,
    # but they have no river stage to solve and on a delta district they are a
    # third of the grid.
    sm = terrain.streams & ~terrain.sea_mask
    stage = np.zeros_like(discharge)
    if sm.any():
        stage[:, sm] = stage_compound(
            discharge[:, sm],
            width[sm][None, :], terrain.slope[sm][None, :],
            bankfull[sm][None, :], spread)

    basin = discharge.reshape(event.hours, -1).max(axis=1)
    return Hydrograph(hours=event.hours, discharge=discharge, stage=stage,
                      peak_hour=int(np.argmax(basin)),
                      catchment_km2=catchment_km2.astype(np.float32),
                      channel_width_m=width.astype(np.float32),
                      bankfull_m=bankfull.astype(np.float32))
