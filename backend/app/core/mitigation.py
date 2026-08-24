"""Mitigation planning: what is done about a Red Zone, by whom, and in what order.

Turns a ranked habitation into an accountable plan. Three things make this
different from a list of suggestions:

**It chooses a strategy before it chooses measures.** Relocation is the last
resort in NDMA doctrine, and the choice between protecting a place and moving it
turns on how often the hazard recurs relative to the design life of a protection
work. A hazard that returns every five years will defeat an embankment within
its design life; one that returns every eighty will not. That comparison is made
explicitly in :func:`strategy_for` rather than left implicit in a threshold.

**Every measure names an accountable authority and a statute.** An instruction
that does not say who signs it is not actionable, and one that cites no
instrument cannot survive an audit. The authorities and instruments are real —
DDMA under sections 30 and 31 of the Disaster Management Act, entitlements under
the Second Schedule of RFCTLARR 2013, forest diversion under the Forest
(Conservation) Act 1980.

**Costs are order-of-magnitude and say so.** A planning figure that pretends to
rupee precision is worse than one that admits its band, because the false
precision is what gets quoted back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from ..data import mitigations as mitigations_data
from ..data.mitigations import MitigationRule

# Indicative unit costs for scale, not for tender. Sources are the CPWD plinth
# area rates and PMAY-G unit assistance in the ranges published for hill and
# plain areas; they move every year and every state applies its own schedule.
# The point of carrying them at all is that "relocate 400 families" and "build
# 2 km of embankment" are not comparable until both are in rupees.
UNIT_COSTS_INR = {
    "resettlement_per_family": 450_000,     # house, plot development, services
    "site_development_per_family": 120_000,  # roads, water, drainage, power
    "embankment_per_km": 25_000_000,
    "slope_stabilisation_per_km": 40_000_000,
    "drainage_per_km": 6_000_000,
    "bioengineering_per_hectare": 250_000,
    "coastal_protection_per_km": 60_000_000,
}

PERSONS_PER_FAMILY = 4.8        # Census 2011 average rural household size


@dataclass
class CostEstimate:
    low_inr: float
    high_inr: float
    basis: str

    def to_dict(self) -> dict:
        return {
            "low_inr": round(self.low_inr),
            "high_inr": round(self.high_inr),
            "low_crore": round(self.low_inr / 1e7, 2),
            "high_crore": round(self.high_inr / 1e7, 2),
            "basis": self.basis,
            "precision": ("Order of magnitude, for prioritisation only. Unit "
                          "rates vary by state schedule and revise annually; "
                          "this is not a tender estimate."),
        }


@dataclass
class MitigationPlan:
    habitation_id: str
    label: str
    strategy: str
    strategy_reason: str
    horizon: str
    population: int
    families: int
    measures: List[MitigationRule]
    cost: Optional[CostEstimate]

    def to_dict(self) -> dict:
        by_family: Dict[str, List[dict]] = {}
        for r in self.measures:
            by_family.setdefault(r.family, []).append(r.to_dict())
        return {
            "habitation_id": self.habitation_id,
            "label": self.label,
            "strategy": self.strategy,
            "strategy_reason": self.strategy_reason,
            "horizon": self.horizon,
            "population_in_red_zone": self.population,
            "families": self.families,
            "measures_by_family": by_family,
            "measure_count": len(self.measures),
            "authorities": mitigations_data.authorities_involved(self.measures),
            "cost_estimate": self.cost.to_dict() if self.cost else None,
        }


# ---------------------------------------------------------------------------
# strategy
# ---------------------------------------------------------------------------

# Design life of a typical protection work. An embankment, retaining wall or
# drain is built to a return period and maintained for decades; a hazard that
# recurs faster than this will defeat it inside its own service life.
PROTECTION_DESIGN_LIFE_YEARS = 25.0


def strategy_for(facts: dict) -> tuple:
    """Choose between protecting a place and moving it.

    Returns ``(strategy, reason)``. The comparison is between how often the
    hazard recurs and how long a protection work is expected to last, which is
    the question an engineer would actually ask — and it is stated in the
    output so a planner can disagree with the reasoning rather than just the
    conclusion.
    """
    rp = facts.get("return_period_years")
    hazard = facts.get("dominant_hazard") or "hazard"
    pop = int(facts.get("population_in_red_zone", 0) or 0)

    if rp is None:
        return ("monitor",
                "No habitation-threatening hazard modelled within a century. "
                "Keep under periodic review.")
    rp = float(rp)

    if pop <= 0:
        return ("regulate",
                "The Red Zone here carries no resident population. Prevent "
                "construction rather than plan a move.")

    if hazard == "erosion":
        return ("relocate",
                "Coastal erosion removes the land itself. Protection can slow "
                "retreat but cannot restore ground, and hard structures shift "
                "the erosion to the next village along the coast.")

    if rp <= 10.0:
        return ("relocate",
                "The hazard recurs roughly every %.0f years, well inside the "
                "%.0f-year service life of a protection work. Anything built "
                "to defend this habitation would be overtopped or undercut "
                "several times before it was due for replacement."
                % (rp, PROTECTION_DESIGN_LIFE_YEARS))

    if rp <= 30.0:
        return ("protect",
                "The hazard recurs about every %.0f years, comparable to the "
                "service life of a protection work. Structural protection plus "
                "a construction ban is cheaper and far less disruptive than "
                "moving %d people." % (rp, pop))

    return ("regulate",
            "The hazard is rare — about one in %.0f years — but severe. The "
            "proportionate response is to stop new construction and keep the "
            "existing population prepared, not to relocate them." % rp)


STRATEGY_FAMILIES = {
    "relocate": (mitigations_data.RELOCATION, mitigations_data.REGULATORY,
                 mitigations_data.PREPAREDNESS),
    "protect": (mitigations_data.STRUCTURAL, mitigations_data.REGULATORY,
                mitigations_data.PREPAREDNESS),
    "regulate": (mitigations_data.REGULATORY, mitigations_data.PREPAREDNESS),
    "monitor": (mitigations_data.PREPAREDNESS,),
}


# ---------------------------------------------------------------------------
# costing
# ---------------------------------------------------------------------------

def estimate_cost(strategy: str, facts: dict, families: int) -> Optional[CostEstimate]:
    """Order-of-magnitude cost, so options can be compared at all."""
    hazard = facts.get("dominant_hazard") or ""

    if strategy == "relocate":
        per = (UNIT_COSTS_INR["resettlement_per_family"]
               + UNIT_COSTS_INR["site_development_per_family"])
        base = per * families
        return CostEstimate(
            base * 0.8, base * 1.5,
            "%d families x resettlement and site development at "
            "approximately Rs %s per family" % (families, f"{per:,}"))

    if strategy == "protect":
        # Frontage scales with the square root of the settlement's footprint;
        # a village of four times the population is roughly twice as wide.
        km = max(0.5, round((families ** 0.5) * 0.06, 2))
        key = {"landslide": "slope_stabilisation_per_km",
               "waterlogging": "drainage_per_km",
               "erosion": "coastal_protection_per_km"}.get(
                   hazard, "embankment_per_km")
        base = UNIT_COSTS_INR[key] * km
        return CostEstimate(
            base * 0.6, base * 1.8,
            "approximately %.2f km of %s"
            % (km, key.replace("_per_km", "").replace("_", " ")))

    if strategy == "regulate":
        return CostEstimate(
            200_000, 1_500_000,
            "notification, survey and enforcement; no capital works")
    return None


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

def plan_for(habitation: dict) -> MitigationPlan:
    """Build the mitigation plan for one ranked habitation."""
    strategy, reason = strategy_for(habitation)
    allowed = STRATEGY_FAMILIES.get(strategy, (mitigations_data.PREPAREDNESS,))

    measures: List[MitigationRule] = []
    for fam in allowed:
        measures.extend(mitigations_data.rules_for(habitation, family=fam))

    order = {"immediate": 0, "short-term": 1, "medium-term": 2, "ongoing": 3}
    measures.sort(key=lambda r: (order.get(r.horizon, 9), r.family, r.id))

    pop = int(habitation.get("population_in_red_zone", 0) or 0)
    families = max(int(round(pop / PERSONS_PER_FAMILY)), 0)

    return MitigationPlan(
        habitation_id=str(habitation.get("id", "")),
        label=str(habitation.get("label", "")),
        strategy=strategy,
        strategy_reason=reason,
        horizon=str(habitation.get("relocation_horizon") or "monitor"),
        population=pop,
        families=families,
        measures=measures,
        cost=estimate_cost(strategy, habitation, families),
    )


def plans_for(habitations: Sequence[dict], limit: int = 10) -> List[MitigationPlan]:
    return [plan_for(h) for h in habitations[:limit]]


def district_summary(plans: Sequence[MitigationPlan]) -> dict:
    """Roll plans up to the sheet a District or State authority argues from."""
    by_strategy: Dict[str, int] = {}
    families_by_strategy: Dict[str, int] = {}
    low = high = 0.0
    for p in plans:
        by_strategy[p.strategy] = by_strategy.get(p.strategy, 0) + 1
        families_by_strategy[p.strategy] = (
            families_by_strategy.get(p.strategy, 0) + p.families)
        if p.cost:
            low += p.cost.low_inr
            high += p.cost.high_inr

    all_rules: List[MitigationRule] = []
    for p in plans:
        all_rules.extend(p.measures)

    return {
        "habitations_planned": len(plans),
        "by_strategy": by_strategy,
        "families_by_strategy": families_by_strategy,
        "relocation_families": families_by_strategy.get("relocate", 0),
        "indicative_cost": {
            "low_crore": round(low / 1e7, 2),
            "high_crore": round(high / 1e7, 2),
            "precision": ("Order of magnitude, for prioritisation and budget "
                          "framing only. Not a tender estimate."),
        },
        "authorities": mitigations_data.authorities_involved(all_rules),
        "doctrine": ("Relocation is treated as the measure of last resort, per "
                     "the NDMA National Disaster Management Plan. Where the "
                     "hazard recurs on a timescale a protection work can "
                     "outlive, the plan protects the habitation instead of "
                     "moving it."),
    }
