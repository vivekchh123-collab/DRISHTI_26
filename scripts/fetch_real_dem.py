"""Bake real elevation for every district from the Copernicus DEM.

    python scripts/fetch_real_dem.py              # all districts
    python scripts/fetch_real_dem.py BR-DAR KL-WAY
    python scripts/fetch_real_dem.py --verify     # check against published ranges

**Copernicus DEM GLO-30 is open, global, 30 m, and needs no account.** It sits
on AWS Open Data as Cloud-Optimized GeoTIFFs with byte-range support, so each
district is read as a window over HTTP — a few megabytes rather than the 46 MB
per one-degree tile.

The output is a small ``.npz`` per district holding elevation already resampled
onto that district's analysis grid: 160x160 float32, about 100 kB each, ~2 MB
for all twenty-two. Those files ship with the application, which is what lets
the app run on **real terrain with no GDAL, no rasterio and no network at
runtime**. rasterio is needed only here, at bake time.

Two details that are easy to get wrong:

* **A district grid usually spans several one-degree tiles.** Reading only the
  tile containing the centroid leaves a third of the frame as nodata zeros,
  which then reads as sea-level ground and floods spectacularly. Every
  intersecting tile is mosaicked.
* **The archive has no tiles over open ocean.** A missing tile is not an error,
  it is water — and that is how the coastline is derived for the coastal
  districts, rather than by guessing at a bearing.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np  # noqa: E402

from app.core.grid import Grid  # noqa: E402
from app.data import districts as districts_data  # noqa: E402

BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "app",
                       "data", "dem")

# Copernicus DEM reports sea surface as 0 and uses this for genuine voids.
NODATA = -32767.0
# Depth assigned to cells with no tile, i.e. open ocean. The hydrology only
# needs the sea to be below land and connected; the exact value is arbitrary.
SEA_DEPTH_M = -12.0


def tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return ("Copernicus_DSM_COG_10_%s%02d_00_%s%03d_00_DEM"
            % (ns, abs(lat), ew, abs(lon)))


def tiles_for(grid: Grid):
    """Every one-degree tile the grid touches."""
    out = []
    for lat in range(int(math.floor(grid.lat0)), int(math.floor(grid.lat1)) + 1):
        for lon in range(int(math.floor(grid.lon0)),
                         int(math.floor(grid.lon1)) + 1):
            out.append((lat, lon))
    return out


def fetch_district(grid: Grid, verbose: bool = True):
    """Mosaic the Copernicus DEM onto a district grid.

    Returns ``(elevation, sea_mask, stats)``.
    """
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject

    n = grid.n
    dst_transform = from_bounds(grid.lon0, grid.lat0, grid.lon1, grid.lat1, n, n)
    mosaic = np.full((n, n), np.nan, np.float32)
    found, missing = [], []

    for lat, lon in tiles_for(grid):
        name = tile_name(lat, lon)
        url = "/vsicurl/%s/%s/%s.tif" % (BASE, name, name)
        try:
            with rasterio.open(url) as src:
                part = np.full((n, n), NODATA, np.float32)
                reproject(source=rasterio.band(src, 1), destination=part,
                          dst_transform=dst_transform, dst_crs="EPSG:4326",
                          src_nodata=src.nodata, dst_nodata=NODATA,
                          resampling=Resampling.bilinear)
            valid = (part > NODATA + 1) & np.isfinite(part)
            mosaic = np.where(valid & ~np.isfinite(mosaic), part, mosaic)
            found.append(name)
        except Exception:
            # No tile means open ocean, not a failure.
            missing.append(name)

    if not found:
        raise SystemExit("no Copernicus DEM tiles covered this grid")

    # Anything still unfilled is sea: either a missing tile or a void inside one.
    sea = ~np.isfinite(mosaic)
    elevation = np.where(sea, SEA_DEPTH_M, mosaic).astype(np.float32)

    stats = {
        "tiles_used": len(found),
        "tiles_absent_ocean": len(missing),
        "sea_fraction": float(sea.mean()),
        "min_m": float(elevation[~sea].min()) if (~sea).any() else 0.0,
        "max_m": float(elevation[~sea].max()) if (~sea).any() else 0.0,
        "mean_m": float(elevation[~sea].mean()) if (~sea).any() else 0.0,
    }
    if verbose:
        print("      %d tile(s), %d absent (ocean), sea %.0f%%"
              % (stats["tiles_used"], stats["tiles_absent_ocean"],
                 100 * stats["sea_fraction"]))
    return elevation, sea, stats


# Native Copernicus DEM posting, in degrees. 30 m is 1/3600 of a degree.
NATIVE_DEG = 1.0 / 3600.0


def subcell_slope(grid: Grid, verbose: bool = True):
    """Slope statistics computed at the DEM's native 30 m, per analysis cell.

    Slope is scale-dependent, and that is not a detail — it decides whether the
    landslide model sees anything at all. A hillslope that fails is 50-100 m
    across; measured over a 300 m cell it is averaged against the flat ground
    around it and disappears. Wayanad came out at a mean 4.9 degrees with 1.3%
    of cells above 25, which is a plateau statistic for a district whose 2024
    disaster happened on a steep escarpment.

    So slope is computed on the native 30 m grid, where the hillslope actually
    exists, and aggregated to the analysis cell by its **90th percentile and
    maximum** rather than its mean. A cell is landslide terrain if part of it is
    steep; averaging asks the wrong question.

    Returns ``(slope_p90, slope_max)`` on the analysis grid, or None.
    """
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject

    n = grid.n
    # Round up to a whole number of native pixels per analysis cell so the
    # aggregation is an exact reshape rather than an interpolation.
    span_deg = max(grid.lon1 - grid.lon0, grid.lat1 - grid.lat0)
    factor = max(int(math.ceil((span_deg / NATIVE_DEG) / n)), 1)
    size = n * factor
    if factor < 2:
        return None                       # already at native resolution

    dst_transform = from_bounds(grid.lon0, grid.lat0, grid.lon1, grid.lat1,
                                size, size)
    fine = np.full((size, size), np.nan, np.float32)
    for lat, lon in tiles_for(grid):
        name = tile_name(lat, lon)
        url = "/vsicurl/%s/%s/%s.tif" % (BASE, name, name)
        try:
            with rasterio.open(url) as src:
                part = np.full((size, size), NODATA, np.float32)
                reproject(source=rasterio.band(src, 1), destination=part,
                          dst_transform=dst_transform, dst_crs="EPSG:4326",
                          src_nodata=src.nodata, dst_nodata=NODATA,
                          resampling=Resampling.bilinear)
            valid = (part > NODATA + 1) & np.isfinite(part)
            fine = np.where(valid & ~np.isfinite(fine), part, fine)
        except Exception:
            continue
    if not np.isfinite(fine).any():
        return None
    fine = np.nan_to_num(fine, nan=0.0)

    cell_m = grid.cell_m / factor
    pad = np.pad(fine, 1, mode="edge")
    z1, z2, z3 = pad[:-2, :-2], pad[:-2, 1:-1], pad[:-2, 2:]
    z4, z6 = pad[1:-1, :-2], pad[1:-1, 2:]
    z7, z8, z9 = pad[2:, :-2], pad[2:, 1:-1], pad[2:, 2:]
    dzdx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8.0 * cell_m)
    dzdy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8.0 * cell_m)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy))).astype(np.float32)

    blocks = slope.reshape(n, factor, n, factor).transpose(0, 2, 1, 3)
    blocks = blocks.reshape(n, n, factor * factor)
    p90 = np.percentile(blocks, 90, axis=2).astype(np.float32)
    smax = blocks.max(axis=2).astype(np.float32)
    if verbose:
        print("      native slope: %d m posting, %dx%d per cell, "
              "p90 mean %.1f deg (grid-scale mean %.1f)"
              % (round(cell_m), factor, factor, p90.mean(),
                 slope.reshape(n, factor, n, factor).mean(axis=(1, 3)).mean()))
    return p90, smax


def save(code: str, elevation, sea, stats, slope_p90=None, slope_max=None) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.npz" % code)
    extra = {}
    if slope_p90 is not None:
        extra["slope_p90"] = slope_p90
    if slope_max is not None:
        extra["slope_max"] = slope_max
    np.savez_compressed(path, elevation=elevation, sea=sea,
                        source="Copernicus DEM GLO-30 (ESA, 30 m)",
                        **extra, **{k: v for k, v in stats.items()})
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="*", help="district codes; default all")
    ap.add_argument("--verify", action="store_true",
                    help="compare against the published elevation range")
    args = ap.parse_args()

    try:
        import rasterio  # noqa: F401
    except ImportError:
        print("rasterio is needed to bake DEM tiles (bake time only, not runtime):")
        print("    pip install rasterio")
        return 1

    codes = args.codes or [d.code for d in districts_data.DISTRICTS]
    print("Copernicus DEM GLO-30 -> district grids")
    print("%d district(s)\n" % len(codes))

    rows, total_bytes = [], 0
    for code in codes:
        d = districts_data.get(code)
        g = Grid.for_district(d.lat, d.lon, d.area_km2, terrain=d.terrain)
        print("  %-8s %s, %s" % (code, d.name, d.state))
        elevation, sea, stats = fetch_district(g)
        sub = subcell_slope(g)
        path = save(code, elevation, sea, stats,
                    *(sub if sub is not None else (None, None)))
        total_bytes += os.path.getsize(path)

        lo, hi = stats["min_m"], stats["max_m"]
        # The published range is for the administrative district; our frame is a
        # padded square around it, so it legitimately reaches a little further.
        # A gross mismatch means the mosaic is wrong.
        ok = (hi >= d.elev_min_m) and (lo <= max(d.elev_max_m * 1.6, 60))
        rows.append((code, d.name, lo, hi, d.elev_min_m, d.elev_max_m, ok,
                     stats["sea_fraction"]))
        print("      real %.0f..%.0f m   published %.0f..%.0f m   %s"
              % (lo, hi, d.elev_min_m, d.elev_max_m, "ok" if ok else "CHECK"))

    print("\n" + "=" * 78)
    print("%-8s %-22s %14s %14s %6s" % ("CODE", "DISTRICT", "REAL", "PUBLISHED", "SEA"))
    print("-" * 78)
    bad = 0
    for code, name, lo, hi, plo, phi, ok, seaf in rows:
        if not ok:
            bad += 1
        print("%-8s %-22s %6.0f..%-6.0f %6.0f..%-6.0f %5.0f%% %s"
              % (code, name[:22], lo, hi, plo, phi, 100 * seaf,
                 "" if ok else " <-- CHECK"))
    print("=" * 78)
    print("%d districts baked, %.1f MB total, written to backend/app/data/dem/"
          % (len(rows), total_bytes / 1e6))
    if bad:
        print("%d district(s) disagree with the published range - inspect before shipping" % bad)
    else:
        print("every district agrees with its published elevation range")
    return 0


if __name__ == "__main__":
    sys.exit(main())
