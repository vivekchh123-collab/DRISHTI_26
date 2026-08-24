"""Relocation: where the people in a Red Zone can actually go.

Identifying unsafe land is the easy half. The question that stops relocation
programmes in practice is the second half — *is there anywhere to put them, and
does it have room* — and that is what this module answers.

Three things have to be true of a relocation site, and all three are hard
requirements rather than scores:

1. **It is safe.** No modelled hazard renders it uninhabitable at the 100-year
   event. A site that is merely *less* dangerous is not a relocation site.
2. **It is buildable.** Slope, drainage and ground conditions permit permanent
   construction.
3. **It has capacity.** Land area net of what is already occupied, at a density
   people will actually live at.

Everything else — road access, water, distance from the origin village, whether
it takes prime farmland — is scored rather than required, because those are
trade-offs a District Magistrate is entitled to make.

**Distance is treated as a cost, not a constraint, and that is deliberate.**
Resettlement fails when people are moved away from their fields, their work and
their kin; they drift back into the hazard zone within a few seasons. So the
allocator minimises displacement distance subject to safety and capacity, and
reports the distance it could not avoid rather than hiding it.

Densities follow Sphere planned-settlement guidance as an absolute floor
(45 m² per person of total site area) with realistic permanent-resettlement
densities above it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .exposure import Exposure
from .grid import Grid, describe_location, haversine_km
from .redzone import Habitation, RedZones
from .terrain import Terrain, normalize, smooth

# Buildable slope limit for permanent construction, degrees. Above this, hill
# cutting is required — which is itself a landslide driver, so a "safe" site on
# a steep slope is not safe once it is built on.
MAX_BUILD_SLOPE_DEG = 15.0

# Persons per km2 of site area, by how urban the district is. The Sphere minimum
# of 45 m2/person implies ~22,000/km2, which is camp density and not somewhere
# people live permanently; these are settlement densities.
DENSITY_RURAL = 2500.0
DENSITY_URBAN = 8000.0
SPHERE_FLOOR_M2_PER_PERSON = 45.0

# Beyond this, relocation reliably fails: people return to the hazard zone
# because their land and work did not move with them.
COMFORTABLE_MOVE_KM = 8.0
MAX_ACCEPTABLE_MOVE_KM = 25.0


@dataclass
class RelocationSite:
    id: str
    label: str
    lat: float
    lon: float
    row: int
    col: int
    area_km2: float
    capacity: int
    existing_population: int
    suitability: float
    road_distance_km: float
    water_distance_km: float
    cropland_fraction: float
    constraints: List[str]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label,
            "lat": round(self.lat, 5), "lon": round(self.lon, 5),
            "area_km2": round(self.area_km2, 2),
            "capacity": self.capacity,
            "existing_population": self.existing_population,
            "suitability": round(self.suitability, 3),
            "road_distance_km": round(self.road_distance_km, 1),
            "water_distance_km": round(self.water_distance_km, 1),
            "cropland_fraction": round(self.cropland_fraction, 2),
            "constraints": self.constraints,
        }


@dataclass
class SiteAssignment:
    site_id: str
    site_label: str
    lat: float
    lon: float
    distance_km: float
    people: int

    def to_dict(self) -> dict:
        return {"site_id": self.site_id, "site_label": self.site_label,
                "lat": round(self.lat, 5), "lon": round(self.lon, 5),
                "distance_km": round(self.distance_km, 1), "people": self.people}


@dataclass
class RelocationPlan:
    habitation_id: str
    habitation_label: str
    horizon: str
    people_to_move: int
    people_placed: int
    shortfall: int
    mean_distance_km: float
    assignments: List[SiteAssignment]
    notes: List[str]

    def to_dict(self) -> dict:
        return {
            "habitation_id": self.habitation_id,
            "habitation_label": self.habitation_label,
            "relocation_horizon": self.horizon,
            "people_to_move": self.people_to_move,
            "people_placed": self.people_placed,
            "shortfall": self.shortfall,
            "placement_rate": round(
                self.people_placed / max(self.people_to_move, 1), 3),
            "mean_distance_km": round(self.mean_distance_km, 1),
            "assignments": [a.to_dict() for a in self.assignments],
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# suitability
# ---------------------------------------------------------------------------

def _distance_field(mask: np.ndarray, max_cells: int = 40) -> np.ndarray:
    """Cell distance to the nearest true cell in ``mask``."""
    out = np.full(mask.shape, np.inf, np.float32)
    if not mask.any():
        return out
    front = mask.copy()
    out[mask] = 0.0
    for d in range(1, max_cells + 1):
        grown = front.copy()
        grown[:-1, :] |= front[1:, :]
        grown[1:, :] |= front[:-1, :]
        grown[:, :-1] |= front[:, 1:]
        grown[:, 1:] |= front[:, :-1]
        new = grown & ~front
        if not new.any():
            break
        out[new] = float(d)
        front = grown
    return out


def suitability_grid(grid: Grid, terrain: Terrain, exposure: Exposure,
                     red: RedZones) -> Tuple[np.ndarray, np.ndarray, Dict[str, np.ndarray]]:
    """Score every cell as a relocation destination.

    Returns ``(suitability, eligible, components)``. ``eligible`` is the hard
    mask — safe, buildable, on land, inside the district. Cells outside it score
    zero and can never be allocated, whatever else is in their favour.
    """
    mask = exposure.district_mask
    safe = ~red.is_red
    buildable = terrain.slope <= MAX_BUILD_SLOPE_DEG
    land = ~terrain.sea_mask & ~terrain.streams

    eligible = mask & safe & buildable & land

    # Safety margin: a site adjacent to a red zone is legal but poor. Distance
    # from the hazard boundary is worth scoring because hazard maps have error
    # bars and a buffer absorbs them.
    margin = np.clip(_distance_field(red.is_red) / 12.0, 0, 1)
    margin = np.where(np.isfinite(margin), margin, 1.0).astype(np.float32)

    road_d = _distance_field(exposure.road_mask)
    water_d = _distance_field(terrain.streams & ~terrain.sea_mask)

    access = np.clip(1.0 - road_d / 15.0, 0, 1).astype(np.float32)
    # Water access is non-monotonic: too far is a supply problem, too close is a
    # flood problem. The optimum sits a few cells back from the channel.
    water = np.clip(1.0 - np.abs(water_d - 4.0) / 12.0, 0, 1).astype(np.float32)

    gentle = 1.0 - normalize(np.clip(terrain.slope, 0, MAX_BUILD_SLOPE_DEG))
    headroom = np.clip(1.0 - exposure.built_up * 1.8, 0, 1).astype(np.float32)
    # Taking irrigated cropland for housing trades one livelihood for another;
    # penalise it rather than forbidding it.
    farmland = np.clip(1.0 - exposure.cropland * 0.8, 0, 1).astype(np.float32)

    components = {
        "safety_margin": margin, "road_access": access, "water_access": water,
        "buildability": gentle.astype(np.float32), "land_headroom": headroom,
        "farmland_conflict": farmland,
    }
    score = (0.26 * margin + 0.22 * access + 0.16 * water
             + 0.14 * gentle + 0.12 * headroom + 0.10 * farmland)
    score = np.where(eligible, smooth(score.astype(np.float32), radius=1), 0.0)
    return score.astype(np.float32), eligible, components


def sustainable_density(urban_fraction: float) -> float:
    return DENSITY_RURAL + (DENSITY_URBAN - DENSITY_RURAL) * float(
        np.clip(urban_fraction, 0, 1))


def find_sites(grid: Grid, terrain: Terrain, exposure: Exposure, red: RedZones,
               suitability: np.ndarray, eligible: np.ndarray,
               urban_fraction: float, hq_lat: float, hq_lon: float,
               hq_name: str, count: int = 12,
               radius_cells: int = 5) -> List[RelocationSite]:
    """Pick separated candidate sites and size each one's carrying capacity."""
    n = grid.n
    min_sep = max(6, n // 14)
    density = sustainable_density(urban_fraction)

    road_d = _distance_field(exposure.road_mask)
    water_d = _distance_field(terrain.streams & ~terrain.sea_mask)

    picked: List[Tuple[int, int]] = []
    for flat in np.argsort(suitability.ravel())[::-1]:
        r, c = divmod(int(flat), n)
        if suitability[r, c] <= 0.05:
            break
        if all(math.hypot(r - rr, c - cc) >= min_sep for rr, cc in picked):
            picked.append((r, c))
        if len(picked) >= count:
            break

    sites: List[RelocationSite] = []
    for i, (r, c) in enumerate(picked):
        r0, r1 = max(0, r - radius_cells), min(n, r + radius_cells + 1)
        c0, c1 = max(0, c - radius_cells), min(n, c + radius_cells + 1)
        sel = np.zeros(suitability.shape, bool)
        sel[r0:r1, c0:c1] = True
        sel &= eligible
        if not sel.any():
            continue

        area = float(sel.sum()) * grid.cell_area_km2
        existing = float(exposure.population[sel].sum())
        capacity = int(max(area * density - existing, 0))

        constraints: List[str] = []
        crop = float(exposure.cropland[sel].mean())
        rd = float(np.min(road_d[sel])) * grid.cell_m / 1000.0
        wd = float(np.min(water_d[sel])) * grid.cell_m / 1000.0
        if crop > 0.4:
            constraints.append("Predominantly cropland — land acquisition and "
                               "livelihood compensation required")
        if rd > 3.0:
            constraints.append("No road within %.1f km — access road required" % rd)
        if wd > 3.0:
            constraints.append("No surface water within %.1f km — piped supply "
                               "or borewell required" % wd)
        if capacity < 500:
            constraints.append("Limited capacity; suitable for a single hamlet only")

        lat, lon = grid.latlon(r, c)
        sites.append(RelocationSite(
            id="RS%02d" % i,
            label=describe_location(grid, r, c, hq_lat, hq_lon, hq_name),
            lat=lat, lon=lon, row=r, col=c,
            area_km2=area, capacity=capacity,
            existing_population=int(existing),
            suitability=float(suitability[r, c]),
            road_distance_km=rd, water_distance_km=wd,
            cropland_fraction=crop, constraints=constraints))

    sites.sort(key=lambda s: -s.capacity)
    return sites


# ---------------------------------------------------------------------------
# allocation
# ---------------------------------------------------------------------------

HORIZON_ORDER = {"immediate": 0, "short-term": 1, "medium-term": 2, "monitor": 9}


def allocate(habitations: Sequence[Habitation], sites: Sequence[RelocationSite],
             max_sites_per_habitation: int = 3) -> List[RelocationPlan]:
    """Match habitations needing relocation to sites with room.

    Ordered by horizon first and priority second, so the villages that must move
    before the next monsoon get first claim on the nearest capacity. Within a
    habitation, the nearest site with room wins — distance is the cost that
    decides whether a resettlement holds.
    """
    remaining: Dict[str, int] = {s.id: s.capacity for s in sites}
    plans: List[RelocationPlan] = []

    need_move = [h for h in habitations if h.horizon != "monitor"]
    need_move.sort(key=lambda h: (HORIZON_ORDER.get(h.horizon, 9),
                                  -h.priority_score))

    for h in need_move:
        target = h.population_in_red
        placed, assigns, notes = 0, [], []
        if target <= 0:
            plans.append(RelocationPlan(
                habitation_id=h.id, habitation_label=h.label, horizon=h.horizon,
                people_to_move=0, people_placed=0, shortfall=0,
                mean_distance_km=0.0, assignments=[],
                notes=["No population in the red zone."]))
            continue

        ranked = sorted(sites, key=lambda s: haversine_km(h.lat, h.lon, s.lat, s.lon))
        for s in ranked:
            if placed >= target or len(assigns) >= max_sites_per_habitation:
                break
            free = remaining.get(s.id, 0)
            if free <= 0:
                continue
            d = haversine_km(h.lat, h.lon, s.lat, s.lon)
            if d > MAX_ACCEPTABLE_MOVE_KM:
                continue
            take = min(free, target - placed)
            remaining[s.id] = free - take
            placed += take
            assigns.append(SiteAssignment(site_id=s.id, site_label=s.label,
                                          lat=s.lat, lon=s.lon,
                                          distance_km=d, people=take))

        shortfall = max(target - placed, 0)
        mean_d = (sum(a.distance_km * a.people for a in assigns) / max(placed, 1)
                  if assigns else 0.0)

        if shortfall > 0:
            notes.append(
                "No safe site within %d km has room for %s people. Options: "
                "requisition additional land, raise site density, or plan "
                "inter-district resettlement."
                % (MAX_ACCEPTABLE_MOVE_KM, f"{shortfall:,}"))
        if mean_d > COMFORTABLE_MOVE_KM:
            notes.append(
                "Mean displacement of %.1f km exceeds the %.0f km beyond which "
                "resettlement commonly fails and households drift back. Budget "
                "for livelihood support and transport links."
                % (mean_d, COMFORTABLE_MOVE_KM))
        if not assigns:
            notes.append("No eligible site found. This habitation cannot be "
                         "relocated within the district as modelled.")

        plans.append(RelocationPlan(
            habitation_id=h.id, habitation_label=h.label, horizon=h.horizon,
            people_to_move=target, people_placed=placed, shortfall=shortfall,
            mean_distance_km=mean_d, assignments=assigns, notes=notes))

    return plans


def district_headroom(grid: Grid, exposure: Exposure, eligible: np.ndarray,
                      density: float) -> int:
    """Theoretical capacity of *all* safe land, not just the shortlisted sites.

    Two different numbers are needed and confusing them is easy. The site list is
    a practical shortlist — a dozen places a District Magistrate could actually
    acquire and build on. This is the ceiling: every eligible cell, filled to a
    sustainable density, net of who already lives there.

    The gap between them is informative. Where headroom is large but site
    capacity is small, the land exists and the constraint is site assembly.
    Where headroom itself is small, the district is simply full, and relocation
    has to be an inter-district decision taken at state level.
    """
    per_cell = density * grid.cell_area_km2
    free = np.where(eligible, np.maximum(per_cell - exposure.population, 0.0), 0.0)
    return int(free.sum())


@dataclass
class RelocationAssessment:
    suitability: np.ndarray
    eligible: np.ndarray
    sites: List[RelocationSite]
    plans: List[RelocationPlan]
    components: Dict[str, np.ndarray]
    density_per_km2: float
    headroom: int

    def summary(self, cell_km2: float) -> dict:
        to_move = sum(p.people_to_move for p in self.plans)
        placed = sum(p.people_placed for p in self.plans)
        constraint = ("district is at sustainable density — relocation must be "
                      "inter-district and is a state-level decision"
                      if self.headroom < to_move else
                      "safe land exists district-wide; the constraint is site "
                      "assembly and acquisition, not land")
        return {
            "method": ("Hard safety and buildability constraints, then a scored "
                       "trade-off on access, water, land headroom and farmland."),
            "sustainable_density_per_km2": self.density_per_km2,
            "sphere_floor_m2_per_person": SPHERE_FLOOR_M2_PER_PERSON,
            "eligible_area_km2": round(float(self.eligible.sum()) * cell_km2, 1),
            "sites_identified": len(self.sites),
            "total_site_capacity": sum(s.capacity for s in self.sites),
            "district_headroom": self.headroom,
            "binding_constraint": constraint,
            "people_to_relocate": to_move,
            "people_placeable": placed,
            "unplaced": to_move - placed,
            "placement_rate": round(placed / max(to_move, 1), 3),
            "max_acceptable_move_km": MAX_ACCEPTABLE_MOVE_KM,
        }


def assess(grid: Grid, terrain: Terrain, exposure: Exposure, red: RedZones,
           habitations: Sequence[Habitation], urban_fraction: float,
           hq_lat: float, hq_lon: float, hq_name: str) -> RelocationAssessment:
    """Full relocation assessment: sites, capacity and allocation."""
    score, eligible, components = suitability_grid(grid, terrain, exposure, red)
    sites = find_sites(grid, terrain, exposure, red, score, eligible,
                       urban_fraction, hq_lat, hq_lon, hq_name)
    plans = allocate(habitations, sites)
    density = sustainable_density(urban_fraction)
    return RelocationAssessment(
        suitability=score, eligible=eligible, sites=sites, plans=plans,
        components=components, density_per_km2=density,
        headroom=district_headroom(grid, exposure, eligible, density))
