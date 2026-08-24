"""Terrain and hydrology.

The functions in the *analysis* half of this module are the real thing: Horn's
slope, Wang & Liu priority-flood depression filling, D8 flow routing, contributing
-area accumulation, stream extraction, HAND (Height Above Nearest Drainage) and
the Beven-Kirkby topographic wetness index. They are what actually decides which
cells flood and where water sits.

The *synthesis* half builds a plausible DEM for a district when no real elevation
raster has been supplied. Swap in a real SRTM/CartoDEM tile via
``load_dem_geotiff`` and every downstream product is computed from real elevation
with no other change. Which path was taken is reported as ``dem_source``.
"""

from __future__ import annotations

import heapq
import math
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .grid import Grid

# ---------------------------------------------------------------------------
# noise primitives (synthesis only)
# ---------------------------------------------------------------------------


def _lattice(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.random((n, n)).astype(np.float32)


def _upsample(a: np.ndarray, n: int) -> np.ndarray:
    """Resize a square array to (n, n) with edge clamping.

    Uses smoothstep rather than linear interpolation of the fractional part.
    Straight bilinear is only C0 continuous, so the lattice of the underlying
    value noise shows through as grid-aligned creases once several octaves are
    summed — a dead giveaway that the terrain is synthetic. Cubic Hermite
    (3t^2 - 2t^3) makes the first derivative continuous and the grid vanishes.
    """
    m = a.shape[0]
    if m == n:
        return a
    src = np.linspace(0, m - 1, n)
    i0 = np.floor(src).astype(int)
    i1 = np.minimum(i0 + 1, m - 1)
    t = (src - i0).astype(np.float32)
    t = t * t * (3.0 - 2.0 * t)                     # smoothstep
    rows = a[i0] * (1 - t)[:, None] + a[i1] * t[:, None]
    cols = rows[:, i0] * (1 - t)[None, :] + rows[:, i1] * t[None, :]
    return cols.astype(np.float32)


def micro(n: int, seed: int, cells: float = 2.5) -> np.ndarray:
    """Near-pixel-scale roughness in [-0.5, 0.5].

    Real floodplains are not planar: they carry ridge-and-swale micro-relief of a
    few tens of centimetres. Without it the depression-filled surface contains
    large exactly-flat regions, and D8 has no gradient to follow across them —
    it resolves the tie in heap-pop order, which draws long straight parallel
    "rivers" that look nothing like a drainage network. Adding micro-relief
    removes the flats at source, which is both more physical and better looking
    than special-casing flat routing afterwards.
    """
    rng = np.random.default_rng(seed)
    lat = max(int(n / max(cells, 1.0)), 2)
    return _upsample(_lattice(lat, rng), n) - 0.5


def fbm(n: int, seed: int, octaves: int = 6, persistence: float = 0.5,
        lacunarity: float = 2.0, base: int = 3) -> np.ndarray:
    """Fractal Brownian motion in [0, 1] - summed octaves of value noise."""
    rng = np.random.default_rng(seed)
    out = np.zeros((n, n), np.float32)
    amp, total, size = 1.0, 0.0, base
    for _ in range(octaves):
        out += amp * _upsample(_lattice(max(size, 2), rng), n)
        total += amp
        amp *= persistence
        size = int(size * lacunarity)
        if size > n:
            break
    out /= max(total, 1e-6)
    lo, hi = float(out.min()), float(out.max())
    return (out - lo) / max(hi - lo, 1e-6)


def ridged(n: int, seed: int, octaves: int = 6) -> np.ndarray:
    """Ridged-multifractal noise - produces the sharp crests of a mountain range."""
    f = fbm(n, seed, octaves=octaves)
    r = 1.0 - np.abs(f * 2.0 - 1.0)
    return (r ** 2).astype(np.float32)


def smooth(a: np.ndarray, radius: int = 1, passes: int = 1) -> np.ndarray:
    """Separable box blur - cheap Gaussian stand-in, no scipy dependency."""
    out = a.astype(np.float32)
    k = 2 * radius + 1
    for _ in range(passes):
        pad = np.pad(out, radius, mode="edge")
        acc = np.zeros_like(out)
        for d in range(k):
            acc += pad[d:d + out.shape[0], radius:radius + out.shape[1]]
        out = acc / k
        pad = np.pad(out, radius, mode="edge")
        acc = np.zeros_like(out)
        for d in range(k):
            acc += pad[radius:radius + out.shape[0], d:d + out.shape[1]]
        out = acc / k
    return out


def normalize(a: np.ndarray) -> np.ndarray:
    lo, hi = float(np.nanmin(a)), float(np.nanmax(a))
    return ((a - lo) / max(hi - lo, 1e-9)).astype(np.float32)


# ---------------------------------------------------------------------------
# DEM synthesis by terrain archetype
# ---------------------------------------------------------------------------

# Which way the open sea lies, in compass degrees, for the coastal districts.
COAST_BEARING = {
    "OD-PUR": 140.0, "OD-KEN": 100.0, "OD-GAN": 145.0, "OD-BAL": 125.0,
    "WB-S24": 175.0, "AP-NEL": 95.0,  "TN-CUD": 95.0,
}


def _meander(n: int, seed: int, amplitude: float, wavelength: float,
             offset: float = 0.5) -> np.ndarray:
    """Column position of a meandering channel for each row, in cell units."""
    rows = np.arange(n, dtype=np.float32)
    rng = np.random.default_rng(seed)
    phase = rng.random() * 6.283
    phase2 = rng.random() * 6.283
    x = (offset * n
         + amplitude * n * np.sin(2 * math.pi * rows / (wavelength * n) + phase)
         + 0.45 * amplitude * n * np.sin(2 * math.pi * rows / (0.37 * wavelength * n) + phase2))
    return x


def _channel_distance(n: int, centre_cols: np.ndarray) -> np.ndarray:
    """Horizontal distance in cells from each cell to the channel centreline."""
    cols = np.arange(n, dtype=np.float32)[None, :]
    return np.abs(cols - centre_cols[:, None])


def synth_dem(grid: Grid, terrain: str, code: str, seed: int) -> Tuple[np.ndarray, dict]:
    """Build an archetype-appropriate DEM. Returns (elevation_m, metadata)."""
    n = grid.n
    meta: dict = {"archetype": terrain}

    if terrain == "steep-mountain":
        relief = 2200.0
        base = 900.0
        e = ridged(n, seed, octaves=7) * relief
        e += fbm(n, seed + 11, octaves=5) * (relief * 0.28)
        # Regional tilt: Himalayan and Ghat districts drain broadly downslope.
        tilt = np.linspace(0, 1, n, dtype=np.float32)[:, None] * (relief * 0.35)
        e = e + tilt + base
        # Incise the main valley so the trunk river is unambiguous.
        cc = _meander(n, seed + 5, amplitude=0.10, wavelength=1.5, offset=0.5)
        d = _channel_distance(n, cc)
        e -= np.exp(-(d / 5.0) ** 2) * 420.0
        e = smooth(e, radius=1)
        meta["relief_m"] = relief

    elif terrain == "alluvial-floodplain":
        base = 55.0
        relief = 26.0
        e = fbm(n, seed, octaves=5, persistence=0.55) * relief
        # Very gentle regional gradient down-valley.
        e += np.linspace(relief * 0.55, 0.0, n, dtype=np.float32)[:, None]
        e += base
        # Trunk river: a wide, deeply-cut, strongly meandering channel.
        cc = _meander(n, seed + 3, amplitude=0.16, wavelength=1.15, offset=0.52)
        d = _channel_distance(n, cc)
        e -= np.exp(-(d / 4.5) ** 2) * 13.0            # channel
        e += np.exp(-((d - 7.0) / 3.0) ** 2) * 2.4     # natural levee crest
        # A tributary joining from the other side - the classic backwater trap.
        tc = _meander(n, seed + 7, amplitude=0.07, wavelength=0.7, offset=0.20)
        td = _channel_distance(n, tc)
        e -= np.exp(-(td / 2.6) ** 2) * 6.0
        # Abandoned channels / oxbows: shallow closed depressions that pond first.
        ox = fbm(n, seed + 21, octaves=3, persistence=0.6)
        e -= np.clip(ox - 0.72, 0, None) * 18.0
        e = smooth(e, radius=1)
        e += micro(n, seed + 91, 2.2) * 0.45      # ridge-and-swale micro-relief
        meta["relief_m"] = relief

    elif terrain == "coastal-deltaic":
        bearing = math.radians(COAST_BEARING.get(code, 95.0))
        ry, rx = np.mgrid[0:n, 0:n].astype(np.float32)
        ry = (ry / n - 0.5)
        rx = (rx / n - 0.5)
        # Signed distance inland, measured along the offshore bearing.
        inland = -(rx * math.sin(bearing) - ry * math.cos(bearing))
        coast_wiggle = (fbm(n, seed + 31, octaves=4) - 0.5) * 0.10
        inland = inland + coast_wiggle + 0.18
        e = inland * 46.0                       # ~0 m at the shore, ~30 m inland
        e += fbm(n, seed, octaves=5) * 6.0
        # Tidal creeks reaching inland from the shoreline.
        creeks = ridged(n, seed + 13, octaves=5)
        e -= np.clip(creeks - 0.55, 0, None) * 16.0 * np.clip(1.4 - inland * 2.2, 0, 1.4)
        # A distributary of the main river.
        cc = _meander(n, seed + 9, amplitude=0.13, wavelength=1.0, offset=0.42)
        d = _channel_distance(n, cc)
        e -= np.exp(-(d / 3.6) ** 2) * 7.0
        e = smooth(e, radius=1)
        e += micro(n, seed + 93, 2.2) * 0.30      # deltaic micro-relief
        meta["relief_m"] = 46.0
        meta["coast_bearing_deg"] = COAST_BEARING.get(code, 95.0)
        meta["sea_level_m"] = 0.0

    else:  # urban-lowland
        base = 8.0
        e = fbm(n, seed, octaves=6, persistence=0.48) * 34.0 + base
        # Engineered drainage: a trunk creek plus two storm drains.
        for i, (amp, wl, off, depth, width) in enumerate(
                [(0.10, 1.3, 0.48, 9.0, 3.0), (0.05, 0.8, 0.22, 5.0, 2.0),
                 (0.05, 0.9, 0.76, 5.0, 2.0)]):
            cc = _meander(n, seed + 40 + i, amplitude=amp, wavelength=wl, offset=off)
            d = _channel_distance(n, cc)
            e -= np.exp(-(d / width) ** 2) * depth
        # Reclaimed low-lying pockets: the chronic waterlogging spots.
        pockets = fbm(n, seed + 61, octaves=3, persistence=0.62)
        e -= np.clip(pockets - 0.66, 0, None) * 22.0
        e = smooth(e, radius=1)
        e += micro(n, seed + 95, 2.2) * 0.35      # kerb-and-plot micro-relief
        meta["relief_m"] = 34.0

    return e.astype(np.float32), meta


def load_dem_geotiff(path: str, grid: Grid) -> Optional[np.ndarray]:
    """Read a real DEM onto the analysis grid, if rasterio is installed.

    Returns None when rasterio is unavailable or the file does not cover the
    grid, in which case the caller falls back to synthesis.
    """
    try:
        import rasterio                       # noqa: F401  (optional dependency)
        from rasterio.warp import reproject, Resampling
        from rasterio.transform import from_bounds
    except Exception:
        return None
    try:
        with rasterio.open(path) as src:
            dst = np.zeros((grid.n, grid.n), np.float32)
            reproject(
                source=rasterio.band(src, 1),
                destination=dst,
                dst_transform=from_bounds(grid.lon0, grid.lat0, grid.lon1,
                                          grid.lat1, grid.n, grid.n),
                dst_crs="EPSG:4326",
                resampling=Resampling.bilinear,
            )
        if not np.isfinite(dst).any():
            return None
        return dst
    except Exception:
        return None



# ---------------------------------------------------------------------------
# real elevation
# ---------------------------------------------------------------------------

DEM_DIR = os.environ.get("DRISHTI_DEM") or os.path.join(
    os.path.dirname(__file__), "..", "data", "dem")


def load_baked_dem(code: str):
    """Real elevation for a district, if it has been baked.

    ``scripts/fetch_real_dem.py`` pulls Copernicus DEM GLO-30 (30 m, open, no
    account) and resamples it onto each district's analysis grid, storing about
    100 kB per district. Loading it needs numpy and nothing else, which is what
    lets the shipped application run on **real terrain with no GDAL, no rasterio
    and no network**.

    Returns ``(elevation, sea_mask)`` or None.
    """
    path = os.path.join(DEM_DIR, "%s.npz" % code)
    if not os.path.exists(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as z:
            elevation = z["elevation"].astype(np.float32)
            sea = z["sea"].astype(bool) if "sea" in z else None
            p90 = z["slope_p90"].astype(np.float32) if "slope_p90" in z else None
            smax = z["slope_max"].astype(np.float32) if "slope_max" in z else None
    except Exception:
        return None
    if sea is None:
        sea = np.zeros(elevation.shape, bool)
    return elevation, sea, p90, smax


# ---------------------------------------------------------------------------
# terrain analysis - real algorithms, independent of how the DEM was obtained
# ---------------------------------------------------------------------------

# D8 neighbour offsets and the distance multiplier for each (diagonals are sqrt2).
_D8 = [(-1, 0, 1.0), (-1, 1, math.sqrt(2)), (0, 1, 1.0), (1, 1, math.sqrt(2)),
       (1, 0, 1.0), (1, -1, math.sqrt(2)), (0, -1, 1.0), (-1, -1, math.sqrt(2))]


def slope_aspect(dem: np.ndarray, cell_m: float) -> Tuple[np.ndarray, np.ndarray]:
    """Horn (1981) 3x3 slope and aspect.

    Returns (slope_degrees, aspect_degrees). Aspect is 0 = north, clockwise.
    """
    p = np.pad(dem, 1, mode="edge")
    z1, z2, z3 = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    z4,     z6 = p[1:-1, :-2], p[1:-1, 2:]
    z7, z8, z9 = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8.0 * cell_m)
    dzdy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8.0 * cell_m)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = (np.degrees(np.arctan2(dzdy, -dzdx)) + 360.0) % 360.0
    return slope.astype(np.float32), aspect.astype(np.float32)


