"""Encroachment: who has built inside the Red Zone since we last looked.

This is the sharpest answer the system has to the problem statement's demand
that Red Zones be *dynamically updated*. Live rainfall does not update a Red
Zone — a red zone is a recurrence property of a place, and whether it rained
this morning does not change it. What genuinely updates it is **change on the
ground**, and the change that matters most for relocation planning is new
construction inside land already known to be unfit for habitation.

Built-up surface from the Global Human Settlement Layer, differenced between
epochs and intersected with the Red Zones, gives a number a State Disaster
Management Authority can act on immediately: *this many hectares of new building
inside the danger, in these habitations, since this year.* It says where to stop
issuing permits, and it quantifies the caseload that will otherwise have to be
relocated later at public expense.

The framing matters. Every hectare built inside a Red Zone after the zone was
known is a hectare somebody will eventually pay to move, so the trend line here
is the difference between a relocation problem that is shrinking and one that is
growing faster than it can be funded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .grid import Grid
from .redzone import RedZones

GHSL_DIR = os.environ.get("DRISHTI_GHSL") or os.path.join(
    os.path.dirname(__file__), "..", "data", "ghsl")

# Below this built-up fraction a cell is not meaningfully settled; GHSL carries
# small non-zero values across rural landscapes from isolated structures, and
# counting those as "construction" would drown the signal.
BUILT_THRESHOLD = 0.02

# A cell counts as newly built if its built-up fraction rose by at least this
# much between epochs. Small enough to catch a hamlet, large enough to stay
# above the product's own epoch-to-epoch noise.
NEW_BUILD_DELTA = 0.01


def load_built(code: str) -> Optional[Dict[int, np.ndarray]]:
    """Built-up fraction per epoch for a district, or None if not baked."""
    path = os.path.join(GHSL_DIR, "%s.npz" % code)
    if not os.path.exists(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as z:
            epochs = [int(e) for e in z["epochs"]]
            return {e: z["built_%d" % e].astype(np.float32) for e in epochs
                    if "built_%d" % e in z}
    except Exception:
        return None


@dataclass
class EncroachmentResult:
    available: bool
    epochs: List[int]
    baseline: Optional[int]
    latest: Optional[int]
    built_now: Optional[np.ndarray]         # built-up fraction, latest epoch
    new_build: Optional[np.ndarray]         # bool, newly built since baseline
    new_in_red: Optional[np.ndarray]        # bool, newly built inside a Red Zone
    stats: dict
    per_habitation: List[dict]

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "epochs": self.epochs,
            "baseline_epoch": self.baseline,
            "latest_epoch": self.latest,
            **self.stats,
            "per_habitation": self.per_habitation,
        }


def _series(built: Dict[int, np.ndarray], red: np.ndarray,
            cell_km2: float) -> List[dict]:
    """Built-up area inside and outside the Red Zone at each epoch."""
    out = []
    for e in sorted(built):
        b = built[e]
        settled = b >= BUILT_THRESHOLD
        out.append({
            "epoch": e,
            "built_up_km2": round(float(b.sum()) * cell_km2, 2),
            "built_up_in_red_zone_km2": round(float(b[red].sum()) * cell_km2, 2),
            "settled_cells": int(settled.sum()),
            "settled_cells_in_red_zone": int((settled & red).sum()),
        })
    return out


def assess(grid: Grid, code: str, red: RedZones, population: np.ndarray,
           district_mask: np.ndarray,
           habitations: Optional[Sequence] = None,
           baseline_epoch: Optional[int] = None) -> EncroachmentResult:
    """Measure new construction inside the Red Zones."""
    built = load_built(code)
    if not built:
        return EncroachmentResult(
            False, [], None, None, None, None, None,
            {"reason": ("GHSL built-up surface not baked for this district — "
                        "run scripts/fetch_ghsl.py"),
             "source": "GHS-BUILT-S R2023A (JRC), 100 m"},
            [])

    epochs = sorted(built)
    latest = epochs[-1]
    # Default baseline is the epoch closest to 2015, which is roughly when
    # district hazard mapping became widely available in India — so growth
    # after it is construction that could have known better.
    if baseline_epoch is None or baseline_epoch not in built:
        baseline_epoch = min(epochs, key=lambda e: abs(e - 2015))
    if baseline_epoch == latest and len(epochs) > 1:
        baseline_epoch = epochs[-2]

    cell_km2 = grid.cell_area_km2
    red_mask = red.is_red & district_mask

    b0, b1 = built[baseline_epoch], built[latest]
    delta = b1 - b0
    new_build = (delta >= NEW_BUILD_DELTA) & district_mask
    new_in_red = new_build & red_mask

    new_km2 = float(new_build.sum()) * cell_km2
    new_red_km2 = float(new_in_red.sum()) * cell_km2
    pop_in_new_red = float(population[new_in_red].sum()) if new_in_red.any() else 0.0

    # Growth inside the Red Zone against growth outside it. If the ratio exceeds
    # one, building is concentrating in the danger rather than avoiding it —
    # which is the finding that should reach a planning authority.
    outside = new_build & ~red_mask & district_mask
    red_share_of_area = (float(red_mask.sum()) / max(int(district_mask.sum()), 1))
    red_share_of_growth = (float(new_in_red.sum()) / max(int(new_build.sum()), 1))
    concentration = (red_share_of_growth / red_share_of_area
                     if red_share_of_area > 0 else None)

    per_hab: List[dict] = []
    if habitations and new_in_red.any():
        n = grid.n
        for h in habitations:
            r, c = grid.rowcol(h.lat, h.lon)
            r0, r1 = max(0, r - 4), min(n, r + 5)
            c0, c1 = max(0, c - 4), min(n, c + 5)
            sel = np.zeros(new_in_red.shape, bool)
            sel[r0:r1, c0:c1] = True
            cells = int((sel & new_in_red).sum())
            if cells:
                per_hab.append({
                    "id": h.id, "label": h.label,
                    "new_build_in_red_zone_km2": round(cells * cell_km2, 3),
                    "new_build_in_red_zone_hectares": round(cells * cell_km2 * 100, 1),
                    "relocation_horizon": h.horizon,
                })
        per_hab.sort(key=lambda x: -x["new_build_in_red_zone_km2"])

    stats = {
        "source": "GHS-BUILT-S R2023A (JRC), 100 m, Mollweide",
        "baseline_epoch": baseline_epoch,
        "latest_epoch": latest,
        "new_build_km2": round(new_km2, 2),
        "new_build_in_red_zone_km2": round(new_red_km2, 3),
        "new_build_in_red_zone_hectares": round(new_red_km2 * 100, 1),
        "population_in_new_red_zone_build": int(round(pop_in_new_red)),
        "red_zone_share_of_district": round(red_share_of_area, 3),
        "red_zone_share_of_new_build": round(red_share_of_growth, 3),
        "concentration_ratio": (None if concentration is None
                                else round(concentration, 2)),
        "concentration_reading": _read_concentration(concentration),
        "series": _series(built, red_mask, cell_km2),
        "thresholds": {
            "settled_built_fraction": BUILT_THRESHOLD,
            "new_build_delta": NEW_BUILD_DELTA,
        },
        "why_this_matters": (
            "Every hectare built inside a Red Zone after the hazard was known "
            "is a hectare somebody will later pay to relocate. This is the "
            "measure that turns a Red Zone map into a permitting decision."),
    }

    return EncroachmentResult(
        True, epochs, baseline_epoch, latest, b1, new_build, new_in_red,
        stats, per_hab)


def _read_concentration(ratio: Optional[float]) -> str:
    if ratio is None:
        return "no Red Zone in this district to compare against"
    if ratio >= 1.5:
        return ("New building is strongly concentrated inside the Red Zone — "
                "construction is moving into the danger, not away from it. "
                "This is a permitting failure, and it is correctable.")
    if ratio >= 1.05:
        return ("New building is slightly over-represented inside the Red Zone.")
    if ratio >= 0.7:
        return ("New building is spread roughly in proportion to area — the "
                "Red Zone is neither attracting nor deterring construction.")
    return ("New building is avoiding the Red Zone, which is what a working "
            "land-use control looks like.")
