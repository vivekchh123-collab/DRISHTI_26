"""State-level rollup, for the authority the problem statement actually names.

SIH26191 asks for "actionable insights to **State Disaster Management
Authorities** for proactive planning". Everything else in this system is built
for a district — one map, one Magistrate, one caseload. An SDMA does not work
that way. It allocates a mitigation budget across every district in the state,
and the question it asks is not "what is happening in Darbhanga" but "of my
thirty-eight districts, which four do I fund this year, and what does it cost".

So this module answers that question and no other: total caseload by relocation
horizon, districts ranked by need, cost banded across the state, and where the
capacity to receive people actually exists.

**Coverage is stated, not implied.** A state where four districts of thirty-eight
are modelled must say so on the same screen as the totals, because a caseload
computed from four districts is not the state's caseload and presenting it as
one would be the most misleading thing this system could do.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from ..data import districts as districts_data
from ..data import vulnerability as vulnerability_data
from . import mitigation as mitigation_mod


@dataclass
class DistrictRow:
    code: str
    name: str
    population: int
    red_zone_km2: float
    red_zone_fraction: float
    population_in_red: int
    immediate: int
    short_term: int
    medium_term: int
    habitations: int
    dominant_hazard: Optional[str]
    vulnerability: float
    relocation_families: int
    cost_low_crore: float
    cost_high_crore: float
    priority_score: float

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name,
            "population_2025_estimate": self.population,
            "red_zone_km2": round(self.red_zone_km2, 1),
            "red_zone_fraction": round(self.red_zone_fraction, 3),
            "population_in_red_zone": self.population_in_red,
            "habitations": self.habitations,
            "by_horizon": {
                "immediate": self.immediate,
                "short_term": self.short_term,
                "medium_term": self.medium_term,
            },
            "dominant_hazard": self.dominant_hazard,
            "vulnerability_index": round(self.vulnerability, 3),
            "relocation_families": self.relocation_families,
            "indicative_cost_crore": {
                "low": round(self.cost_low_crore, 2),
                "high": round(self.cost_high_crore, 2),
            },
            "priority_score": round(self.priority_score, 1),
        }


def _district_row(code: str) -> Optional[DistrictRow]:
    from . import scenario as scenario_mod

    d = districts_data.get(code)
    try:
        a = scenario_mod.assessment(code)
    except Exception:
        return None

    mask = a.exposure.district_mask
    red = a.redzones.is_red & mask
    cell = a.grid.cell_area_km2
    red_km2 = float(red.sum()) * cell
    frac = float(red.sum()) / max(int(mask.sum()), 1)
    pop_red = int(round(float(a.exposure.population[red].sum()))) if red.any() else 0

    habs = a.habitations
    imm = sum(1 for h in habs if h.horizon == "immediate")
    sht = sum(1 for h in habs if h.horizon == "short-term")
    med = sum(1 for h in habs if h.horizon == "medium-term")

    dom = None
    by_haz = a.redzones.summary(cell, mask).get("by_dominant_hazard_km2", {})
    if by_haz:
        top = max(by_haz.items(), key=lambda kv: kv[1])
        dom = top[0] if top[1] > 0 else None

    plans = mitigation_mod.plans_for([h.to_dict() for h in habs], limit=25)
    summary = mitigation_mod.district_summary(plans)
    vuln = float((a.vulnerability or {}).get("index", 0.5))

    # What an SDMA is really ranking: how many people are exposed, how urgently,
    # and how poorly placed they are to cope. Cost is deliberately not in the
    # score — an expensive district is not a lower priority for being expensive.
    score = 100.0 * (
        0.40 * min(pop_red / 200_000.0, 1.0)
        + 0.28 * min((imm * 2 + sht) / 20.0, 1.0)
        + 0.18 * min(frac / 0.5, 1.0)
        + 0.14 * vuln)

    return DistrictRow(
        code=code, name=d.name, population=d.population_2025,
        red_zone_km2=red_km2, red_zone_fraction=frac,
        population_in_red=pop_red,
        immediate=imm, short_term=sht, medium_term=med,
        habitations=len(habs), dominant_hazard=dom, vulnerability=vuln,
        relocation_families=summary["relocation_families"],
        cost_low_crore=summary["indicative_cost"]["low_crore"],
        cost_high_crore=summary["indicative_cost"]["high_crore"],
        priority_score=score)


def build(state: str) -> dict:
    """The sheet an SDMA takes into a budget meeting."""
    codes = [d.code for d in districts_data.DISTRICTS if d.state == state]
    rows = [r for r in (_district_row(c) for c in codes) if r is not None]
    rows.sort(key=lambda r: -r.priority_score)

    total_pop_red = sum(r.population_in_red for r in rows)
    total_families = sum(r.relocation_families for r in rows)
    low = sum(r.cost_low_crore for r in rows)
    high = sum(r.cost_high_crore for r in rows)

    hazard_counts: Dict[str, int] = {}
    for r in rows:
        if r.dominant_hazard:
            hazard_counts[r.dominant_hazard] = hazard_counts.get(r.dominant_hazard, 0) + 1

    vuln = vulnerability_data.STATES.get(state)

    return {
        "state": state,
        "coverage": {
            "districts_modelled": len(rows),
            "note": ("Totals cover the modelled districts only. A state has "
                     "many more, and a caseload computed from a subset is not "
                     "the state's caseload."),
        },
        "totals": {
            "red_zone_km2": round(sum(r.red_zone_km2 for r in rows), 1),
            "population_in_red_zone": total_pop_red,
            "habitations": sum(r.habitations for r in rows),
            "immediate": sum(r.immediate for r in rows),
            "short_term": sum(r.short_term for r in rows),
            "medium_term": sum(r.medium_term for r in rows),
            "relocation_families": total_families,
            "indicative_cost_crore": {"low": round(low, 2), "high": round(high, 2)},
        },
        "dominant_hazards": hazard_counts,
        "social_indicators": ({
            "literacy_pct": vuln.literacy_pct,
            "sc_st_pct": vuln.sc_st_pct,
            "source": vuln.source,
            "source_url": vuln.source_url,
        } if vuln else None),
        "districts": [r.to_dict() for r in rows],
        "how_ranked": (
            "Population in Red Zone (0.40), urgency weighted to the immediate "
            "horizon (0.28), share of district affected (0.18) and social "
            "vulnerability (0.14). Cost is deliberately excluded: an expensive "
            "district is not a lower priority for being expensive."),
    }


def states_with_coverage() -> List[dict]:
    counts: Dict[str, int] = {}
    for d in districts_data.DISTRICTS:
        counts[d.state] = counts.get(d.state, 0) + 1
    return [{"state": s, "districts_modelled": n}
            for s, n in sorted(counts.items())]


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def habitations_csv(state: Optional[str] = None,
                    code: Optional[str] = None) -> str:
    """Ranked habitations as CSV.

    An insight that cannot leave the screen is not actionable. An SDMA needs a
    sheet it can put in front of a finance department, and that means a file,
    not a dashboard.
    """
    from . import scenario as scenario_mod

    if code:
        codes = [code]
    elif state:
        codes = [d.code for d in districts_data.DISTRICTS if d.state == state]
    else:
        codes = [d.code for d in districts_data.DISTRICTS]

    buf = io.StringIO()
    buf.write("district_code,district,state,rank,habitation,latitude,longitude,"
              "population,population_in_red_zone,fraction_in_red_zone,"
              "return_period_years,dominant_hazard,relocation_horizon,"
              "vulnerability_index,priority_score,strategy,families,"
              "cost_low_crore,cost_high_crore,lead_authorities\n")

    for c in codes:
        try:
            a = scenario_mod.assessment(c)
        except Exception:
            continue
        d = districts_data.get(c)
        vuln = float((a.vulnerability or {}).get("index", 0.5))
        for h in a.habitations:
            hd = h.to_dict()
            plan = mitigation_mod.plan_for(hd)
            pd = plan.to_dict()
            leads = sorted({m["lead_authority"]["id"]
                            for fam in pd["measures_by_family"].values()
                            for m in fam})
            cost = pd.get("cost_estimate") or {}
            rp = hd.get("return_period_years")
            buf.write(",".join([
                c, _q(d.name), _q(d.state), str(hd["rank"]), _q(hd["label"]),
                "%.5f" % hd["lat"], "%.5f" % hd["lon"],
                str(hd["population"]), str(hd["population_in_red_zone"]),
                "%.3f" % hd["fraction_in_red_zone"],
                "" if rp is None else "%.1f" % rp,
                hd.get("dominant_hazard") or "",
                hd.get("relocation_horizon") or "",
                "%.3f" % vuln, "%.1f" % hd["priority_score"],
                pd["strategy"], str(pd["families"]),
                "%.2f" % cost.get("low_crore", 0.0),
                "%.2f" % cost.get("high_crore", 0.0),
                _q(" ".join(leads)),
            ]) + "\n")
    return buf.getvalue()


def _q(s: str) -> str:
    """Quote a CSV field only when it needs it."""
    s = str(s or "")
    if any(ch in s for ch in ',"\n'):
        return '"' + s.replace('"', '""') + '"'
    return s
