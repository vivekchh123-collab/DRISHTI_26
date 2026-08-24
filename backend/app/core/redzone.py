"""Multi-hazard Red Zones: land unsuitable for permanent habitation.

The question this module answers is not "is this place flooded today" but "should
anyone be living here at all". Those are different questions and they need
different arithmetic.

**Recurrence is the whole idea.** A place that is chest-deep in water once a
century is a place you protect. A place that is chest-deep every fifth monsoon is
a place you move. The severity of a single event says almost nothing on its own;
what matters is the *return period at which the hazard first becomes
habitation-threatening*. So the model is run at the 5-, 25- and 100-year events
and each cell records the shortest return period at which any hazard crosses the
threshold. That single number — years between events that make a place
uninhabitable — is the red-zone index, it is physically meaningful, and it maps
directly onto a relocation horizon.

**Hazards combine by maximum, not by sum.** A hamlet that floods 3 m deep every
five years is unsuitable regardless of its landslide rating; adding a second
hazard on top cannot make it more unsuitable than "do not live here". A small
multi-hazard premium is added because two independent hazards do mean more days
per decade of disruption, but the dominant hazard governs, and it is named in
the output so a planner knows what they are being told to act on.

Aligned with NDMA's National Disaster Management Plan, which frames relocation of
habitations in hazard-prone zones as a mitigation measure requiring
evidence-based prioritisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .exposure import Exposure
from .flood import MIN_DEPTH_M, FloodTimeline
from .grid import Grid, describe_location
from .terrain import Terrain, normalize, smooth

# Intensity at which a hazard makes a place unfit to live, not merely damaged.
# These are the thresholds that separate "flood-prone" from "uninhabitable".
UNINHABITABLE = {
    # Water deep enough to destroy a kachcha dwelling and drown livestock.
    "flood_depth_m": 1.0,
    # Water standing long enough to make a season's residence impossible — but
    # only if it is also deep enough to matter. Duration alone fired on 15 cm of
    # standing water and put almost the whole floodplain into "immediate".
    "flood_duration_h": 72.0,
    "flood_duration_depth_m": 0.5,
    # Ground within the runout path of a triggered slide.
    "landslide": True,
    # Waterlogging that persists past the point of habitability.
    "waterlog_days": 10.0,
    # Land being lost to the sea.
    "erosion_m_per_year": 1.5,
    # Ground in the path of a cloudburst-driven flash flood. Unlike the other
    # hazards this one is binary: there is no useful depth threshold, because
    # what arrives is a debris-laden wall of water minutes after rain that fell
    # somewhere upstream.
    "cloudburst": True,
}

# Relocation horizon by the return period of the uninhabitable hazard.
# Upper bound is 100.5 rather than 100 so that a cell whose hazard first appears
# at the 100-year event lands in medium-term rather than falling through to
# "monitor" on a floating-point boundary.
HORIZONS = (
    (10.0, "immediate", "Hazard recurs within a decade. Relocate before the next season."),
    (30.0, "short-term", "Hazard recurs within a generation. Plan relocation over 1-3 years."),
    (100.5, "medium-term", "Hazard is rare but severe. Restrict new construction; relocate over 3-10 years."),
    (np.inf, "monitor", "No habitation-threatening hazard modelled at the 100-year event."),
)

HAZARDS = ("flood", "landslide", "waterlogging", "erosion", "cloudburst")

# Share of a settlement's population that must be exposed before the hazard is
# treated as governing that settlement's relocation horizon. A quarter is a
# judgement call, published here rather than buried, and it is what stops one
# affected field on a village boundary from classifying the whole village.
MATERIAL_EXPOSURE = 0.25


def horizon_for(return_period_years: float) -> Tuple[str, str]:
    for limit, name, advice in HORIZONS:
        if return_period_years < limit:
            return name, advice
    return HORIZONS[-1][1], HORIZONS[-1][2]


# ---------------------------------------------------------------------------
# per-event hazard layers
# ---------------------------------------------------------------------------

@dataclass
class HazardLayers:
    """Which cells one event renders uninhabitable, hazard by hazard."""
    return_period_years: float
    severity: str
    masks: Dict[str, np.ndarray]        # hazard -> bool, uninhabitable
    intensity: Dict[str, np.ndarray]    # hazard -> 0..1 normalised severity

    @property
    def any_mask(self) -> np.ndarray:
        out = None
        for m in self.masks.values():
            out = m.copy() if out is None else (out | m)
        return out


def hazard_layers(return_period_years: float, severity: str, *,
                  terrain: Terrain,
                  timeline: Optional[FloodTimeline] = None,
                  landslide_affected: Optional[np.ndarray] = None,
                  landslide_susceptibility: Optional[np.ndarray] = None,
                  waterlog_hours: Optional[np.ndarray] = None,
                  erosion_rate: Optional[np.ndarray] = None,
                  cloudburst_affected: Optional[np.ndarray] = None,
                  cloudburst_susceptibility: Optional[np.ndarray] = None
                  ) -> HazardLayers:
    """Reduce one modelled event to per-hazard uninhabitability."""
    shape = terrain.dem.shape
    zeros = np.zeros(shape, np.float32)
    masks: Dict[str, np.ndarray] = {}
    inten: Dict[str, np.ndarray] = {}

    # --- flood: deep enough, or standing long enough ---
    if timeline is not None:
        deep = timeline.peak_depth >= UNINHABITABLE["flood_depth_m"]
        long = ((timeline.duration_hours >= UNINHABITABLE["flood_duration_h"])
                & (timeline.peak_depth >= UNINHABITABLE["flood_duration_depth_m"]))
        masks["flood"] = deep | long
        inten["flood"] = np.clip(timeline.peak_depth / 3.0, 0, 1).astype(np.float32)
    else:
        masks["flood"], inten["flood"] = np.zeros(shape, bool), zeros

    # --- landslide: inside the runout, not merely on a susceptible slope ---
    if landslide_affected is not None:
        masks["landslide"] = landslide_affected
        inten["landslide"] = (landslide_susceptibility
                              if landslide_susceptibility is not None else zeros)
    else:
        masks["landslide"], inten["landslide"] = np.zeros(shape, bool), zeros

    # --- waterlogging: standing water past the point of habitability ---
    if waterlog_hours is not None:
        days = waterlog_hours / 24.0
        masks["waterlogging"] = days >= UNINHABITABLE["waterlog_days"]
        inten["waterlogging"] = np.clip(days / 30.0, 0, 1).astype(np.float32)
    else:
        masks["waterlogging"], inten["waterlogging"] = np.zeros(shape, bool), zeros

    # --- coastal erosion: land actively being lost ---
    if erosion_rate is not None:
        masks["erosion"] = erosion_rate >= UNINHABITABLE["erosion_m_per_year"]
        inten["erosion"] = np.clip(erosion_rate / 6.0, 0, 1).astype(np.float32)
    else:
        masks["erosion"], inten["erosion"] = np.zeros(shape, bool), zeros

    # --- cloudburst: in the path of a flash flood from rain that fell upstream ---
    if cloudburst_affected is not None:
        masks["cloudburst"] = cloudburst_affected
        inten["cloudburst"] = (cloudburst_susceptibility
                               if cloudburst_susceptibility is not None else zeros)
    else:
        masks["cloudburst"], inten["cloudburst"] = np.zeros(shape, bool), zeros

    return HazardLayers(return_period_years=return_period_years,
                        severity=severity, masks=masks, intensity=inten)


# ---------------------------------------------------------------------------
# recurrence across events
# ---------------------------------------------------------------------------

@dataclass
class RedZones:
    """The standing multi-hazard assessment for a district."""
    return_period: np.ndarray        # years to the first uninhabitable event; inf if none
    unsuitability: np.ndarray        # 0..1 composite
    dominant: np.ndarray             # int index into HAZARDS, -1 if none
    horizon: np.ndarray              # int index into HORIZONS
    per_hazard_rp: Dict[str, np.ndarray]
    events: List[dict]

    @property
    def is_red(self) -> np.ndarray:
        """Land where a habitation-threatening hazard recurs inside a century."""
        return np.isfinite(self.return_period)

    def dominant_name(self, r: int, c: int) -> Optional[str]:
        d = int(self.dominant[r, c])
        return HAZARDS[d] if d >= 0 else None

    def summary(self, cell_km2: float, mask: Optional[np.ndarray] = None) -> dict:
        sel = mask if mask is not None else np.ones(self.return_period.shape, bool)
        red = self.is_red & sel
        out = {
            "method": ("Recurrence-based: each cell records the shortest return "
                       "period at which any hazard renders it uninhabitable."),
            "events_modelled": self.events,
            "red_zone_area_km2": round(float(red.sum()) * cell_km2, 2),
            "red_zone_fraction": round(float(red.sum()) / max(int(sel.sum()), 1), 4),
            "by_horizon_km2": {},
            "by_dominant_hazard_km2": {},
            "thresholds": UNINHABITABLE,
        }
        for i, (_, name, _) in enumerate(HORIZONS):
            out["by_horizon_km2"][name] = round(
                float(((self.horizon == i) & sel).sum()) * cell_km2, 2)
        for i, h in enumerate(HAZARDS):
            out["by_dominant_hazard_km2"][h] = round(
                float(((self.dominant == i) & red).sum()) * cell_km2, 2)
        return out


def build(events: Sequence[HazardLayers],
          multi_hazard_premium: float = 0.12) -> RedZones:
    """Combine several modelled events into a standing red-zone assessment.

    ``events`` must be ordered from the most frequent (shortest return period)
    to the rarest. A cell's return period is the shortest one at which any
    hazard renders it uninhabitable, which is the number a planner needs: it
    answers "how often does this place become unliveable" rather than "how bad
    was one particular flood".
    """
    if not events:
        raise ValueError("at least one modelled event is required")

    ordered = sorted(events, key=lambda e: e.return_period_years)
    shape = ordered[0].masks["flood"].shape

    rp = np.full(shape, np.inf, np.float64)
    per_hazard: Dict[str, np.ndarray] = {
        h: np.full(shape, np.inf, np.float64) for h in HAZARDS}
    worst_int = np.zeros(shape, np.float32)
    dominant = np.full(shape, -1, np.int8)

    for ev in ordered:
        for h in HAZARDS:
            m = ev.masks[h]
            fresh = m & ~np.isfinite(per_hazard[h])
            per_hazard[h][fresh] = ev.return_period_years
        hit = ev.any_mask
        fresh = hit & ~np.isfinite(rp)
        rp[fresh] = ev.return_period_years

        # The dominant hazard is the one with the highest intensity at the
        # event that first made the cell uninhabitable.
        for i, h in enumerate(HAZARDS):
            better = fresh & ev.masks[h] & (ev.intensity[h] > worst_int)
            worst_int = np.where(better, ev.intensity[h], worst_int)
            dominant = np.where(better, i, dominant)

    # Unsuitability rises as the return period falls. Log scaling, because the
    # difference between 5 and 25 years matters far more to a planner than the
    # difference between 75 and 95.
    with np.errstate(divide="ignore"):
        freq = np.where(np.isfinite(rp), np.log(100.0 / np.maximum(rp, 1.0))
                        / np.log(100.0), 0.0)
    freq = np.clip(freq, 0, 1)

    n_hazards = sum(np.isfinite(per_hazard[h]).astype(np.float32) for h in HAZARDS)
    premium = np.clip((n_hazards - 1.0), 0, 3) * multi_hazard_premium

    unsuit = np.clip(0.72 * freq + 0.28 * worst_int + premium, 0, 1).astype(np.float32)
    unsuit = np.where(np.isfinite(rp), unsuit, 0.0)

    horizon = np.full(shape, len(HORIZONS) - 1, np.int8)
    for i in range(len(HORIZONS) - 1, -1, -1):
        horizon = np.where(rp < HORIZONS[i][0], i, horizon)

    return RedZones(
        return_period=rp, unsuitability=unsuit, dominant=dominant,
        horizon=horizon,
        per_hazard_rp={h: per_hazard[h] for h in HAZARDS},
        events=[{"severity": e.severity,
                 "return_period_years": e.return_period_years} for e in ordered],
    )


# ---------------------------------------------------------------------------
# habitation-level rollup
# ---------------------------------------------------------------------------

@dataclass
class Habitation:
    """A settlement assessed for relocation need."""
    id: str
    label: str
    lat: float
    lon: float
    population: int
    population_in_red: int
    fraction_in_red: float
    return_period_years: float      # weighted by population actually exposed
    earliest_rp: float              # first affected cell anywhere in the settlement
    dominant_hazard: Optional[str]
    hazards_present: List[str]
    unsuitability: float
    horizon: str
    advice: str
    priority_score: float
    rank: int = 0
    vulnerability: Optional[dict] = None

    def to_dict(self) -> dict:
        rp = self.return_period_years
        return {
            "id": self.id, "label": self.label, "rank": self.rank,
            "vulnerability": self.vulnerability,
            "lat": round(self.lat, 5), "lon": round(self.lon, 5),
            "population": self.population,
            "population_in_red_zone": self.population_in_red,
            "fraction_in_red_zone": round(self.fraction_in_red, 3),
            "return_period_years": None if not np.isfinite(rp) else round(rp, 1),
            "earliest_hazard_return_period_years": (
                None if not np.isfinite(self.earliest_rp) else round(self.earliest_rp, 1)),
            "material_exposure_threshold": MATERIAL_EXPOSURE,
            "dominant_hazard": self.dominant_hazard,
            "hazards_present": self.hazards_present,
            "unsuitability": round(self.unsuitability, 3),
            "relocation_horizon": self.horizon,
            "advice": self.advice,
            "priority_score": round(self.priority_score, 1),
        }


def assess_habitations(grid: Grid, exposure: Exposure, red: RedZones,
                       radius_cells: int = 4,
                       vulnerability: Optional[dict] = None) -> List[Habitation]:
    """Roll the raster assessment up to named settlements and rank them.

    Priority weights how many people are exposed, how often, how badly — and
    **how able they are to cope**. A large village exposed every five years
    outranks a hamlet exposed once a century, which is the ordering a State
    Disaster Management Authority needs to fund relocation against.

    ``vulnerability`` is the district's social vulnerability record from
    :mod:`app.data.vulnerability`. Without it the ranking silently treats a
    kachcha-housed, low-literacy settlement as equivalent to a well-served one
    of the same size, which is exactly what the word *vulnerable* in the problem
    statement is there to prevent.
    """
    out: List[Habitation] = []
    n = grid.n
    for s in exposure.settlements:
        r0, r1 = max(0, s.row - radius_cells), min(n, s.row + radius_cells + 1)
        c0, c1 = max(0, s.col - radius_cells), min(n, s.col + radius_cells + 1)
        sel = np.zeros(red.return_period.shape, bool)
        sel[r0:r1, c0:c1] = True
        sel &= exposure.district_mask

        pop = float(exposure.population[sel].sum())
        red_sel = sel & red.is_red
        pop_red = float(exposure.population[red_sel].sum())
        frac = pop_red / max(pop, 1.0)

        if red_sel.any():
            # Population-weighted return period, not the minimum.
            #
            # Taking the minimum meant one flooded field on the edge of a
            # village put the whole settlement into "immediate" — which made
            # every habitation in the district immediate, and a priority list
            # where everything is top priority is not a priority list. The
            # question a planner is really asking is "when does a *substantial
            # part* of this place become uninhabitable", so the reported return
            # period is the shortest one at which at least
            # MATERIAL_EXPOSURE of the settlement's people are in a red cell.
            rp = np.inf
            for candidate in sorted({float(v) for v in
                                     red.return_period[red_sel].ravel()}):
                hit = sel & (red.return_period <= candidate)
                if float(exposure.population[hit].sum()) >= MATERIAL_EXPOSURE * pop:
                    rp = candidate
                    break
            earliest = float(np.min(red.return_period[red_sel]))
            if not np.isfinite(rp):
                # Exposure never reaches the material threshold: the hazard is
                # real but peripheral. Report it one horizon later than the
                # earliest affected cell would suggest, rather than ignoring it.
                rp = earliest * 4.0
            unsuit = float(np.max(red.unsuitability[red_sel]))
            doms = red.dominant[red_sel]
            doms = doms[doms >= 0]
            dominant = (HAZARDS[int(np.bincount(doms).argmax())]
                        if doms.size else None)
            present = [h for h in HAZARDS
                       if np.isfinite(red.per_hazard_rp[h][red_sel]).any()]
        else:
            rp, earliest, unsuit, dominant, present = np.inf, np.inf, 0.0, None, []

        horizon, advice = horizon_for(rp)
        out.append(Habitation(
            id="H%s" % s.id[1:], label=s.label, lat=s.lat, lon=s.lon,
            population=int(round(pop)), population_in_red=int(round(pop_red)),
            fraction_in_red=frac, return_period_years=rp, earliest_rp=earliest,
            dominant_hazard=dominant, hazards_present=present,
            unsuitability=unsuit, horizon=horizon, advice=advice,
            priority_score=0.0, vulnerability=vulnerability))

    # Normalise within the district so the ranking answers "who first, here".
    pop_cap = max((h.population_in_red for h in out), default=1) or 1
    vuln = float((vulnerability or {}).get("index", 0.5))
    for h in out:
        exposure_term = h.population_in_red / pop_cap
        frequency_term = (0.0 if not np.isfinite(h.return_period_years)
                          else float(np.clip(
                              np.log(100.0 / max(h.return_period_years, 1.0))
                              / np.log(100.0), 0, 1)))
        # Vulnerability carries real weight rather than a token share. The
        # problem statement names it alongside hazard intensity, and a
        # population less able to recover should outrank an equally exposed one
        # that can.
        h.priority_score = 100.0 * (0.34 * exposure_term
                                    + 0.26 * frequency_term
                                    + 0.16 * h.unsuitability
                                    + 0.08 * h.fraction_in_red
                                    + 0.16 * vuln)

    out.sort(key=lambda h: -h.priority_score)
    for i, h in enumerate(out, 1):
        h.rank = i
    return out