def curvature(dem: np.ndarray, cell_m: float) -> np.ndarray:
    """Laplacian curvature. Negative = concave (convergent, water collects)."""
    p = np.pad(dem, 1, mode="edge")
    lap = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]
           - 4.0 * p[1:-1, 1:-1]) / (cell_m ** 2)
    return (lap * 1e4).astype(np.float32)


def fill_depressions(dem: np.ndarray, epsilon: float = 0.0) -> np.ndarray:
    """Wang & Liu (2006) priority-flood depression filling.

    Guarantees every cell has a non-ascending path to the grid edge, which is
    what makes D8 routing terminate.

    ``epsilon`` defaults to 0, so depressions fill to genuine flats rather than
    to a ramp in heap-pop order. That ramp is cheap but it radiates from the
    spill point and produces visibly straight, parallel drainage. Flats are
    instead given a real gradient afterwards by :func:`resolve_flats`.
    """
    n = dem.shape[0]
    filled = dem.astype(np.float64).copy()
    closed = np.zeros((n, n), bool)
    heap = []

    for i in range(n):
        for (r, c) in ((0, i), (n - 1, i), (i, 0), (i, n - 1)):
            if not closed[r, c]:
                closed[r, c] = True
                heapq.heappush(heap, (filled[r, c], r, c))

    while heap:
        z, r, c = heapq.heappop(heap)
        for dr, dc, _ in _D8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < n and 0 <= cc < n and not closed[rr, cc]:
                closed[rr, cc] = True
                if filled[rr, cc] <= z:
                    filled[rr, cc] = z + epsilon
                heapq.heappush(heap, (filled[rr, cc], rr, cc))

    # float64 throughout: the gradient resolve_flats imposes across a flat is
    # sub-millimetric against elevations of tens of metres, and float32 cannot
    # represent it — cells that should differ round back to equal and the flat
    # survives.
    return filled


