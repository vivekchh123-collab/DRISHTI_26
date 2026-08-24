"""Response doctrine: supply norms and the standard-operating-procedure rules.

Every number and every action in this file is traceable to a published source —
NDMA's flood management guidelines, the Incident Response System, the Indian
Public Health Standards, or the Sphere humanitarian standards. The ``source``
field is carried through the API to the screen, so any instruction the dashboard
gives can be traced back to the document it came from.

That traceability is the point. A District Magistrate ordering an evacuation has
to be able to justify it afterwards, and "the software recommended it" is not a
justification. "NDMA flood SOP, mandatory evacuation above 1.5 m" is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# relief supply norms, per affected person per day unless stated
# ---------------------------------------------------------------------------

SUPPLY_NORMS: Dict[str, dict] = {
    "drinking_water_litres": {
        "per_person_per_day": 3.0,
        "label": "Drinking water",
        "unit": "litres",
        "source": "Sphere Handbook — survival minimum for drinking",
    },
    "domestic_water_litres": {
        "per_person_per_day": 15.0,
        "label": "Water (all uses)",
        "unit": "litres",
        "source": "Sphere Handbook — basic domestic water supply",
    },
    "food_packets": {
        "per_person_per_day": 2.0,
        "label": "Cooked meal packets",
        "unit": "packets",
        "source": "NDMA relief norms — two cooked meals per day",
    },
    "dry_ration_kg": {
        "per_person_per_day": 0.6,
        "label": "Dry ration",
        "unit": "kg",
        "source": "NDMA relief norms — rice/pulses equivalent, 2100 kcal",
    },
    "ors_sachets": {
        "per_person_per_day": 0.2,
        "label": "ORS sachets",
        "unit": "sachets",
        "source": "IPHS — diarrhoeal disease prophylaxis in flood relief camps",
    },
    "halogen_tablets": {
        "per_person_per_day": 2.0,
        "label": "Halogen tablets",
        "unit": "tablets",
        "source": "IPHS — household water treatment",
    },
    "bleaching_powder_kg": {
        "per_person_per_day": 0.01,
        "label": "Bleaching powder",
        "unit": "kg",
        "source": "IPHS — well and surround disinfection",
    },
}

# Capacity assumptions used when converting need into units of resource.
BOAT_CAPACITY = 12                 # persons per NDRF/SDRF inflatable sortie
BOAT_SORTIES_PER_DAY = 14          # realistic round trips per boat per day
MEDICAL_TEAM_PER_PEOPLE = 5000     # one team per 5,000 affected (IPHS)
SHELTER_SPACE_M2 = 3.5             # covered area per person (Sphere)
PUMP_PER_KM2_WATERLOGGED = 1.5     # dewatering pumps per km^2 of standing water


@dataclass(frozen=True)
class ActionRule:
    """One conditional instruction.

    ``applies`` is a predicate over a plain dict of zone facts, which keeps the
    rules declarative and independently testable — each one can be exercised
    without running the flood model.
    """
    id: str
    phase: str          # "warning" | "response" | "recovery"
    role: str           # who owns it
    action: str
    priority: str       # "immediate" | "urgent" | "routine"
    source: str
    applies: Callable[[dict], bool]


def _depth(f: dict) -> float:
    return float(f.get("max_depth_m", 0.0))


def _pop(f: dict) -> int:
    return int(f.get("population_affected", 0))


RULES: Tuple[ActionRule, ...] = (
    # ---------------- evacuation ----------------
    ActionRule(
        "EVAC-MANDATORY", "response", "District Magistrate / SDRF",
        "Order mandatory evacuation. Water is above survivable standing depth; "
        "do not wait for people to self-evacuate.",
        "immediate", "NDMA Flood Management Guidelines — evacuation triggers",
        lambda f: _depth(f) >= 1.5,
    ),
    ActionRule(
        "EVAC-CUTOFF", "response", "District Magistrate / SDRF",
        "Order mandatory evacuation — road access is closing and the window to "
        "move people by vehicle is about to shut.",
        "immediate", "NDMA Flood Management Guidelines — isolation criterion",
        lambda f: f.get("is_cut_off") and _depth(f) >= 0.7,
    ),
    ActionRule(
        "EVAC-ADVISE", "response", "Block Development Officer",
        "Issue evacuation advisory and move vulnerable groups — elderly, "
        "pregnant women, disabled, children under five — to the relief centre.",
        "urgent", "NDMA Flood Management Guidelines — advisory triggers",
        lambda f: 0.7 <= _depth(f) < 1.5 and not f.get("is_cut_off"),
    ),
    ActionRule(
        "EVAC-STANDBY", "warning", "Block Development Officer",
        "Put the zone on evacuation standby. Identify vulnerable households and "
        "confirm relief-centre readiness.",
        "routine", "NDMA Flood Management Guidelines — preparedness",
        lambda f: 0.3 <= _depth(f) < 0.7,
    ),

    # ---------------- rescue assets ----------------
    ActionRule(
        "BOAT-DEPLOY", "response", "SDRF / NDRF",
        "Deploy motorised rescue boats. Depth exceeds wading limit, so all "
        "movement in this zone is by water.",
        "immediate", "Incident Response System — rescue resource deployment",
        lambda f: _depth(f) >= 1.0,
    ),
    ActionRule(
        "AIRLIFT-REQUEST", "response", "District Magistrate",
        "Request air support through the State EOC. The zone is isolated with a "
        "large affected population and cannot be served by boat alone.",
        "immediate", "NDMA — air support requisition through State EOC",
        lambda f: f.get("is_cut_off") and _pop(f) >= 25_000,
    ),

    # ---------------- health ----------------
    ActionRule(
        "HEALTH-CAMP", "response", "Chief Medical Officer",
        "Establish a medical camp with ORS, chlorination and snakebite "
        "antivenom. Snakebite and diarrhoeal disease dominate flood morbidity.",
        "urgent", "IPHS — flood relief camp health services",
        lambda f: _pop(f) >= 5_000,
    ),
    ActionRule(
        "HEALTH-FACILITY", "response", "Chief Medical Officer",
        "Relocate patients and drugs from the inundated health facilities in "
        "this zone and re-route referrals.",
        "immediate", "IPHS — continuity of essential health services",
        lambda f: bool(f.get("facilities_at_risk")),
    ),
    ActionRule(
        "WATER-DISINFECT", "recovery", "PHED",
        "Disinfect all hand pumps and open wells before restoring supply. "
        "Contaminated shallow groundwater is the main post-flood disease route.",
        "urgent", "IPHS / PHED — post-flood water quality protocol",
        lambda f: _depth(f) >= 0.5,
    ),

    # ---------------- lifelines ----------------
    ActionRule(
        "POWER-ISOLATE", "response", "Electricity Distribution Company",
        "Isolate distribution transformers and low-tension lines in the "
        "inundated area. Live conductors in standing water are a leading cause "
        "of flood fatalities.",
        "immediate", "NDMA — utility safety during inundation",
        lambda f: _depth(f) >= 0.5,
    ),
    ActionRule(
        "ROAD-ALT", "response", "Public Works Department",
        "Publish an alternative route and post traffic marshals. The primary "
        "road into this zone is below the vehicle-safe depth.",
        "urgent", "IRS — logistics and route management",
        lambda f: f.get("is_cut_off"),
    ),

    # ---------------- relief and recovery ----------------
    ActionRule(
        "RELIEF-CAMP", "response", "District Magistrate",
        "Open additional relief centres. Assessed displacement exceeds the "
        "capacity already open in this zone.",
        "urgent", "NDMA — relief camp management",
        lambda f: f.get("shelter_shortfall", 0) > 0,
    ),
    ActionRule(
        "CROP-ASSESS", "recovery", "District Agriculture Officer",
        "Begin crop loss enumeration for input subsidy and insurance claims "
        "under PMFBY while the inundation record is still verifiable.",
        "routine", "NDMA — post-flood damage assessment",
        lambda f: float(f.get("cropland_flooded_km2", 0.0)) >= 1.0,
    ),
    ActionRule(
        "DEWATER", "recovery", "Municipal Body / PHED",
        "Deploy dewatering pumps. Water in this zone will not drain by gravity "
        "within an acceptable period.",
        "urgent", "NDMA — urban flood management",
        lambda f: int(f.get("duration_hours", 0)) >= 48 and _depth(f) >= 0.3,
    ),
    ActionRule(
        "FODDER", "recovery", "Animal Husbandry Department",
        "Establish cattle camps and move fodder. Livestock loss is a principal "
        "driver of household destitution after a flood.",
        "routine", "NDMA — livestock relief norms",
        lambda f: float(f.get("cropland_flooded_km2", 0.0)) >= 2.0,
    ),
)


def rules_for(facts: dict, phase: Optional[str] = None) -> List[ActionRule]:
    """Every rule whose condition the zone satisfies, most urgent first."""
    order = {"immediate": 0, "urgent": 1, "routine": 2}
    out = [r for r in RULES
           if (phase is None or r.phase == phase) and r.applies(facts)]
    return sorted(out, key=lambda r: order.get(r.priority, 9))
