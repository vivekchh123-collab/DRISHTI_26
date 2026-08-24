"""Exposure: who and what is actually in the way of the water.

Flood extent on its own is a statistic. This module is what turns it into a
priority — population, built-up land, cropland, the road network and the
critical facilities that a District Magistrate has to keep reachable.

Population is allocated **dasymetrically**: the district's total is distributed
across the grid in proportion to modelled built-up intensity rather than spread
evenly, because an even spread puts a third of a district's people in the river
and makes every ranking meaningless. The settlement pattern itself is derived
from the terrain — people build on ground that is flat, well drained and near
water but not in it, which is a real and very stable regularity.

**What is measured and what is inferred.** The district totals are real -
Census 2011 population, urban share and cropland share, carried forward at
1.6%/yr. Their *arrangement within the district* is not observed: built-up,
cropland, roads and facilities are all generated from the terrain here. Roads
are least-cost paths between settlements, not surveyed alignments; facilities
are placed at Indian Public Health Standards provision rates, not from a
register.

That is a defensible way to produce a plausible exposure surface, and it is not
the same thing as observing one. No consumer of this module should describe
these layers as satellite-derived. The upgrade path is WorldPop for population
and OpenStreetMap for roads and facilities; **neither is wired today**, and only
:func:`build_exposure` would change if they were.

Note that GHSL built-up *is* baked and genuinely satellite-derived, but it is
used by :mod:`app.core.encroachment` for change detection rather than to place
population here.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .boundary import Boundary
from .grid import Grid, describe_location, haversine_km
from .terrain import Terrain, normalize, smooth

# Facility provision rates, per 100,000 population, from the Indian Public Health
# Standards and district planning norms. Used to place a plausible number of
# facilities rather than an arbitrary one.
CHC_PER_100K = 2.0          # community health centres
SCHOOLS_PER_100K = 42.0     # government schools; the default relief-shelter stock


@dataclass
class Settlement:
    """A populated place on the grid."""
    id: str
    row: int
    col: int
    lat: float
    lon: float
    population: int
    is_hq: bool
    label: str                # "12 km NE of Darbhanga" — measured, never invented


@dataclass
class Facility:
    id: str
    kind: str                 # "hospital" | "shelter"
    row: int
    col: int
    lat: float
    lon: float
    name: str
    capacity: int             # people, for shelters; beds, for hospitals


@dataclass
class Exposure:
    built_up: np.ndarray            # 0..1 built-up fraction per cell
    population: np.ndarray          # people per cell
    cropland: np.ndarray            # 0..1 cropped fraction per cell
    road_mask: np.ndarray           # bool, cells carrying a road
    settlements: List[Settlement]
    facilities: List[Facility]
    district_mask: np.ndarray
    total_population: int

    def shelters(self) -> List[Facility]:
        return [f for f in self.facilities if f.kind == "shelter"]

    def hospitals(self) -> List[Facility]:
        return [f for f in self.facilities if f.kind == "hospital"]


# ---------------------------------------------------------------------------
# land cover
# ---------------------------------------------------------------------------

def settlement_suitability(terrain: Terrain, mask: np.ndarray,
                           hq_rc: Optional[Tuple[int, int]] = None) -> np.ndarray:
    """Where people plausibly build, 0..1.

    Three competing pulls, which together reproduce the observed pattern of
    Indian rural settlement: flat ground is preferred, ground safely above the
    river is strongly preferred, and proximity to water is preferred — but the
    channel itself is excluded. The result is the characteristic ribbon of
    villages set back from the bank on the higher terrace.
    """
    flat = 1.0 - normalize(np.clip(terrain.slope, 0, 15))
    hand_c = np.clip(terrain.hand, 0, float(np.percentile(terrain.hand, 90)) + 1e-6)
    dry = normalize(hand_c)
    # Distance to drainage, approximated by blurring the stream mask.
    near_water = normalize(smooth(terrain.streams.astype(np.float32), radius=5))

    s = 0.42 * flat + 0.38 * dry + 0.20 * near_water

    if hq_rc is not None:
        # The district headquarters is, by definition, the largest town — the
        # terrain does not know that, and without saying so the model can put
        # the HQ on near-empty ground and report a district capital of a few
        # thousand people. A decaying bump reproduces the real pattern of
        # density falling away from the principal town.
        n = terrain.grid.n
        rr, cc = np.mgrid[0:n, 0:n]
        d = np.hypot(rr - hq_rc[0], cc - hq_rc[1]) / max(n * 0.085, 1.0)
        s = s + 1.15 * np.exp(-0.5 * d ** 2)

    s = np.where(terrain.streams | terrain.sea_mask, 0.0, s)
    s = np.where(mask, s, 0.0)
    return normalize(smooth(s, radius=1)).astype(np.float32)


def build_up_grid(suitability: np.ndarray, urban_fraction: float,
                  mask: np.ndarray) -> np.ndarray:
    """Built-up fraction per cell.

    Urban districts concentrate; rural districts disperse. The exponent controls
    that: a high exponent makes a few cells dense and leaves the rest near empty,
    which is what a metropolitan district actually looks like.
    """
    conc = 1.6 + 3.2 * float(np.clip(urban_fraction, 0, 1))
    b = np.power(np.clip(suitability, 0, 1), conc)
    b = np.where(mask, b, 0.0)
    peak = float(b.max())
    return (b / peak if peak > 0 else b).astype(np.float32)


def cropland_grid(terrain: Terrain, built: np.ndarray, cropland_fraction: float,
                  mask: np.ndarray) -> np.ndarray:
    """Cropped fraction per cell.

    Agriculture takes the fertile, gently sloping, well-watered land that
    settlement does not — which on an alluvial plain is precisely the low ground
    that floods. That overlap is the reason crop loss dominates flood damage in
    Bihar and Assam.
    """
    if cropland_fraction <= 0:
        return np.zeros(built.shape, np.float32)
    gentle = 1.0 - normalize(np.clip(terrain.slope, 0, 8))
    wet = normalize(np.clip(terrain.twi, np.percentile(terrain.twi, 5),
                            np.percentile(terrain.twi, 95)))
    score = np.where(mask & ~terrain.streams & ~terrain.sea_mask,
                     0.55 * gentle + 0.45 * wet, 0.0)
    score = score * (1.0 - np.clip(built * 1.4, 0, 1))
    inside = score[mask]
    if inside.size == 0:
        return np.zeros(built.shape, np.float32)
    cut = float(np.quantile(inside, max(1.0 - cropland_fraction, 0.0)))
    out = np.clip((score - cut) / max(score.max() - cut, 1e-6), 0, 1)
    return (out * mask).astype(np.float32)


def allocate_population(built: np.ndarray, mask: np.ndarray,
                        total: int) -> np.ndarray:
    """Dasymetric population allocation, preserving the district total exactly."""
    w = np.where(mask, built, 0.0).astype(np.float64)
    s = w.sum()
    if s <= 0:
        w = mask.astype(np.float64)
        s = max(w.sum(), 1.0)
    return (w / s * float(total)).astype(np.float32)


# ---------------------------------------------------------------------------
# settlements, roads and facilities
# ---------------------------------------------------------------------------

def pick_settlements(grid: Grid, population: np.ndarray, built: np.ndarray,
                     hq_lat: float, hq_lon: float, hq_name: str,
                     count: int,
                     min_sep_cells: Optional[int] = None) -> List[Settlement]:
    """Choose settlement centres at local maxima of built-up intensity.

    Separation defaults to a tenth of the grid, which is deliberately large. The
    headquarters sits under a broad density bump, so a small separation returns
    its own suburbs — "4 km NE", "4 km ENE", "5 km NE" of the same town — instead
    of a spread of distinct places across the district, and a worst-affected
    ranking over one town's wards is not what a District Magistrate needs.
    """
    if min_sep_cells is None:
        min_sep_cells = max(10, grid.n // 10)
    order = np.argsort(built.ravel())[::-1]
    chosen: List[Tuple[int, int]] = []
    for flat in order:
        r, c = divmod(int(flat), grid.n)
        if built[r, c] <= 0:
            break
        if all(math.hypot(r - rr, c - cc) >= min_sep_cells for rr, cc in chosen):
            chosen.append((r, c))
        if len(chosen) >= count:
            break

    # The headquarters always leads the list. Drop anything the density bump
    # produced within one separation of it — those are its own wards, and
    # listing them beside it gives two entries for the same town.
    hq_rc = grid.rowcol(hq_lat, hq_lon)
    chosen = [rc for rc in chosen
              if math.hypot(rc[0] - hq_rc[0], rc[1] - hq_rc[1]) >= min_sep_cells]
    chosen.insert(0, hq_rc)

    out: List[Settlement] = []
    for i, (r, c) in enumerate(chosen):
        lat, lon = grid.latlon(r, c)
        # A settlement's population is what falls in its neighbourhood, so the
        # figures add up to the district total rather than being invented.
        r0, r1 = max(0, r - 3), min(grid.n, r + 4)
        c0, c1 = max(0, c - 3), min(grid.n, c + 4)
        pop = int(population[r0:r1, c0:c1].sum())
        out.append(Settlement(
            id="S%02d" % i, row=r, col=c, lat=lat, lon=lon,
            population=pop, is_hq=(i == 0),
            label=(hq_name if i == 0
                   else describe_location(grid, r, c, hq_lat, hq_lon, hq_name)),
        ))
    return out


def travel_cost(terrain: Terrain, mask: np.ndarray) -> np.ndarray:
    """Cost of building or driving a road through each cell.

    Steep ground and river crossings are expensive; flat dry ground is cheap.
    Roads generated by least-cost path over this surface follow valleys, hug
    contours and cross rivers at a small number of points — which is what a real
    road network does, and what makes cut-off analysis meaningful.
    """
    cost = 1.0 + 0.35 * np.clip(terrain.slope, 0, 40)
    cost += np.where(terrain.streams, 26.0, 0.0)        # bridges are costly
    cost += np.where(terrain.sea_mask, 1e5, 0.0)
    cost += np.where(mask, 0.0, 12.0)                   # prefer staying in-district
    return cost.astype(np.float64)


def least_cost_path(cost: np.ndarray, start: Tuple[int, int],
                    goal: Tuple[int, int]) -> List[Tuple[int, int]]:
    """Dijkstra over the 8-connected cost surface."""
    n = cost.shape[0]
    src, dst = start[0] * n + start[1], goal[0] * n + goal[1]
    dist = np.full(n * n, np.inf)
    prev = np.full(n * n, -1, np.int64)
    dist[src] = 0.0
    heap = [(0.0, src)]
    flat = cost.ravel()
    nbrs = ((-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
            (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414), (1, 1, 1.414))
    while heap:
        d, u = heapq.heappop(heap)
        if u == dst:
            break
        if d > dist[u]:
            continue
        ur, uc = divmod(u, n)
        for dr, dc, w in nbrs:
            vr, vc = ur + dr, uc + dc
            if not (0 <= vr < n and 0 <= vc < n):
                continue
            v = vr * n + vc
            nd = d + w * 0.5 * (flat[u] + flat[v])
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    if not np.isfinite(dist[dst]):
        return []
    path, cur = [], dst
    while cur != -1:
        path.append(divmod(int(cur), n))
        cur = prev[cur]
    return path[::-1]


def build_roads(terrain: Terrain, mask: np.ndarray,
                settlements: Sequence[Settlement]) -> np.ndarray:
    """Connect every settlement to the headquarters by least-cost path."""
    if not settlements:
        return np.zeros(terrain.dem.shape, bool)
    cost = travel_cost(terrain, mask)
    hq = (settlements[0].row, settlements[0].col)
    road = np.zeros(terrain.dem.shape, bool)
    for s in settlements[1:]:
        for (r, c) in least_cost_path(cost, hq, (s.row, s.col)):
            road[r, c] = True
    for s in settlements:
        road[s.row, s.col] = True
    return road


def place_facilities(grid: Grid, settlements: Sequence[Settlement],
                     total_population: int, hq_name: str) -> List[Facility]:
    """Distribute hospitals and shelters over settlements by population share.

    Counts follow Indian Public Health Standards provision rates rather than
    being chosen for effect, so the shelter capacity a District Magistrate sees
    is the capacity such a district would plausibly have.
    """
    if not settlements:
        return []
    pop_total = max(sum(s.population for s in settlements), 1)
    n_hosp = max(1, int(round(total_population / 100_000 * CHC_PER_100K)))
    n_shelter = max(2, int(round(total_population / 100_000 * SCHOOLS_PER_100K / 6)))

    out: List[Facility] = []
    for kind, count in (("hospital", n_hosp), ("shelter", n_shelter)):
        placed = 0
        for i, s in enumerate(settlements):
            share = s.population / pop_total
            k = int(round(count * share))
            if i == 0:
                k = max(k, 1)                       # the HQ always has both
            for j in range(k):
                if placed >= count:
                    break
                # Offset repeats so several facilities in one town do not stack
                # on a single cell.
                r = int(np.clip(s.row + (j % 3) - 1, 0, grid.n - 1))
                c = int(np.clip(s.col + (j // 3) - 1, 0, grid.n - 1))
                lat, lon = grid.latlon(r, c)
                if kind == "hospital":
                    name = "CHC %s" % (s.label if s.is_hq else s.id)
                    cap = 30 if not s.is_hq else 120
                else:
                    name = "Relief centre %s-%d" % (s.id, j + 1)
                    cap = 400 if s.is_hq else 250
                out.append(Facility(
                    id="%s%03d" % (kind[0].upper(), len(out)),
                    kind=kind, row=r, col=c, lat=lat, lon=lon,
                    name=name, capacity=cap))
                placed += 1
    return out


def build_exposure(grid: Grid, terrain: Terrain, bnd: Boundary, *,
                   population_total: int, urban_fraction: float,
                   cropland_fraction: float, hq_lat: float, hq_lon: float,
                   hq_name: str, settlement_count: int = 14) -> Exposure:
    """Assemble every exposure layer for a district."""
    mask = bnd.mask
    suit = settlement_suitability(terrain, mask, grid.rowcol(hq_lat, hq_lon))
    built = build_up_grid(suit, urban_fraction, mask)
    pop = allocate_population(built, mask, population_total)
    crop = cropland_grid(terrain, built, cropland_fraction, mask)

    settlements = pick_settlements(grid, pop, built, hq_lat, hq_lon, hq_name,
                                   settlement_count)
    roads = build_roads(terrain, mask, settlements)
    facilities = place_facilities(grid, settlements, population_total, hq_name)

    return Exposure(built_up=built, population=pop, cropland=crop,
                    road_mask=roads, settlements=settlements,
                    facilities=facilities, district_mask=mask,
                    total_population=int(population_total))
