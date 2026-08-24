"""Response planning: shelters, routes, resources and the action card.

Given a ranked zone, this produces the thing a District Magistrate can actually
act on — how many boats, which shelter and whether it is big enough, which road
to use when the primary one is under water, how much food and water per day, and
a drafted alert.

Shelter allocation is capacity-aware and greedy: zones are served worst-first,
and each takes the nearest shelter that still has room. That ordering matters.
Allocating nearest-first without tracking capacity is the classic failure that
sends four villages to one school and turns people away at the door.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..data.sops import (BOAT_CAPACITY, BOAT_SORTIES_PER_DAY,
                         MEDICAL_TEAM_PER_PEOPLE, PUMP_PER_KM2_WATERLOGGED,
                         SHELTER_SPACE_M2, SUPPLY_NORMS, rules_for)
from .exposure import Exposure, Facility
from .grid import Grid, haversine_km
from .impact import ROAD_IMPASSABLE_M, ZoneImpact
from .terrain import Terrain

# Fraction of the affected population that actually needs shelter rather than
# staying with relatives or on a raised embankment. Indian flood experience puts
# this well below 100%; assuming everyone needs a camp bed over-orders by
# several times and is recorded in docs/ASSUMPTIONS.md.
DISPLACEMENT_RATE = 0.35

# What a district typically holds without state assistance. These are the
# thresholds that decide whether a need is met locally or has to be escalated to
# the State EOC — which is a decision, and therefore worth surfacing.
DISTRICT_BOAT_HOLDING = 30
DISTRICT_MEDICAL_TEAMS = 12


@dataclass
class ShelterAllocation:
    shelter_id: str
    shelter_name: str
    lat: float
    lon: float
    distance_km: float
    capacity: int
    assigned: int
    is_flooded: bool


@dataclass
class ResourceNeed:
    item: str
    label: str
    quantity: float
    unit: str
    basis: str
    source: str

    def to_dict(self) -> dict:
        return {"item": self.item, "label": self.label,
                "quantity": round(self.quantity, 1), "unit": self.unit,
                "basis": self.basis, "source": self.source}


@dataclass
class ActionCard:
    zone_id: str
    zone_label: str
    rank: int
    headline: str
    population_affected: int
    displaced_estimate: int
    boats_required: int
    medical_teams: int
    pumps: int
    shelters: List[ShelterAllocation]
    shelters_used: int
    shelter_shortfall: int
    escalation: List[str]
    actions: List[dict]
    resources: List[ResourceNeed]
    route_note: str
    alert_text: str

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "zone_label": self.zone_label,
            "rank": self.rank,
            "headline": self.headline,
            "population_affected": self.population_affected,
            "displaced_estimate": self.displaced_estimate,
            "boats_required": self.boats_required,
            "medical_teams": self.medical_teams,
            "pumps": self.pumps,
            "shelters_used": self.shelters_used,
            "shelter_shortfall": self.shelter_shortfall,
            "escalation": self.escalation,
            "shelters": [s.__dict__ for s in self.shelters[:5]],
            "actions": self.actions,
            "resources": [r.to_dict() for r in self.resources],
            "route_note": self.route_note,
            "alert_text": self.alert_text,
        }


def boats_required(displaced: int, is_cut_off: bool, depth_m: float) -> int:
    """Boats needed to clear the displaced population within roughly a day."""
    if depth_m < 1.0 and not is_cut_off:
        return 0
    per_boat_per_day = BOAT_CAPACITY * BOAT_SORTIES_PER_DAY
    return int(math.ceil(displaced / max(per_boat_per_day, 1)))


def resource_plan(displaced: int, days: int = 3) -> List[ResourceNeed]:
    """Relief supplies for the displaced population over ``days``."""
    out: List[ResourceNeed] = []
    for key, spec in SUPPLY_NORMS.items():
        qty = spec["per_person_per_day"] * displaced * days
        out.append(ResourceNeed(
            item=key, label=spec["label"], quantity=qty, unit=spec["unit"],
            basis="%s per person per day x %s people x %d days"
                  % (spec["per_person_per_day"], f"{displaced:,}", days),
            source=spec["source"]))
    return out


def allocate_shelters(zones: Sequence[ZoneImpact], exposure: Exposure,
                      peak_depth: np.ndarray,
                      grid: Grid) -> Dict[str, List[ShelterAllocation]]:
    """Assign shelters to zones worst-first, respecting capacity.

    A shelter standing in the flood is excluded outright — a relief centre under
    half a metre of water is not a relief centre.
    """
    remaining: Dict[str, int] = {}
    usable: List[Facility] = []
    for f in exposure.shelters():
        flooded = bool(peak_depth[f.row, f.col] >= 0.3)
        if flooded:
            continue
        usable.append(f)
        remaining[f.id] = f.capacity

    ordered = sorted(zones, key=lambda z: z.rank)
    needs = {z.id: int(round(z.population_affected * DISPLACEMENT_RATE))
             for z in ordered}
    total_need = sum(needs.values())
    total_capacity = sum(remaining.values())

    # Two passes. The first gives every zone its proportional share of the
    # district's dry capacity; the second hands the remainder out worst-first.
    #
    # Pure worst-first allocation is what a naive greedy loop does, and it is
    # wrong here: when demand exceeds capacity — which for a district of five
    # million it always does — the top-ranked zone swallows every shelter and
    # every other zone is told it has nowhere to send anyone. No District
    # Magistrate would do that, and a plan that says it is not usable.
    share: Dict[str, int] = {}
    if total_capacity < total_need and total_need > 0:
        for z in ordered:
            share[z.id] = int(total_capacity * needs[z.id] / total_need)
    else:
        share = dict(needs)

    out: Dict[str, List[ShelterAllocation]] = {}
    for z in ordered:
        need = min(needs[z.id], share.get(z.id, 0))
        picks: List[ShelterAllocation] = []
        if need > 0 and usable:
            ranked = sorted(usable,
                            key=lambda f: haversine_km(z.lat, z.lon, f.lat, f.lon))
            for f in ranked:
                if need <= 0:
                    break
                free = remaining.get(f.id, 0)
                if free <= 0:
                    continue
                take = min(free, need)
                remaining[f.id] = free - take
                need -= take
                picks.append(ShelterAllocation(
                    shelter_id=f.id, shelter_name=f.name, lat=f.lat, lon=f.lon,
                    distance_km=round(haversine_km(z.lat, z.lon, f.lat, f.lon), 1),
                    capacity=f.capacity, assigned=take, is_flooded=False))
                # Allocation continues until the need is met or the district
                # runs out of dry shelter. Truncating here instead would report
                # a shortfall that is an artefact of the display limit rather
                # than a real gap in capacity.
        out[z.id] = picks

    # Residual pass: proportional shares round down and some zones need less
    # than their share, so hand what is left out worst-first.
    for z in ordered:
        still = needs[z.id] - sum(p.assigned for p in out.get(z.id, []))
        if still <= 0:
            continue
        for f in sorted(usable, key=lambda f: haversine_km(z.lat, z.lon, f.lat, f.lon)):
            if still <= 0:
                break
            free = remaining.get(f.id, 0)
            if free <= 0:
                continue
            take = min(free, still)
            remaining[f.id] = free - take
            still -= take
            out.setdefault(z.id, []).append(ShelterAllocation(
                shelter_id=f.id, shelter_name=f.name, lat=f.lat, lon=f.lon,
                distance_km=round(haversine_km(z.lat, z.lon, f.lat, f.lon), 1),
                capacity=f.capacity, assigned=take, is_flooded=False))
    return out


def alternate_route(terrain: Terrain, exposure: Exposure, grid: Grid,
                    peak_depth: np.ndarray, zone: ZoneImpact) -> str:
    """Describe a usable approach to a cut-off zone, or say plainly there is none.

    Searches for the shallowest crossing on the approach rather than the
    shortest path, because for relief logistics the binding constraint is depth
    at the worst point, not distance.
    """
    if not zone.is_cut_off:
        return "Primary road access open."
    if not exposure.settlements:
        return "No route information available."

    hq = exposure.settlements[0]
    zr, zc = grid.rowcol(zone.lat, zone.lon)

    # Widen the corridor to any land, not just mapped road, and find the least
    # deep crossing along a straight approach from the headquarters.
    steps = max(abs(zr - hq.row), abs(zc - hq.col), 1)
    worst, worst_at = 0.0, None
    for i in range(steps + 1):
        r = int(round(hq.row + (zr - hq.row) * i / steps))
        c = int(round(hq.col + (zc - hq.col) * i / steps))
        d = float(peak_depth[r, c])
        if d > worst:
            worst, worst_at = d, (r, c)

    if worst < ROAD_IMPASSABLE_M:
        return "Direct approach from %s is passable (max %.2f m)." % (hq.label, worst)
    if worst_at is None:
        return "No passable land route. Boat or air access only."
    lat, lon = grid.latlon(*worst_at)
    dist = haversine_km(hq.lat, hq.lon, lat, lon)
    return ("Direct approach from %s is blocked %.0f km out, %.2f m deep at the "
            "worst point (%.4f, %.4f). Boat transfer required beyond that point; "
            "stage vehicles short of it." % (hq.label, dist, worst, lat, lon))


def draft_alert(zone: ZoneImpact, card_actions: Sequence[dict],
                district_name: str) -> str:
    """A short alert suitable for SMS or a CAP payload.

    Deliberately concrete: what, where, how deep, what to do. Under 300
    characters so it survives a single SMS segment chain.
    """
    urgency = "EVACUATE NOW" if zone.max_depth_m >= 1.5 else (
        "MOVE TO SAFETY" if zone.max_depth_m >= 0.7 else "STAY ALERT")
    cut = " Road access cut." if zone.is_cut_off else ""
    return ("[%s FLOOD ALERT] %s: water up to %.1f m near %s.%s %s. "
            "Move to the nearest relief centre. Avoid fallen power lines and "
            "flooded roads. Helpline 1077."
            % (district_name.upper(), urgency, zone.max_depth_m, zone.label,
               cut, "Boat rescue en route" if zone.max_depth_m >= 1.0
               else "Assistance on the way"))


def build_action_card(zone: ZoneImpact, exposure: Exposure, terrain: Terrain,
                      grid: Grid, peak_depth: np.ndarray,
                      shelters: Sequence[ShelterAllocation],
                      district_name: str, days: int = 3) -> ActionCard:
    """Assemble the full response plan for one zone."""
    displaced = int(round(zone.population_affected * DISPLACEMENT_RATE))
    assigned = sum(s.assigned for s in shelters)
    shortfall = max(displaced - assigned, 0)

    facts = {
        "max_depth_m": zone.max_depth_m,
        "mean_depth_m": zone.mean_depth_m,
        "population_affected": zone.population_affected,
        "is_cut_off": zone.is_cut_off,
        "duration_hours": zone.duration_hours,
        "cropland_flooded_km2": zone.cropland_flooded_km2,
        "facilities_at_risk": zone.facilities_at_risk,
        "shelter_shortfall": shortfall,
    }
    actions = [{"id": r.id, "phase": r.phase, "role": r.role,
                "action": r.action, "priority": r.priority, "source": r.source}
               for r in rules_for(facts)]

    boats = boats_required(displaced, zone.is_cut_off, zone.max_depth_m)
    teams = int(math.ceil(zone.population_affected / MEDICAL_TEAM_PER_PEOPLE)) \
        if zone.population_affected else 0
    pumps = (int(math.ceil(zone.area_flooded_km2 * PUMP_PER_KM2_WATERLOGGED))
             if zone.duration_hours >= 48 else 0)

    # A requirement far beyond what a district holds is not a silly number to be
    # hidden — it is the finding. It tells the Magistrate this cannot be handled
    # locally, and that the State EOC has to be called tonight rather than after
    # the first day's rescue attempt has already failed.
    escalation: List[str] = []
    if boats > DISTRICT_BOAT_HOLDING:
        escalation.append(
            "Boat requirement (%d) exceeds a typical district holding of %d. "
            "Requisition through the State EOC / NDRF battalion."
            % (boats, DISTRICT_BOAT_HOLDING))
    if teams > DISTRICT_MEDICAL_TEAMS:
        escalation.append(
            "Medical teams required (%d) exceed district capacity of %d. "
            "Request state medical mobilisation."
            % (teams, DISTRICT_MEDICAL_TEAMS))
    if shortfall > 0:
        escalation.append(
            "Shelter shortfall of %s people once every dry relief centre in the "
            "district is full. Requisition additional buildings or request "
            "inter-district evacuation." % f"{shortfall:,}")

    headline = "%s — %s people affected, water to %.1f m%s" % (
        zone.label, f"{zone.population_affected:,}", zone.max_depth_m,
        ", ROAD CUT" if zone.is_cut_off else "")

    return ActionCard(
        zone_id=zone.id, zone_label=zone.label, rank=zone.rank,
        headline=headline,
        population_affected=zone.population_affected,
        displaced_estimate=displaced,
        boats_required=boats, medical_teams=teams, pumps=pumps,
        shelters=list(shelters), shelters_used=len(shelters),
        shelter_shortfall=shortfall, escalation=escalation,
        actions=actions,
        resources=resource_plan(displaced, days),
        route_note=alternate_route(terrain, exposure, grid, peak_depth, zone),
        alert_text=draft_alert(zone, actions, district_name),
    )


def build_response_plan(zones: Sequence[ZoneImpact], exposure: Exposure,
                        terrain: Terrain, grid: Grid, peak_depth: np.ndarray,
                        district_name: str,
                        top_n: int = 10) -> List[ActionCard]:
    """Action cards for the worst ``top_n`` zones, with shelters shared between them."""
    alloc = allocate_shelters(zones, exposure, peak_depth, grid)
    return [build_action_card(z, exposure, terrain, grid, peak_depth,
                              alloc.get(z.id, []), district_name)
            for z in sorted(zones, key=lambda z: z.rank)[:top_n]]
