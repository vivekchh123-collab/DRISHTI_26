"""Bake distance-to-active-fault, a real proxy for the BIS LHEF *structure* factor.

    python scripts/fetch_faults.py
    python scripts/fetch_faults.py BR-DAR KL-WAY

**What this closes, and what it does not.** BIS IS 14496 (Part 2)'s "structure"
factor rates proximity to lineaments, faults and thrusts and their
intersections — the LHEF scheme's proxy for structural weakness in the rock
mass. Until now it was held at a neutral mid-rating for every district because
no lineament map was wired in (see ``core/landslide.py``). The GEM Global
Active Faults database is open, current and needs no account: 13,696 mapped
faults worldwide, ~471 with a vertex inside India. Distance to the nearest one
is a real, defensible, and **openly stated** proxy for the factor BIS asks for.

It is a proxy, not a substitute for a structural geology survey. GEM maps
faults with demonstrated Quaternary activity — it does not capture every joint,
fold axis or shear zone a GSI lithology sheet would show. That limitation is
carried into the API alongside the number, the same way every other estimated
layer in this system states its own ceiling.

One 10.6 MB GeoJSON, fetched once and cached locally rather than once per
district — GitHub has no interest in serving the same 10 MB file twenty-two
times in a row. Faults are rasterised onto each district's grid with
``rasterio.features.rasterize`` (no shapefile parsing, no ``fiona`` needed —
GeoJSON is plain dicts), then reduced to a distance field with
``scipy.ndimage.distance_transform_edt``. Both stay **bake-time-only**
dependencies; the ``.npz`` output loads with numpy alone, matching every other
baked layer in this system.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np  # noqa: E402

from app.core.grid import Grid  # noqa: E402
from app.data import districts as districts_data  # noqa: E402

SOURCE_URL = ("https://raw.githubusercontent.com/GEMScienceTools/"
             "gem-global-active-faults/master/geojson/"
             "gem_active_faults_harmonized.geojson")
CACHE = os.path.join(os.path.dirname(__file__), "_cache_gem_faults.geojson")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "app",
                       "data", "faults")

# Distance bands, km, mapped onto the BIS LHEF 0-2 structure scale. A
# monotonic decay from the nearest mapped fault trace: closer means a higher
# structure rating. Break points follow the buffer distances commonly used in
# regional seismic microzonation (near-fault / near-field / far-field), not a
# BIS-published table - GSI's own structure rating requires a lineament map
# this system does not have, and this proxy is documented as a proxy rather
# than dressed up as one.
BANDS = ((2.0, 2.0), (5.0, 1.5), (10.0, 1.0), (20.0, 0.5), (float("inf"), 0.0))


def fetch_raw() -> dict:
    """Download the fault catalogue once, or read the cached copy."""
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    print("  downloading GEM Global Active Faults (10.6 MB, once) ...")
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "drishti/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    with open(CACHE, "wb") as fh:
        fh.write(body)
    return json.loads(body)


def faults_near(raw: dict, grid: Grid, margin_deg: float = 1.0):
    """GeoJSON LineString geometries whose bbox overlaps the grid, padded.

    A fault just outside the frame can still be the nearest one to a cell near
    the frame's edge, so the filter pads the district's extent rather than
    clipping to it exactly.
    """
    lat0, lat1 = grid.lat0 - margin_deg, grid.lat1 + margin_deg
    lon0, lon1 = grid.lon0 - margin_deg, grid.lon1 + margin_deg
    out = []
    for f in raw.get("features", []):
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        if geom.get("type") != "LineString" or not coords:
            continue
        if any(lon0 <= lo <= lon1 and lat0 <= la <= lat1 for lo, la, *_ in coords):
            out.append(geom)
    return out


def structure_rating(distance_km: np.ndarray) -> np.ndarray:
    rating = np.zeros_like(distance_km, np.float32)
    lo = 0.0
    for hi, value in BANDS:
        band = (distance_km >= lo) & (distance_km < hi)
        rating[band] = value
        lo = hi
    return rating


# A fault a few km outside a district's grid is still the nearest fault to a
# cell right at the grid's edge. Rasterizing onto the grid's exact extent alone
# throws that fault's whole line away - only the portion inside the window
# would ever be drawn, and a fault whose nearest vertex is *just* outside draws
# nothing at all, understating the true distance for every cell near that edge.
# So rasterization happens on a padded raster and the distance transform is
# cropped back to the district afterwards, rather than the other way round.
PAD_DEG = 0.5


def bake_district(code: str, raw: dict, verbose: bool = True):
    import rasterio.features
    from rasterio.transform import from_bounds
    from scipy.ndimage import distance_transform_edt

    d = districts_data.get(code)
    grid = Grid.for_district(d.lat, d.lon, d.area_km2, terrain=d.terrain)
    n = grid.n

    geoms = faults_near(raw, grid, margin_deg=max(1.0, PAD_DEG * 2))
    if not geoms:
        distance_km = np.full((n, n), 999.0, np.float32)
    else:
        # Same cell size as the district grid, extended by PAD_DEG on every
        # side, so a fault just outside the district still pulls the distance
        # field down correctly for cells near the boundary.
        cell_deg = (grid.lat1 - grid.lat0) / n
        pad_cells = int(round(PAD_DEG / cell_deg))
        pn = n + 2 * pad_cells
        plat0, plat1 = grid.lat0 - pad_cells * cell_deg, grid.lat1 + pad_cells * cell_deg
        plon0, plon1 = grid.lon0 - pad_cells * cell_deg, grid.lon1 + pad_cells * cell_deg

        transform = from_bounds(plon0, plat0, plon1, plat1, pn, pn)
        fault_mask = rasterio.features.rasterize(
            [(g, 1) for g in geoms], out_shape=(pn, pn), transform=transform,
            fill=0, dtype=np.uint8).astype(bool)

        if fault_mask.any():
            # EDT gives distance in *pixels* to the nearest True cell; the grid
            # is not square in metres (a degree of longitude shrinks with
            # latitude), so pixels are converted with the grid's own average
            # cell size rather than assumed to be uniform.
            px = distance_transform_edt(~fault_mask)
            distance_km_padded = (px * grid.cell_m / 1000.0).astype(np.float32)
            distance_km = distance_km_padded[pad_cells:pad_cells + n,
                                             pad_cells:pad_cells + n]
        else:
            distance_km = np.full((n, n), 999.0, np.float32)

    rating = structure_rating(distance_km)
    if verbose:
        print("      %d fault trace(s) nearby, mean distance %.1f km, "
              "mean structure rating %.2f/2.0"
              % (len(geoms), float(distance_km.mean()), float(rating.mean())))

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.npz" % code)
    np.savez_compressed(
        path, distance_km=distance_km, structure_rating=rating,
        fault_count_nearby=len(geoms),
        source=("GEM Global Active Faults Database, distance-to-fault as a "
                "proxy for BIS LHEF structure factor - not a lineament survey"))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="*")
    args = ap.parse_args()

    try:
        import rasterio  # noqa: F401
        import scipy  # noqa: F401
    except ImportError as exc:
        print("bake-time dependency missing: %s" % exc)
        print("  pip install rasterio scipy")
        return 1

    codes = args.codes or [d.code for d in districts_data.DISTRICTS]
    print("GEM Global Active Faults -> distance-to-fault, district grids")
    raw = fetch_raw()
    print("%d fault traces loaded; %d district(s)\n"
          % (len(raw.get("features", [])), len(codes)))

    total, ok = 0, 0
    for code in codes:
        d = districts_data.get(code)
        print("  %-8s %s, %s" % (code, d.name, d.state))
        path = bake_district(code, raw)
        ok += 1
        total += os.path.getsize(path)

    print("\n%d/%d districts baked, %.1f MB in backend/app/data/faults/"
          % (ok, len(codes), total / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
