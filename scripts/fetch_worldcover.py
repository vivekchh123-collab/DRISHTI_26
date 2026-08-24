"""Bake cropland and tree-cover fraction from ESA WorldCover, 10 m.

    python scripts/fetch_worldcover.py
    python scripts/fetch_worldcover.py BR-DAR KL-WAY

**Replaces two more inferred layers.** ``cropland_fraction`` is currently a
single district-wide constant from Census net-sown-area, spread across the grid
by terrain rather than observed. WorldCover gives a real land-cover class per
10 m pixel, from Sentinel-1/2, at global coverage with no credentials.

Class 40 (cropland) becomes ``cropland_fraction`` per cell, replacing the
constant. Class 10 (tree cover) becomes a real baseline for
``core/deforestation.py``, which currently infers a "should be forested" mask
from terrain and slope rather than observing what is actually there. Class 50
(built-up) is kept too, as an independent cross-check against GHSL rather than
to replace it — two real sources agreeing is a stronger claim than either alone.

**Tiling.** WorldCover ships as 3°x3° Cloud-Optimized GeoTIFFs on S3, named by
their lower-left corner: a point at 26.15N, 85.90E falls in tile ``N24E084``.
Verified against the real bucket rather than assumed from documentation. A
district can span up to four tiles; all intersecting ones are mosaicked exactly
as ``fetch_real_dem.py`` mosaics one-degree DEM tiles.

Each tile is ~120 MB, but a windowed ``/vsicurl`` read pulls only the byte
ranges the district's grid needs — this is the same COG mechanism the DEM bake
already relies on, not a district-by-district full-tile download.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np  # noqa: E402

from app.core.grid import Grid  # noqa: E402
from app.data import districts as districts_data  # noqa: E402

BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "app",
                       "data", "worldcover")

# ESA WorldCover class codes that matter here. The full legend has eleven
# classes; only these three feed a layer this system uses.
CLASS_TREE, CLASS_CROP, CLASS_BUILT = 10, 40, 50


def tile_id(lat: float, lon: float) -> str:
    la = int(math.floor(lat / 3.0)) * 3
    lo = int(math.floor(lon / 3.0)) * 3
    ns, ew = ("N" if la >= 0 else "S"), ("E" if lo >= 0 else "W")
    return "%s%02d%s%03d" % (ns, abs(la), ew, abs(lo))


def tiles_for(grid: Grid):
    """Every 3-degree tile the grid's corners fall in."""
    out = set()
    for la in (grid.lat0, grid.lat1):
        for lo in (grid.lon0, grid.lon1):
            out.add(tile_id(la, lo))
    return sorted(out)


def tile_url(tid: str) -> str:
    return "/vsicurl/%s/ESA_WorldCover_10m_2021_v200_%s_Map.tif" % (BASE, tid)


def fetch_district(grid: Grid, retries: int = 2, verbose: bool = True):
    """Cropland / tree-cover / built-up fraction per analysis cell.

    Reads the class raster with nearest-neighbour (categorical data must never
    be interpolated) onto a grid several times finer than the analysis cell,
    then reduces each analysis cell to the *fraction* of its native pixels in
    each class. That fraction is what ``exposure.py`` actually wants; the raw
    class codes are not.
    """
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject

    n = grid.n
    # Oversample 4x so a fraction can be computed rather than a single nearest
    # class per analysis cell - a 200 m cell can genuinely be part cropland,
    # part built-up, and a single nearest sample would erase that.
    over = 4
    m = n * over
    dst_transform = from_bounds(grid.lon0, grid.lat0, grid.lon1, grid.lat1, m, m)
    mosaic = np.zeros((m, m), np.uint8)
    covered = np.zeros((m, m), bool)
    used, dropped = 0, []

    for tid in tiles_for(grid):
        part = None
        last_exc = None
        for attempt in range(retries + 1):
            try:
                with rasterio.open(tile_url(tid)) as src:
                    part = np.zeros((m, m), np.uint8)
                    reproject(source=rasterio.band(src, 1), destination=part,
                              dst_transform=dst_transform, dst_crs="EPSG:4326",
                              src_nodata=src.nodata, dst_nodata=0,
                              resampling=Resampling.nearest)
                break
            except Exception as exc:
                last_exc = exc
                part = None
                if attempt < retries:
                    time.sleep(2.0 * (attempt + 1))

        if part is None:
            dropped.append((tid, str(last_exc)[:70]))
            continue

        got = part > 0
        mosaic = np.where(got & ~covered, part, mosaic)
        covered |= got
        used += 1

    if dropped:
        for tid, msg in dropped:
            print("      WARNING tile %s dropped after retries: %s" % (tid, msg))
    if not used:
        return None

    def fraction(cls: int) -> np.ndarray:
        hit = (mosaic == cls).astype(np.float32)
        # Block-mean the oversampled grid back down to the analysis grid.
        return hit.reshape(n, over, n, over).mean(axis=(1, 3)).astype(np.float32)

    return {
        "cropland": fraction(CLASS_CROP),
        "tree_cover": fraction(CLASS_TREE),
        "built_up": fraction(CLASS_BUILT),
    }


def bake_district(code: str, verbose: bool = True):
    d = districts_data.get(code)
    grid = Grid.for_district(d.lat, d.lon, d.area_km2, terrain=d.terrain)
    layers = fetch_district(grid, verbose=verbose)
    if layers is None:
        return None

    if verbose:
        print("      cropland %.0f%%  tree cover %.0f%%  built-up %.0f%%  "
              "(Census cropland_fraction on file: %.0f%%)"
              % (100 * layers["cropland"].mean(), 100 * layers["tree_cover"].mean(),
                 100 * layers["built_up"].mean(), 100 * d.cropland_fraction))

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.npz" % code)
    np.savez_compressed(
        path, source="ESA WorldCover 10m v200 (2021)", **layers)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="*")
    args = ap.parse_args()

    try:
        import rasterio  # noqa: F401
    except ImportError:
        print("rasterio is needed at bake time:  pip install rasterio")
        return 1

    codes = args.codes or [d.code for d in districts_data.DISTRICTS]
    print("ESA WorldCover 10m -> district grids")
    print("%d district(s)\n" % len(codes))

    total, ok = 0, 0
    for code in codes:
        d = districts_data.get(code)
        print("  %-8s %s, %s" % (code, d.name, d.state))
        path = bake_district(code)
        if not path:
            print("      no tiles resolved - skipped")
            continue
        ok += 1
        total += os.path.getsize(path)

    print("\n%d/%d districts baked, %.1f MB in backend/app/data/worldcover/"
          % (ok, len(codes), total / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
