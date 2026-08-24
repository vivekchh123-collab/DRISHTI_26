"""Mitigation measures, the authority accountable for each, and its legal basis.

The flood SOPs in :mod:`app.data.sops` answer "the water is rising, what do I do
tonight". This file answers the different and slower question SIH26191 actually
asks: *this place is unfit to live in — what is done about it, by whom, and
under what statute.*

**Relocation is a last resort, not the default.** NDMA's National Disaster
Management Plan and the National Disaster Management Guidelines both treat
resettlement as the measure of last resort, after structural protection and
land-use regulation have been considered and found insufficient. A system that
recommends moving every exposed habitation would be both naive and unaffordable:
displacing a village severs livelihoods, land rights and social networks, and it
costs an order of magnitude more than a retaining wall. So the rules below
choose between three families of response —

* **structural protection** — keep the people, change the hazard
* **non-structural regulation** — keep the people, change what may be built
* **relocation** — move the people, when neither of the above can work

— and only escalate to the third where the hazard recurs fast enough and hard
enough that protection cannot realistically hold.

**Every rule names a real authority and a real instrument.** The chain in India
is specific: a District Disaster Management Authority is chaired by the District
Magistrate and derives its powers from Sections 30 and 31 of the Disaster
Management Act 2005; resettlement entitlements are governed by the Second
Schedule of the RFCTLARR Act 2013; diverting forest land needs clearance under
the Forest (Conservation) Act 1980. An instruction that does not say who signs
it is not actionable, and one that cites no instrument cannot survive audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# authorities
# ---------------------------------------------------------------------------

NATIONAL, STATE, DISTRICT, LOCAL = "national", "state", "district", "local"


@dataclass(frozen=True)
class Authority:
    """An institution that can actually be held to a decision."""
    id: str
    name: str
    level: str
    remit: str

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "level": self.level,
                "remit": self.remit}


AUTHORITIES: Tuple[Authority, ...] = (
    Authority("NDMA", "National Disaster Management Authority", NATIONAL,
              "National policy, guidelines and mitigation funding norms."),
    Authority("NDRF", "National Disaster Response Force", NATIONAL,
              "Specialist response deployment on state requisition."),
    Authority("GSI", "Geological Survey of India", NATIONAL,
              "Landslide hazard zonation and site-specific slope investigation."),
    Authority("CWC", "Central Water Commission", NATIONAL,
              "Flood forecasting, basin studies and flood-plain zoning advice."),
    Authority("NCCR", "National Centre for Coastal Research", NATIONAL,
              "Shoreline change assessment and coastal protection advice."),

    Authority("SDMA", "State Disaster Management Authority", STATE,
              "State Disaster Management Plan, mitigation fund allocation, "
              "and approval of relocation proposals."),
    Authority("SEC", "State Executive Committee", STATE,
              "Executes SDMA decisions across state departments."),
    Authority("REV", "Revenue Department", STATE,
              "Land records, title, acquisition and compensation."),
    Authority("PWD", "Public Works Department", STATE,
              "Roads, public buildings and protection works at the new site."),
    Authority("PHED", "Public Health Engineering Department", STATE,
              "Water supply and sanitation at resettlement sites."),
    Authority("WRD", "Water Resources / Irrigation Department", STATE,
              "Embankments, drainage, river training and their maintenance."),
    Authority("FOREST", "State Forest Department", STATE,
              "Forest land diversion, afforestation and bio-engineering."),
    Authority("TCP", "Town and Country Planning Department", STATE,
              "Master plans, zoning and building bye-law enforcement."),
    Authority("HOUSING", "State Housing Board / Rural Development Dept", STATE,
              "Construction of resettlement housing, PMAY-G convergence."),

    Authority("DDMA", "District Disaster Management Authority", DISTRICT,
              "District Disaster Management Plan; chaired by the District "
              "Magistrate. Powers under DM Act 2005 sections 30-31."),
    Authority("DM", "District Magistrate / Collector", DISTRICT,
              "Executive orders, land acquisition, and prohibitory orders."),
    Authority("TEHSIL", "Tehsildar / Revenue Circle Officer", DISTRICT,
              "Field-level land records, beneficiary verification, mutation."),

    Authority("GP", "Gram Panchayat", LOCAL,
              "Gram Sabha consent, beneficiary lists, and site upkeep."),
    Authority("ULB", "Urban Local Body / Municipal Corporation", LOCAL,
              "Building permission, storm drainage, and encroachment removal."),
    Authority("VDMC", "Village Disaster Management Committee", LOCAL,
              "Community early warning, drills and first response."),
)

BY_ID: Dict[str, Authority] = {a.id: a for a in AUTHORITIES}


# ---------------------------------------------------------------------------
# legal and policy instruments
# ---------------------------------------------------------------------------

INSTRUMENTS: Dict[str, str] = {
    "DM-ACT-30": "Disaster Management Act 2005, s.30 — powers and functions of "
                 "the District Authority",
    "DM-ACT-31": "Disaster Management Act 2005, s.31 — District Disaster "
                 "Management Plan",
    "DM-ACT-38": "Disaster Management Act 2005, s.38 — responsibilities of "
                 "State Government departments",
    "RFCTLARR-2013": "Right to Fair Compensation and Transparency in Land "
                     "Acquisition, Rehabilitation and Resettlement Act 2013 — "
                     "Second Schedule sets the resettlement entitlements",
    "RFCTLARR-SIA": "RFCTLARR Act 2013, Chapter II — Social Impact Assessment "
                    "and Gram Sabha consultation before acquisition",
    "NDMP-2019": "NDMA National Disaster Management Plan 2019 — mitigation and "
                 "relocation as a measure of last resort",
    "NDMA-FLOOD": "NDMA Guidelines: Management of Floods (2008)",
    "NDMA-LANDSLIDE": "NDMA Guidelines: Management of Landslides and Snow "
                      "Avalanches (2009)",
    "NDMA-CYCLONE": "NDMA Guidelines: Management of Cyclones (2008)",
    "FCA-1980": "Forest (Conservation) Act 1980 — prior clearance to divert "
                "forest land for non-forest use",
    "CRZ-2019": "Coastal Regulation Zone Notification 2019 — construction "
                "restrictions within the coastal zone",
    "MBBL-2016": "Model Building Bye-Laws 2016 — hazard-based construction "
                 "restrictions, Chapter on natural hazard safety",
    "PMAY-G": "Pradhan Mantri Awaas Yojana (Gramin) — rural housing "
              "convergence for resettlement construction",
    "SDMF": "State Disaster Mitigation Fund — Fifteenth Finance Commission "
            "mitigation window",
    "IS-14496": "BIS IS 14496 (Part 2): 1998 — landslide hazard zonation",
}


# ---------------------------------------------------------------------------
# measures
# ---------------------------------------------------------------------------

STRUCTURAL, REGULATORY, RELOCATION, PREPAREDNESS = (
    "structural", "regulatory", "relocation", "preparedness")


@dataclass(frozen=True)
class MitigationRule:
    """One measure, its owner, its statutory basis and when it applies.

    ``applies`` is a predicate over a flat dict of habitation facts, which keeps
    the rules declarative and independently testable — every rule can be
    exercised without running the hazard model at all.
    """
    id: str
    family: str                  # structural | regulatory | relocation | preparedness
    measure: str
    rationale: str
    lead: str                    # Authority.id
    supporting: Tuple[str, ...]  # Authority.id
    instrument: str              # INSTRUMENTS key
    horizon: str                 # immediate | short-term | medium-term | ongoing
    applies: Callable[[dict], bool]

    def to_dict(self) -> dict:
        lead = BY_ID.get(self.lead)
        return {
            "id": self.id,
            "family": self.family,
            "measure": self.measure,
            "rationale": self.rationale,
            "lead_authority": lead.to_dict() if lead else {"id": self.lead},
            "supporting_authorities": [
                BY_ID[a].to_dict() for a in self.supporting if a in BY_ID],
            "legal_basis": INSTRUMENTS.get(self.instrument, self.instrument),
            "instrument_id": self.instrument,
            "horizon": self.horizon,
        }


def _rp(f: dict) -> float:
    v = f.get("return_period_years")
    return float(v) if v is not None else float("inf")


def _pop(f: dict) -> int:
    return int(f.get("population_in_red_zone", 0) or 0)


def _hz(f: dict) -> str:
    return str(f.get("dominant_hazard") or "")


def _horizon(f: dict) -> str:
    return str(f.get("relocation_horizon") or "")


RULES: Tuple[MitigationRule, ...] = (

    # ------------------------------------------------------------------
    # relocation — only where the hazard recurs too fast to defend against
    # ------------------------------------------------------------------
    MitigationRule(
        "REL-PROPOSE", RELOCATION,
        "Prepare a resettlement proposal for the habitation and place it before "
        "the SDMA for sanction.",
        "The hazard recurs within a decade. Protection works are designed to a "
        "return period; a hazard this frequent will overtop or undercut them "
        "within their design life, and rebuilding after each event costs more "
        "than moving once.",
        "DDMA", ("SDMA", "REV", "DM"), "NDMP-2019", "immediate",
        lambda f: _horizon(f) == "immediate" and _pop(f) > 0,
    ),
    MitigationRule(
        "REL-SIA", RELOCATION,
        "Commission the Social Impact Assessment and convene the Gram Sabha "
        "before any land is identified.",
        "Consent is a statutory precondition, not a courtesy. Resettlement "
        "carried out without it has repeatedly failed in India — people return "
        "to the hazard zone because the new site was chosen without them.",
        "REV", ("GP", "DM", "TEHSIL"), "RFCTLARR-SIA", "immediate",
        lambda f: _horizon(f) in ("immediate", "short-term") and _pop(f) > 0,
    ),
    MitigationRule(
        "REL-ENTITLE", RELOCATION,
        "Compute the resettlement entitlement for every affected family against "
        "the Second Schedule and publish the beneficiary list.",
        "Entitlements are statutory and itemised — housing, a one-time grant, "
        "transport, livelihood support. Publishing the list early is what "
        "prevents the disputes that stall resettlement for years.",
        "REV", ("TEHSIL", "GP"), "RFCTLARR-2013", "short-term",
        lambda f: _horizon(f) in ("immediate", "short-term") and _pop(f) > 0,
    ),
    MitigationRule(
        "REL-SITE", RELOCATION,
        "Secure the identified relocation site and verify its title, access and "
        "water supply before any construction is sanctioned.",
        "A resettlement site without a road and a water source is abandoned "
        "within two seasons. Site suitability is assessed by this system, but "
        "title and services have to be verified on the ground.",
        "REV", ("PWD", "PHED", "TEHSIL"), "RFCTLARR-2013", "short-term",
        lambda f: _horizon(f) in ("immediate", "short-term") and _pop(f) > 0,
    ),
    MitigationRule(
        "REL-FOREST", RELOCATION,
        "Apply for forest land diversion clearance if the identified site falls "
        "on forest land.",
        "In hill and coastal districts the safe ground is very often forest "
        "land, and clearance under the Forest (Conservation) Act takes months. "
        "Starting it late is the single most common cause of delay.",
        "FOREST", ("REV", "SDMA"), "FCA-1980", "short-term",
        lambda f: _horizon(f) in ("immediate", "short-term")
        and _hz(f) in ("landslide", "erosion"),
    ),
    MitigationRule(
        "REL-HOUSING", RELOCATION,
        "Converge PMAY-G and State Disaster Mitigation Fund allocations to "
        "build at the new site.",
        "Resettlement housing is rarely funded from one head. Convergence is "
        "how it is actually paid for.",
        "HOUSING", ("SDMA", "GP"), "PMAY-G", "medium-term",
        lambda f: _horizon(f) in ("immediate", "short-term") and _pop(f) >= 100,
    ),
    MitigationRule(
        "REL-VACATE", RELOCATION,
        "Record the vacated land as hazard-prone in the revenue record and "
        "prohibit re-occupation.",
        "Without a revenue entry the vacated site is reoccupied — often by the "
        "same families, sometimes within a year. This is why relocation "
        "programmes are found to have failed a decade later.",
        "REV", ("DM", "GP", "TEHSIL"), "DM-ACT-30", "medium-term",
        lambda f: _horizon(f) in ("immediate", "short-term") and _pop(f) > 0,
    ),

    # ------------------------------------------------------------------
    # structural protection — keep the people, change the hazard
    # ------------------------------------------------------------------
    MitigationRule(
        "STR-EMBANK", STRUCTURAL,
        "Survey the embankment reach protecting this habitation and programme "
        "strengthening or raising.",
        "Where the hazard recurs on a generational rather than a decadal "
        "timescale, protection is cheaper and far less disruptive than moving "
        "people. An embankment designed to the right return period is the "
        "correct answer here, not relocation.",
        "WRD", ("CWC", "DDMA"), "NDMA-FLOOD", "short-term",
        lambda f: _hz(f) == "flood" and _horizon(f) == "short-term",
    ),
    MitigationRule(
        "STR-DRAIN", STRUCTURAL,
        "Desilt and augment the drainage channels serving this habitation "
        "before the next monsoon.",
        "Waterlogging is a drainage-capacity problem, not a river problem. It "
        "is one of the few hazards that can be substantially fixed with "
        "maintenance rather than capital works.",
        "WRD", ("ULB", "GP"), "DM-ACT-38", "short-term",
        lambda f: _hz(f) == "waterlogging",
    ),
    MitigationRule(
        "STR-SLOPE", STRUCTURAL,
        "Commission a GSI slope-stability investigation and design slope "
        "stabilisation — retaining structures with surface and subsurface "
        "drainage.",
        "Most slope failures are triggered by pore pressure, so drainage is "
        "usually more effective per rupee than a retaining wall alone. The "
        "investigation has to precede the design.",
        "GSI", ("PWD", "DDMA"), "NDMA-LANDSLIDE", "short-term",
        lambda f: _hz(f) == "landslide" and _horizon(f) in ("short-term",
                                                           "medium-term"),
    ),
    MitigationRule(
        "STR-BIOENG", STRUCTURAL,
        "Programme bio-engineering — deep-rooted planting and contour bunding — "
        "on the slope above the habitation.",
        "Root reinforcement adds real cohesion to the soil mantle and costs a "
        "fraction of concrete. It is slow, which is why it belongs on a "
        "medium-term horizon rather than an immediate one.",
        "FOREST", ("GSI", "GP"), "NDMA-LANDSLIDE", "medium-term",
        lambda f: _hz(f) == "landslide",
    ),
    MitigationRule(
        "STR-COAST", STRUCTURAL,
        "Assess shoreline protection options — mangrove restoration first, hard "
        "structures only where it cannot work.",
        "Hard coastal defences move erosion along the coast rather than "
        "removing it, so the neighbouring village pays for this one. Mangrove "
        "and dune restoration do not have that transfer effect.",
        "NCCR", ("FOREST", "WRD"), "CRZ-2019", "medium-term",
        lambda f: _hz(f) == "erosion",
    ),

    # ------------------------------------------------------------------
    # regulatory — keep the people, change what may be built
    # ------------------------------------------------------------------
    MitigationRule(
        "REG-NOBUILD", REGULATORY,
        "Notify the Red Zone as a no-construction area and stop issuing "
        "building permissions inside it.",
        "The cheapest possible mitigation, and the one most often skipped. "
        "Every permit issued inside a Red Zone adds to the caseload that will "
        "later have to be relocated at public expense.",
        "TCP", ("DM", "ULB", "GP"), "MBBL-2016", "immediate",
        lambda f: _rp(f) <= 100.0 and _pop(f) > 0,
    ),
    MitigationRule(
        "REG-ZONING", REGULATORY,
        "Incorporate the Red Zone boundary into the district master plan and "
        "the District Disaster Management Plan at its next revision.",
        "A hazard map that is not in the statutory plan has no legal force and "
        "will not be consulted when a permit is issued.",
        "DDMA", ("TCP", "SDMA"), "DM-ACT-31", "medium-term",
        lambda f: _rp(f) <= 100.0,
    ),
    MitigationRule(
        "REG-CRZ", REGULATORY,
        "Enforce Coastal Regulation Zone setbacks against the measured "
        "shoreline retreat rate, not the historic shoreline.",
        "A setback measured from where the coast used to be is not a setback. "
        "Where retreat is metres per year the line has to move with it.",
        "TCP", ("NCCR", "ULB"), "CRZ-2019", "short-term",
        lambda f: _hz(f) == "erosion",
    ),

    # ------------------------------------------------------------------
    # preparedness — for everywhere that is exposed at all
    # ------------------------------------------------------------------
    MitigationRule(
        "PRE-EWS", PREPAREDNESS,
        "Extend the early warning last mile to this habitation and confirm the "
        "receiving contact.",
        "Warnings fail at the last mile far more often than at the forecast. A "
        "named person who answers is worth more than another model.",
        "VDMC", ("DDMA", "GP"), "NDMP-2019", "immediate",
        lambda f: _rp(f) <= 100.0 and _pop(f) > 0,
    ),
    MitigationRule(
        "PRE-DRILL", PREPAREDNESS,
        "Hold a pre-monsoon evacuation drill on the identified route and "
        "confirm the shelter is usable.",
        "A shelter nobody has walked to is a shelter nobody reaches at night in "
        "the rain.",
        "VDMC", ("DDMA", "GP"), "DM-ACT-31", "ongoing",
        lambda f: _rp(f) <= 30.0 and _pop(f) > 0,
    ),
    MitigationRule(
        "PRE-MONITOR", PREPAREDNESS,
        "Keep the habitation under periodic hazard review; re-assess after any "
        "significant event or land-use change.",
        "A Red Zone is not permanent. Deforestation, new construction upslope "
        "and shoreline retreat all change it, which is why the assessment is "
        "re-run rather than filed.",
        "DDMA", ("SDMA",), "DM-ACT-31", "ongoing",
        lambda f: _rp(f) <= 100.0,
    ),
)


def rules_for(facts: dict, family: Optional[str] = None) -> List[MitigationRule]:
    """Every measure that applies to one habitation.

    A rule that raises on unexpected facts is skipped rather than taking the
    whole plan down: a missing field should cost one recommendation, not the
    page an officer is reading.
    """
    out = []
    for r in RULES:
        if family and r.family != family:
            continue
        try:
            if r.applies(facts):
                out.append(r)
        except Exception:
            continue
    return out


def families() -> Tuple[str, ...]:
    return (RELOCATION, STRUCTURAL, REGULATORY, PREPAREDNESS)


def authorities_involved(rules: List[MitigationRule]) -> List[dict]:
    """Distinct authorities across a set of measures, with what each leads."""
    seen: Dict[str, dict] = {}
    for r in rules:
        for aid, role in [(r.lead, "lead")] + [(s, "supporting") for s in r.supporting]:
            a = BY_ID.get(aid)
            if a is None:
                continue
            entry = seen.setdefault(aid, {**a.to_dict(), "leads": [], "supports": []})
            (entry["leads"] if role == "lead" else entry["supports"]).append(r.id)
    order = {NATIONAL: 0, STATE: 1, DISTRICT: 2, LOCAL: 3}
    return sorted(seen.values(), key=lambda e: (order.get(e["level"], 9), e["name"]))
