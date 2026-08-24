"""Observed disaster history, and what it says about our modelled Red Zones.

Two jobs. The first is to unify three very different sources into one timeline:
the curated register (:mod:`app.data.disasters`), live cyclone geometry
(:mod:`app.providers.ibtracs`) and current alerts
(:mod:`app.providers.gdacs`). Each event keeps its provenance so the interface
can show where it came from.

The second job matters more. Until now this system has had no way to check
itself against reality: the flood model was validated for internal consistency
and against analytical cases, never against an event that actually happened.
:func:`validate` compares the Red Zones we model with the places disasters
actually occurred.

**The result is reported as measured.** A poor score is a finding about the
model, not a reason to move thresholds until the picture improves. The numbers
here are deliberately accompanied by their sample size, because with a handful
of precisely-located events a high hit rate is encouraging rather than
conclusive, and saying otherwise would be the kind of overclaim this project has
tried throughout to avoid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..data import disasters as disasters_data
from ..data import districts as districts_data
from ..providers import ibtracs as ibtracs_mod

# Events whose location is a single mapped site rather than a district-wide
# footprint. Only these can be tested point-in-polygon; asking whether "the
# Assam floods" fall inside a Red Zone is not a well-posed question, because
# the answer is a whole valley.
POINT_HAZARDS = ("landslide", "glof", "cloudburst")


# ---------------------------------------------------------------------------
# unified timeline
# ---------------------------------------------------------------------------

def events(start: str = "1990-01-01", end: str = "2099-12-31",
           hazards: Optional[Sequence[str]] = None,
           district: Optional[str] = None) -> List[dict]:
    """Curated events in a window, optionally filtered."""
    rows = (disasters_data.for_district(district) if district
            else disasters_data.in_window(start, end))
    out = []
    for e in rows:
        if e.end < start or e.start > end:
            continue
        if hazards and e.hazard not in hazards:
            continue
        out.append(e.to_dict())
    return out


def tracks(start: str = "1990-01-01", end: str = "2099-12-31") -> List[dict]:
    """Observed cyclone tracks in a window, from the baked IBTrACS archive."""
    return ibtracs_mod.tracks_in_window(start, end)


def timeline(start: str = "1990-01-01", end: str = "2099-12-31") -> dict:
    """Everything the timeline screen needs for a window."""
    ev = events(start, end)
    tr = tracks(start, end)
    per_year: Dict[int, int] = {}
    for e in ev:
        per_year[e["year"]] = per_year.get(e["year"], 0) + 1
    for t in tr:
        y = int(t["start"][:4])
        per_year[y] = per_year.get(y, 0) + 1

    return {
        "window": {"start": start, "end": end},
        "events": ev,
        "tracks": tr,
        "counts_per_year": [{"year": y, "count": per_year[y]}
                            for y in sorted(per_year)],
        "sources": {
            "curated": disasters_data.coverage(),
            "ibtracs": ibtracs_mod.baked_meta(),
        },
    }


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

@dataclass
class EventVerdict:
    event_id: str
    name: str
    hazard: str
    date: str
    lat: float
    lon: float
    inside_grid: bool
    in_red_zone: Optional[bool]
    susceptibility: Optional[float]
    percentile: Optional[float]          # the meaningful measure; see validate_district
    return_period_years: Optional[float]
    dominant_hazard: Optional[str]
    nearest_red_km: Optional[float]

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "name": self.name,
            "hazard": self.hazard, "date": self.date,
            "lat": self.lat, "lon": self.lon,
            "inside_grid": self.inside_grid,
            "susceptibility_percentile": self.percentile,
            "susceptibility": self.susceptibility,
            "in_red_zone": self.in_red_zone,
            "return_period_years": self.return_period_years,
            "dominant_hazard": self.dominant_hazard,
            "distance_to_nearest_red_zone_km": self.nearest_red_km,
        }


def _haversine_grid(grid, lat: float, lon: float) -> np.ndarray:
    """Distance in km from a point to every cell centre."""
    lats, lons = grid.meshgrid()
    p1 = math.radians(lat)
    p2 = np.radians(lats)
    dl = np.radians(lons - lon)
    a = (np.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2)
    return 2 * 6371.0088 * np.arcsin(np.clip(np.sqrt(a), 0, 1))


def validate_district(code: str) -> dict:
    """How highly does the model rate the places disasters actually happened?

    The obvious test — is the event inside a Red Zone — turns out to be the
    wrong one, and it is worth saying why rather than quietly using something
    else. Landslide *initiation* is deliberately the top fraction of a per cent
    of a district, because susceptibility is not the same as failure and marking
    every steep slope as a source area produces a map that means nothing. Asking
    whether one event lands in 48 cells out of 102,400 is a test that even a
    perfect model fails almost always. Run as-is it returned a hit rate of zero
    while placing all three events within 3.6 km of a Red Zone.

    The measure the landslide literature actually uses is the **susceptibility
    percentile at known event locations**, compared against the background
    distribution — the basis of the success-rate and ROC curves used to score
    susceptibility maps. A random point scores 50 by construction. A model with
    skill puts real events well above that.

    Both numbers are returned. The percentile is the one that means something.
    """
    from . import scenario as scenario_mod

    a = scenario_mod.assessment(code)
    grid, red = a.grid, a.redzones
    susc = a.landslide.susceptibility
    mask = a.exposure.district_mask
    background = susc[mask]
    verdicts: List[EventVerdict] = []

    for e in disasters_data.for_district(code):
        if e.hazard not in POINT_HAZARDS:
            continue
        inside = (grid.lat0 <= e.lat <= grid.lat1
                  and grid.lon0 <= e.lon <= grid.lon1)
        if not inside:
            verdicts.append(EventVerdict(
                e.id, e.name, e.hazard, e.start, e.lat, e.lon,
                False, None, None, None, None, None, None))
            continue

        r, c = grid.rowcol(e.lat, e.lon)
        value = float(susc[r, c])
        pct = (float((background <= value).mean() * 100.0)
               if background.size else None)
        is_red = bool(red.is_red[r, c])
        rp = float(red.return_period[r, c])
        dist = None
        if not is_red and red.is_red.any():
            dist = float(_haversine_grid(grid, e.lat, e.lon)[red.is_red].min())
        verdicts.append(EventVerdict(
            e.id, e.name, e.hazard, e.start, e.lat, e.lon, True, is_red,
            round(value, 3), round(pct, 1) if pct is not None else None,
            round(rp, 1) if np.isfinite(rp) else None,
            red.dominant_name(r, c),
            round(dist, 2) if dist is not None else None))

    tested = [v for v in verdicts if v.inside_grid]
    hits = [v for v in tested if v.in_red_zone]
    pcts = [v.percentile for v in tested if v.percentile is not None]
    mean_pct = round(sum(pcts) / len(pcts), 1) if pcts else None

    return {
        "district": code,
        "point_events_available": len(verdicts),
        "point_events_tested": len(tested),
        "mean_susceptibility_percentile": mean_pct,
        "chance_baseline_percentile": 50.0,
        "above_chance": None if mean_pct is None else mean_pct > 50.0,
        "in_red_zone": len(hits),
        "hit_rate": round(len(hits) / len(tested), 3) if tested else None,
        "verdicts": [v.to_dict() for v in verdicts],
        "method": (
            "Susceptibility percentile at the event location against the "
            "district background — the success-rate approach used to score "
            "landslide susceptibility maps. A random point scores 50. Only "
            "point-located hazards are tested; a basin-wide flood has no single "
            "coordinate, and testing a district centroid against a "
            "district-wide Red Zone would inflate the score meaninglessly."),
        "why_hit_rate_is_not_the_measure": (
            "Initiation is deliberately a fraction of a per cent of the "
            "district. A point-in-Red-Zone test asks whether one event landed "
            "in tens of cells out of a hundred thousand, which a perfect model "
            "would also usually fail. It is reported for completeness only."),
        "caveat": ("Small sample. With a handful of located events a good mean "
                   "percentile is encouraging, not conclusive."),
    }


def validate_all() -> dict:
    """Validation across every modelled district, plus a correlation test.

    Two independent checks, because each is weak on its own:

    * **Point-in-Red-Zone** is direct but rests on very few events.
    * **Rank correlation** between how many disasters a district has actually
      suffered and how much of it we mark as Red Zone uses every event in the
      register. It cannot confirm any individual zone, but if the sign came out
      negative — the worst-hit districts scoring the smallest Red Zones — the
      model would be telling us something is badly wrong.
    """
    from . import scenario as scenario_mod

    per_district, tested, hits = [], 0, 0
    counts, fractions, all_pcts = [], [], []

    for d in districts_data.DISTRICTS:
        try:
            res = validate_district(d.code)
        except Exception:
            continue
        if res["point_events_tested"]:
            tested += res["point_events_tested"]
            hits += res["in_red_zone"]
            all_pcts.extend(v["susceptibility_percentile"] for v in res["verdicts"]
                            if v.get("susceptibility_percentile") is not None)
        per_district.append({
            "code": d.code, "name": d.name,
            "tested": res["point_events_tested"],
            "mean_percentile": res["mean_susceptibility_percentile"],
            "in_red_zone": res["in_red_zone"],
        })

        a = scenario_mod.assessment(d.code)
        frac = float((a.redzones.is_red & a.exposure.district_mask).sum()
                     / max(int(a.exposure.district_mask.sum()), 1))
        counts.append(len(disasters_data.for_district(d.code)))
        fractions.append(frac)

    rho = _spearman(counts, fractions) if len(counts) > 3 else None
    return {
        "susceptibility_test": {
            "districts": [p for p in per_district if p["tested"]],
            "events_tested": tested,
            "mean_percentile": (round(sum(all_pcts) / len(all_pcts), 1)
                                if all_pcts else None),
            "chance_baseline": 50.0,
            "above_chance": (bool(sum(all_pcts) / len(all_pcts) > 50.0)
                             if all_pcts else None),
            "per_event_percentiles": sorted(round(p, 1) for p in all_pcts),
            "reading": ("A random point scores 50 by construction. Higher means "
                        "the model rates the places disasters actually happened "
                        "above the ground around them."),
            "also_reported": {
                "in_red_zone": hits,
                "note": ("Point-in-Red-Zone, reported for completeness. "
                         "Initiation covers a fraction of a per cent of a "
                         "district by design, so this test is near-impossible "
                         "to pass and is not the measure of skill."),
            },
        },
        "correlation_test": {
            "spearman_rho": None if rho is None else round(rho, 3),
            "n_districts": len(counts),
            "question": ("Do districts with more recorded disasters have larger "
                         "modelled Red Zones?"),
            "reading": _read_rho(rho),
        },
        "honesty": ("Reported as measured. A weak result here is a finding "
                    "about the model, not a reason to retune thresholds until "
                    "the picture improves."),
    }


def _spearman(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Spearman rank correlation, with average ranks for ties."""
    n = len(a)
    if n < 3 or len(b) != n:
        return None

    def ranks(values):
        order = sorted(range(n), key=lambda i: values[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    ra, rb = ranks(list(a)), ranks(list(b))
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra)
                    * sum((y - mb) ** 2 for y in rb))
    return num / den if den > 0 else None


def _read_rho(rho: Optional[float]) -> str:
    if rho is None:
        return "not enough districts to compute"
    if rho >= 0.5:
        return "strong positive — worse-hit districts do carry larger Red Zones"
    if rho >= 0.2:
        return "weak positive — the expected direction, but not decisive"
    if rho > -0.2:
        return ("no relationship — the model is not tracking observed disaster "
                "burden at district level")
    return ("NEGATIVE — worse-hit districts carry smaller Red Zones. This would "
            "indicate a real problem with the model and must be investigated, "
            "not presented")
