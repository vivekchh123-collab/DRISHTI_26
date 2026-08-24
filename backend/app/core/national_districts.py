"""National district coverage: all 735, at two clearly-separated tiers.

The problem statement addresses State Disaster Management Authorities, and an
SDMA thinks in districts. A screen that can only offer 27 km grid squares is not
speaking their language, however live the squares are.

So every district in India gets a score. **What it does not get is a pretence
that the score means the same thing everywhere.**

* **Modelled** — the 22 districts with baked elevation. Full physics: HAND, D8
  routing, the BIS landslide rating, recurrence across three return periods,
  exposure and relocation caseload.
* **Screening** — the remaining 713. A live-weather score aggregated from the
  national grid, with no terrain analysis behind it at all.

Screening says *where to look*. It is not a hazard assessment and must never be
displayed as one. Every record carries its ``tier`` and a ``basis`` string
saying in words what stands behind the number, because the failure this module
could produce — a national map where a screening score looks exactly like a
modelled one — would be the most misleading thing in the system.

Promoting a district from screening to modelled is a data task, not a code task:
bake its elevation with ``scripts/fetch_real_dem.py`` and it moves tier.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..data import districts as districts_data

BOUNDARIES = os.environ.get("DRISHTI_DISTRICT_BOUNDARIES") or os.path.join(
    os.path.dirname(__file__), "..", "data", "boundaries", "india_districts.json")

MODELLED, SCREENING = "modelled", "screening"

CACHE_TTL = 1800.0
_LOCK = threading.Lock()
_CACHE: Optional[Tuple[float, dict]] = None
_BOUNDS: Optional[List[dict]] = None


def load_boundaries() -> List[dict]:
    """All 735 district boundaries, or an empty list if not baked."""
    global _BOUNDS
    if _BOUNDS is not None:
        return _BOUNDS
    try:
        with open(BOUNDARIES, encoding="utf-8") as fh:
            _BOUNDS = json.load(fh).get("districts", [])
    except Exception:
        _BOUNDS = []
    return _BOUNDS


def boundaries_meta() -> dict:
    try:
        with open(BOUNDARIES, encoding="utf-8") as fh:
            blob = json.load(fh)
        return {k: v for k, v in blob.items() if k != "districts"}
    except Exception:
        return {}


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


# District names differ between sources, and the mismatches are not typos —
# they are genuine alternative official names. geoBoundaries writes the numeral
# in South 24 Parganas as a word; Balasore appears under its Odia name
# Baleshwar; Nellore under its full name, Sri Potti Sriramulu Nellore. Without
# these three aliases the districts fall silently into the screening tier, which
# is exactly the kind of quiet degradation that is hard to notice and easy to
# present as fact.
NAME_ALIASES: Dict[str, str] = {
    "southtwentyfourparganas": "South 24 Parganas",
    "baleshwar": "Balasore",
    "baleswar": "Balasore",
    "sripottisriramulunellore": "Nellore",
    "spsrnellore": "Nellore",
}


def modelled_index() -> Dict[str, str]:
    """Normalised district name -> our code, for the districts we model."""
    idx = {_norm(d.name): d.code for d in districts_data.DISTRICTS}
    for alias, canonical in NAME_ALIASES.items():
        code = idx.get(_norm(canonical))
        if code:
            idx[alias] = code
    return idx


@dataclass
class DistrictScore:
    id: str
    name: str
    tier: str
    lat: float
    lon: float
    bbox: List[float]
    score: Optional[float]
    drivers: List[str]
    code: Optional[str] = None
    red_zone_km2: Optional[float] = None
    population_in_red: Optional[int] = None
    immediate: Optional[int] = None

    def to_dict(self, include_ring: bool = False,
                ring: Optional[list] = None) -> dict:
        if self.tier == MODELLED:
            basis = ("Full hazard model: HAND inundation, D8 routing, BIS "
                     "landslide rating, recurrence across 5-, 25- and "
                     "100-year events.")
        elif self.score is None:
            basis = ("Screening unavailable - no live weather connection. "
                     "Nothing is known about this district right now.")
        else:
            basis = ("Screening only - live weather aggregated from the "
                     "national grid. No terrain analysis behind this number.")
        out = {
            "id": self.id, "name": self.name, "tier": self.tier,
            "lat": self.lat, "lon": self.lon, "bbox": self.bbox,
            "score": None if self.score is None else round(self.score, 1),
            "drivers": self.drivers, "basis": basis,
        }
        if self.code:
            out.update({
                "code": self.code,
                "red_zone_km2": self.red_zone_km2,
                "population_in_red_zone": self.population_in_red,
                "immediate_habitations": self.immediate,
            })
        if include_ring and ring:
            out["ring"] = ring
        return out


def _cells_in_bbox(cells: Sequence, bbox: List[float]) -> List:
    """Grid cells whose centre falls in a district's bounding box.

    A bounding box rather than point-in-polygon: at 27 km the grid is coarser
    than most districts, so a strict polygon test would leave small districts
    with no cells and a misleading zero.
    """
    w, s, e, n = bbox
    out = []
    for c in cells:
        lat = getattr(c, "lat", None)
        lon = getattr(c, "lon", None)
        if lat is None or lon is None:
            continue
        if s <= lat <= n and w <= lon <= e:
            out.append(c)
    return out


def build(include_rings: bool = False) -> dict:
    """Score every district in India, at whichever tier it qualifies for."""
    from . import national as national_mod
    from . import scenario as scenario_mod

    bounds = load_boundaries()
    if not bounds:
        return {"available": False,
                "reason": ("district boundaries not baked - run "
                           "scripts/fetch_districts.py"),
                "districts": []}

    screen = national_mod.get()
    cells = getattr(screen, "cells", [])
    by_name = modelled_index()

    # Without a live connection every weather field sits at zero, so every
    # screening cell scores zero. Publishing that would render as "no hazard
    # anywhere in India" - a confident, wrong, national all-clear. Offline the
    # honest answer is that screening has nothing to say; the modelled
    # districts still do, because their physics is baked.
    screening_live = bool(getattr(screen, "live", False))

    rows: List[DistrictScore] = []
    ring_by_id: Dict[str, list] = {}

    for b in bounds:
        code = by_name.get(_norm(b["name"]))
        lat, lon = b["centroid"]
        ring_by_id[b["id"]] = b.get("ring", [])

        if code:
            try:
                a = scenario_mod.assessment(code)
            except Exception:
                code = None
            else:
                mask = a.exposure.district_mask
                red = a.redzones.is_red & mask
                cell_km2 = a.grid.cell_area_km2
                pop_red = (int(round(float(a.exposure.population[red].sum())))
                           if red.any() else 0)
                imm = sum(1 for h in a.habitations if h.horizon == "immediate")
                frac = float(red.sum()) / max(int(mask.sum()), 1)
                sc = 100.0 * min(1.0, 0.45 * min(pop_red / 300_000.0, 1.0)
                                 + 0.35 * min(frac / 0.5, 1.0)
                                 + 0.20 * min(imm / 10.0, 1.0))
                rows.append(DistrictScore(
                    id=b["id"], name=b["name"], tier=MODELLED,
                    lat=lat, lon=lon, bbox=b["bbox"], score=sc,
                    drivers=(["%d habitations need immediate relocation" % imm]
                             if imm else ["no immediate relocation caseload"]),
                    code=code, red_zone_km2=round(float(red.sum()) * cell_km2, 1),
                    population_in_red=pop_red, immediate=imm))
                continue

        if not screening_live:
            rows.append(DistrictScore(
                id=b["id"], name=b["name"], tier=SCREENING,
                lat=lat, lon=lon, bbox=b["bbox"], score=None, drivers=[]))
            continue

        inside = _cells_in_bbox(cells, b["bbox"])
        if inside:
            sc = sum(getattr(c, "score", 0.0) for c in inside) / len(inside)
            drivers: List[str] = []
            for c in inside:
                for d in getattr(c, "drivers", []) or []:
                    if d not in drivers:
                        drivers.append(d)
        else:
            sc, drivers = 0.0, []

        rows.append(DistrictScore(
            id=b["id"], name=b["name"], tier=SCREENING,
            lat=lat, lon=lon, bbox=b["bbox"], score=sc, drivers=drivers[:3]))

    rows.sort(key=lambda r: (r.score is None, -(r.score or 0.0)))
    modelled = [r for r in rows if r.tier == MODELLED]

    return {
        "available": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "live": screening_live,
        "screening_available": screening_live,
        "screening_note": (
            "" if screening_live else
            "No live weather connection, so screening districts carry no "
            "score. This is not an all-clear - it is an absence of "
            "information. Modelled districts are unaffected."),
        "counts": {"total": len(rows), "modelled": len(modelled),
                   "screening": len(rows) - len(modelled)},
        "tiers": {
            "modelled": "Full hazard model. Districts with baked elevation.",
            "screening": ("Live weather aggregated from the national grid. "
                          "Says where to look; not a hazard assessment."),
        },
        "promotion": ("A screening district becomes modelled by baking its "
                      "elevation - scripts/fetch_real_dem.py. A data task, not "
                      "a code change."),
        "boundaries": boundaries_meta(),
        "districts": [r.to_dict(include_rings, ring_by_id.get(r.id))
                      for r in rows],
    }


def get(include_rings: bool = False) -> dict:
    """Cached :func:`build`, on the same cycle as the national screen."""
    global _CACHE
    with _LOCK:
        hit = _CACHE
    if hit and (time.time() - hit[0]) < CACHE_TTL and not include_rings:
        return hit[1]
    built = build(include_rings)
    if not include_rings:
        with _LOCK:
            _CACHE = (time.time(), built)
    return built