def connected_to_edge(mask: np.ndarray) -> np.ndarray:
    """The part of ``mask`` reachable from the grid boundary, four-connected.

    Used to decide what is genuinely sea. Taking every cell below zero as ocean
    is wrong: an enclosed pocket below sea level is a depression, not sea. On
    the Odisha coast that mistake put isolated below-zero hollows into the
    stream network, gave them open-water runoff and left them draining into
    land, which broke the network's connectivity invariant. Kuttanad and the
    Netherlands are the real-world versions of the same distinction.
    """
    if not mask.any():
        return np.zeros(mask.shape, bool)
    reach = np.zeros(mask.shape, bool)
    reach[0, :] |= mask[0, :]
    reach[-1, :] |= mask[-1, :]
    reach[:, 0] |= mask[:, 0]
    reach[:, -1] |= mask[:, -1]
    for _ in range(mask.shape[0] * 2):
        grown = reach.copy()
        grown[:-1, :] |= reach[1:, :]
        grown[1:, :] |= reach[:-1, :]
        grown[:, :-1] |= reach[:, 1:]
        grown[:, 1:] |= reach[:, :-1]
        grown &= mask
        if grown.sum() == reach.sum():
            break
        reach = grown
    return reach


def drainage_pits(filled: np.ndarray) -> np.ndarray:
    """Interior cells with no strictly-lower neighbour — i.e. that cannot drain.

    The post-condition of flat resolution. Any cell left here is a place where
    D8 dead-ends and the stream network breaks apart.
    """
    n = filled.shape[0]
    has_lower = np.zeros((n, n), bool)
    for dr, dc, _ in _D8:
        sh = np.full((n, n), np.inf)
        r0, r1 = max(0, -dr), n - max(0, dr)
        c0, c1 = max(0, -dc), n - max(0, dc)
        sh[r0:r1, c0:c1] = filled[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        has_lower |= sh < filled - 1e-12
    pits = ~has_lower
    pits[0, :] = pits[-1, :] = pits[:, 0] = pits[:, -1] = False
    return pits


def resolve_flats_iter(filled: np.ndarray, max_step: float = 0.05,
                       passes: int = 3) -> np.ndarray:
    """Apply flat resolution, keeping the surface that drains best.

    One pass clears almost everything; splitting a flat can occasionally expose
    a smaller one nested inside it, which a second pass catches. But a further
    pass can also make things worse, because each pass shrinks the level steps
    it has to work within. So this keeps the best surface seen rather than the
    last one, and stops as soon as a pass fails to improve.
    """
    best = filled
    best_pits = int(drainage_pits(best).sum())
    cur = filled
    for _ in range(passes):
        if best_pits == 0:
            break
        cur = resolve_flats(cur, max_step)
        pits = int(drainage_pits(cur).sum())
        if pits < best_pits:
            best, best_pits = cur, pits
        else:
            break
    return best


def resolve_flats(filled: np.ndarray, max_step: float = 0.05) -> np.ndarray:
    """Garbrecht & Martz (1997) flat resolution.

    Depression filling leaves large regions at exactly one elevation. D8 has no
    gradient to follow across them, so it breaks the tie in whatever order the
    cells were visited — which draws long straight parallel channels converging
    on the spill point. On a Bihar or Assam floodplain, where filled flats can
    reach a tenth of the district, that artefact is the most visible thing on the
    map and it is obviously not a river network.

    The fix imposes a real drainage gradient over each flat by combining two
    distance fields:

    * distance from the flat's outlets — elevation rises with it, so water runs
      toward the outlet;
    * distance from the flat's higher edge — elevation falls with it, so water
      runs away from the surrounding high ground.

    Their sum is monotonic toward the outlet, which guarantees every flat cell
    drains, and the resulting channels curve the way real ones do.

    The imposed relief is capped at ``max_step`` metres and additionally at half
    the true drop below the flat's lowest outlet, so resolution can never invert
    the real terrain.
    """
    from collections import deque

    n = filled.shape[0]
    out = filled.astype(np.float64).copy()
    # Exact equality, deliberately. Priority-flood assigns every cell in a
    # depression the same spill value, so a true flat is bit-identical. Any
    # rounding tolerance here must be finer than the gradient this function
    # imposes (~5e-5 m) or a second pass re-reads its own output as still flat
    # and corrupts it — which is exactly what a 1e-4 tolerance did.
    F = out

    # A cell needs help if no neighbour is strictly lower.
    has_lower = np.zeros((n, n), bool)
    for dr, dc, _ in _D8:
        sh = np.full((n, n), np.inf)
        r0, r1 = max(0, -dr), n - max(0, dr)
        c0, c1 = max(0, -dc), n - max(0, dc)
        sh[r0:r1, c0:c1] = F[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        has_lower |= sh < F - 1e-9
    need = ~has_lower
    need[0, :] = need[-1, :] = need[:, 0] = need[:, -1] = False
    if not need.any():
        return out

    def _bfs(sources, level, member):
        dist = {}
        q = deque()
        for s in sources:
            dist[s] = 0
            q.append(s)
        while q:
            a, b = q.popleft()
            for dr, dc, _ in _D8:
                x, y = a + dr, b + dc
                if 0 <= x < n and 0 <= y < n and (x, y) in member and (x, y) not in dist:
                    dist[(x, y)] = dist[(a, b)] + 1
                    q.append((x, y))
        return dist

    seen = np.zeros((n, n), bool)
    for si in range(n):
        for sj in range(n):
            if not need[si, sj] or seen[si, sj]:
                continue
            level = F[si, sj]

            member = set()
            q = deque([(si, sj)])
            seen[si, sj] = True
            while q:
                a, b = q.popleft()
                member.add((a, b))
                for dr, dc, _ in _D8:
                    x, y = a + dr, b + dc
                    if (0 <= x < n and 0 <= y < n and not seen[x, y]
                            and F[x, y] == level):
                        seen[x, y] = True
                        q.append((x, y))

            outlets, highs = [], []
            drop, rise = np.inf, np.inf
            for (a, b) in member:
                lo = hi = False
                for dr, dc, _ in _D8:
                    x, y = a + dr, b + dc
                    if not (0 <= x < n and 0 <= y < n):
                        lo = True                      # the grid edge drains out
                        continue
                    if F[x, y] < level:
                        lo = True
                        drop = min(drop, level - F[x, y])
                    elif F[x, y] > level:
                        hi = True
                        # Smallest step up out of the flat. Raising a flat cell
                        # past a neighbour that drains into it would strand that
                        # neighbour with nowhere to go — turning one resolved
                        # flat into a fresh pit just outside it.
                        rise = min(rise, F[x, y] - level)
                if lo:
                    outlets.append((a, b))
                if hi:
                    highs.append((a, b))
            if not outlets:
                continue                                # fully enclosed; fill handled it

            d_low = _bfs(outlets, level, member)
            d_high = _bfs(highs, level, member) if highs else {}
            max_high = max(d_high.values()) if d_high else 0

            # The outlet gradient carries twice the weight of the high-edge
            # gradient, and that factor is load-bearing rather than cosmetic.
            # Stepping one cell toward the outlet always drops d_low by exactly
            # 1, but d_high may drop by 1 at the same time and push the second
            # term up by 1. Weighted equally the two cancel, the cell keeps no
            # strictly-lower neighbour, and D8 dead-ends mid-flat — which is
            # what fragmented the network into 59 pieces. At weight 2 the sum
            # falls by at least 1 per step, so every flat cell provably drains.
            inc = {}
            for c in member:
                inc[c] = 2 * d_low.get(c, 0) + (max_high - d_high.get(c, max_high))
            span = max(inc.values()) or 1
            headroom = min(drop, rise)
            cap = min(max_step, 0.5 * headroom) if np.isfinite(headroom) else max_step
            for c, v in inc.items():
                out[c] += cap * (v / span)

    return out


@dataclass
class FlowNetwork:
    """Everything derived from routing water downhill across the DEM."""
    filled: np.ndarray        # depression-filled elevation (m)
    receiver: np.ndarray      # flat index of the downstream cell, -1 at outlets
    order: np.ndarray         # flat indices sorted by ascending filled elevation
    accum: np.ndarray         # contributing cell count (n, n)
    slope: np.ndarray         # degrees
    aspect: np.ndarray        # degrees


def flow_network(dem: np.ndarray, cell_m: float) -> FlowNetwork:
    """D8 flow directions and contributing-area accumulation."""
    n = dem.shape[0]
    filled = resolve_flats_iter(fill_depressions(dem))
    slope, aspect = slope_aspect(dem, cell_m)

    # Steepest-descent receiver for every cell, vectorised over the 8 directions.
    best_drop = np.zeros((n, n), np.float64)
    receiver = np.full((n, n), -1, np.int64)
    flat = np.arange(n * n).reshape(n, n)

    for dr, dc, dist in _D8:
        shifted = np.full((n, n), np.inf, np.float64)
        idx = np.full((n, n), -1, np.int64)
        r0, r1 = max(0, -dr), n - max(0, dr)
        c0, c1 = max(0, -dc), n - max(0, dc)
        shifted[r0:r1, c0:c1] = filled[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        idx[r0:r1, c0:c1] = flat[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        drop = (filled - shifted) / (dist * cell_m)
        better = (drop > best_drop) & np.isfinite(shifted)
        best_drop = np.where(better, drop, best_drop)
        receiver = np.where(better, idx, receiver)

    # Accumulate from the top of the catchment down.
    order = np.argsort(filled.ravel())[::-1]
    accum = np.ones(n * n, np.float64)
    recv = receiver.ravel()
    for i in order:
        j = recv[i]
        if j >= 0:
            accum[j] += accum[i]

    return FlowNetwork(filled=filled.astype(np.float64), receiver=recv,
                       order=order[::-1].copy(),
                       accum=accum.reshape(n, n).astype(np.float32),
                       slope=slope, aspect=aspect)


def extract_streams(net: FlowNetwork, threshold_cells: int) -> np.ndarray:
    """Boolean stream mask from a contributing-area threshold."""
    return net.accum >= float(threshold_cells)


def hand(dem: np.ndarray, net: FlowNetwork,
         streams: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Height Above Nearest Drainage (Renno et al., 2008; Nobre et al., 2011).

    For each cell, the vertical distance to the stream cell it drains into. HAND
    is the single strongest terrain predictor of riverine inundation: a cell with
    HAND = 2 m floods when the river rises 2 m, wherever it sits on the map.

    Computed by walking the ascending-elevation order once, so each cell inherits
    its receiver's already-resolved outlet in O(n).

    Returns ``(hand, nearest)``. ``nearest`` is the flat index of the drainage
    cell each cell drains into, and the flood model needs it as much as HAND
    itself: inundation depth is the river stage *at that cell's own outlet*
    minus its HAND, which is what lets one district hold several rivers at
    different stages at the same time.
    """
    n = dem.shape[0]
    nearest = np.full(n * n, -1, np.int64)
    st = streams.ravel()
    recv = net.receiver

    for i in net.order:               # ascending elevation: receivers come first
        if st[i]:
            nearest[i] = i
        else:
            j = recv[i]
            nearest[i] = nearest[j] if j >= 0 else -1

    flat_dem = net.filled.ravel()
    ref = np.where(nearest >= 0, flat_dem[np.maximum(nearest, 0)], flat_dem)
    h = np.maximum(flat_dem - ref, 0.0)
    return (h.reshape(n, n).astype(np.float32), nearest.reshape(n, n))


def twi(net: FlowNetwork, cell_m: float) -> np.ndarray:
    """Beven-Kirkby topographic wetness index, ln(a / tan(beta)).

    High TWI = a large upslope area draining into a flat cell, i.e. the terrain
    signature of a waterlogging hotspot.
    """
    a = net.accum * cell_m                       # specific catchment area
    tanb = np.maximum(np.tan(np.radians(net.slope)), 0.0015)
    return np.log(np.maximum(a, cell_m) / tanb).astype(np.float32)


def drainage_density(streams: np.ndarray, cell_m: float, radius: int = 6) -> np.ndarray:
    """Stream length per unit area in a moving window (km/km^2)."""
    s = streams.astype(np.float32)
    win = smooth(s, radius=radius)
    cells = (2 * radius + 1) ** 2
    length_km = win * cells * (cell_m / 1000.0)
    area_km2 = cells * (cell_m / 1000.0) ** 2
    return (length_km / area_km2).astype(np.float32)


@dataclass
class Terrain:
    """The full terrain product set for one district."""
    grid: Grid
    dem: np.ndarray
    net: FlowNetwork
    streams: np.ndarray
    hand: np.ndarray
    nearest: np.ndarray      # flat index of each cell's outlet drainage cell
    twi: np.ndarray
    curvature: np.ndarray
    drainage_density: np.ndarray
    sea_mask: np.ndarray
    dem_source: str
    meta: dict

    # Slope measured at the DEM's native 30 m and aggregated per analysis cell.
    # The grid-scale slope in `net` answers "how tilted is this cell"; these
    # answer "is any part of it steep", which is the question a landslide asks.
    # Present only when a baked DEM supplied them.
    slope_p90: Optional[np.ndarray] = None
    slope_max: Optional[np.ndarray] = None

    @property
    def hillslope(self) -> np.ndarray:
        """The slope the landslide model should use.

        Native-resolution 90th percentile where it exists, grid-scale slope
        otherwise, so the model degrades to the coarser answer rather than
        failing when a district has not been baked.
        """
        return self.slope_p90 if self.slope_p90 is not None else self.net.slope

    @property
    def slope(self) -> np.ndarray:
        return self.net.slope

    @property
    def aspect(self) -> np.ndarray:
        return self.net.aspect

    @property
    def accum(self) -> np.ndarray:
        return self.net.accum


def build_terrain(grid: Grid, terrain_type: str, code: str, seed: int,
                  dem_path: Optional[str] = None) -> Terrain:
    """Assemble every terrain-derived layer for a district."""
    dem_source = "synthetic-dem"
    dem = None
    baked_sea = None
    sub_p90 = sub_max = None

    if dem_path:
        dem = load_dem_geotiff(dem_path, grid)
        if dem is not None:
            dem_source = "geotiff:" + dem_path

    # Preferred when it has been baked: real 30 m elevation, loaded from a small
    # numpy archive so the shipped application needs neither GDAL nor a network.
    if dem is None:
        baked = load_baked_dem(code)
        if baked is not None:
            dem, baked_sea, sub_p90, sub_max = baked
            dem_source = "Copernicus DEM GLO-30 (ESA, 30 m)"

    if dem is None:
        dem, meta = synth_dem(grid, terrain_type, code, seed)
    else:
        meta = {"archetype": terrain_type}

    if baked_sea is not None and baked_sea.any():
        sea = baked_sea
    elif dem_source != "synthetic-dem":
        # Real DEMs record the sea surface at 0 m rather than leaving it void,
        # so open water is low ground that reaches the edge of the frame.
        sea = connected_to_edge(dem <= 0.5)
    else:
        sea = (connected_to_edge(dem < 0.0)
               if terrain_type == "coastal-deltaic" else np.zeros(dem.shape, bool))
    # Route water over the sea surface too, but keep it out of land statistics.
    work = np.where(sea, -0.5, dem).astype(np.float32)

    net = flow_network(work, grid.cell_m)
    # Stream threshold scales with cell size so the extracted network has a
    # comparable density regardless of district size.
    thresh = max(12, int(grid.n * grid.n * 0.0035))
    streams = extract_streams(net, thresh) | sea
    h, nearest = hand(work, net, streams)
    w = twi(net, grid.cell_m)
    curv = curvature(dem, grid.cell_m)
    dd = drainage_density(streams & ~sea, grid.cell_m)

    meta.update({
        "cell_m": round(grid.cell_m, 1),
        "stream_threshold_cells": thresh,
        "elevation_min_m": round(float(dem.min()), 1),
        "elevation_max_m": round(float(dem.max()), 1),
        "mean_slope_deg": round(float(net.slope.mean()), 2),
        "sea_fraction": round(float(sea.mean()), 4),
    })

    return Terrain(grid=grid, dem=dem, net=net, streams=streams, hand=h,
                   slope_p90=sub_p90, slope_max=sub_max,
                   nearest=nearest, twi=w,
                   curvature=curv, drainage_density=dd, sea_mask=sea,
                   dem_source=dem_source, meta=meta)
