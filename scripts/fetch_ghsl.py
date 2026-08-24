"""Bake built-up surface from the Global Human Settlement Layer.

    python scripts/fetch_ghsl.py              # all districts
    python scripts/fetch_ghsl.py BR-DAR KL-WAY

GHSL is the JRC's global record of built-up surface, published every five years
from 1975 to 2030 at 100 m. It is what makes **encroachment detection** possible:
built-up area inside a Red Zone, differenced between epochs, answers the question
the problem statement is really asking — *"who has built inside the danger since
we last looked"*.

That question is the sharpest form of "dynamically update Red Zones", and it is
directly actionable: it tells a State Disaster Management Authority exactly where
to stop issuing building permissions.

Same pattern as ``fetch_real_dem.py``: windowed reads over HTTP, resampled onto
each district's analysis grid, stored as a compressed ``.npz`` the app loads with
numpy alone. rasterio is a bake-time dependency only.

The tiles are in Mollweide (ESRI:54009), so the tile index has to be computed
through the projection rather than from latitude and longitude directly.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.zip")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np  # noqa: E402

from app.core.grid import Grid  # noqa: E402
from app.data import districts as districts_data  # noqa: E402

BASE = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
        "GHS_BUILT_S_GLOBE_R2023A")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "app",
                       "data", "ghsl")

# Four epochs spanning 35 years: enough to establish a trend and to answer
# "since 2015" without quadrupling the download.
EPOCHS = (1990, 2005, 2015, 2025)

# GHSL's Mollweide sphere radius and its 1,000 km tile grid.
R_MOLLWEIDE = 6371007.181
TILE_M = 1_000_000
X0 = 18_041_000
Y0 = 9_000_000

# One GHSL cell is 100 m, so a full cell is 10,000 m2 of ground.
CELL_AREA_M2 = 10_000.0


def mollweide(lon: float, lat: float):
    """Forward Mollweide, solved by Newton iteration as the projection requires."""
    lam, phi = math.radians(lon), math.radians(lat)
    theta = phi
    for _ in range(60):
        denom = 2 + 2 * math.cos(2 * theta)
        if abs(denom) < 1e-12:
            break
        d = (2 * theta + math.sin(2 * theta) - math.pi * math.sin(phi)) / denom
        theta -= d
        if abs(d) < 1e-12:
            break
    x = (R_MOLLWEIDE * 2 * math.sqrt(2) / math.pi) * lam * math.cos(theta)
    y = R_MOLLWEIDE * math.sqrt(2) * math.sin(theta)
    return x, y


def tile_of(lon: float, lat: float):
    x, y = mollweide(lon, lat)
    return (int((Y0 - y) // TILE_M) + 1, int((x + X0) // TILE_M) + 1)


def tiles_for(grid: Grid):
    """Every Mollweide tile the grid's corners and edges fall in."""
    out = set()
    lats = (grid.lat0, 0.5 * (grid.lat0 + grid.lat1), grid.lat1)
    lons = (grid.lon0, 0.5 * (grid.lon0 + grid.lon1), grid.lon1)
    for la in lats:
        for lo in lons:
            out.add(tile_of(lo, la))
    return sorted(out)


def tile_url(epoch: int, row: int, col: int) -> str:
    name = ("GHS_BUILT_S_E%d_GLOBE_R2023A_54009_100_V1_0_R%d_C%d"
            % (epoch, row, col))
    return ("/vsizip//vsicurl/%s/GHS_BUILT_S_E%d_GLOBE_R2023A_54009_100/V1-0/"
            "tiles/%s.zip/%s.tif" % (BASE, epoch, name, name))


def read_epoch(grid: Grid, epoch: int, verbose: bool = True):
    """Built-up fraction (0..1) on the district grid for one epoch."""
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject

    n = grid.n
    dst_transform = from_bounds(grid.lon0, grid.lat0, grid.lon1, grid.lat1, n, n)
    mosaic = np.zeros((n, n), np.float32)
    covered = np.zeros((n, n), bool)
    used = 0

    for row, col in tiles_for(grid):
        try:
            with rasterio.open(tile_url(epoch, row, col)) as src:
                part = np.zeros((n, n), np.float32)
                reproject(source=rasterio.band(src, 1), destination=part,
                          dst_transform=dst_transform, dst_crs="EPSG:4326",
                          src_nodata=src.nodata, dst_nodata=0,
                          # Average, not nearest: we want the mean built-up
                          # surface across the coarser analysis cell, and
                          # nearest would sample one 100 m pixel and call it
                          # the whole 200 m cell.
                          resampling=Resampling.average)
            got = part > 0
            mosaic = np.where(got & ~covered, part, mosaic)
            covered |= got
            used += 1
        except Exception:
            continue

    if not used:
        return None
    return np.clip(mosaic / CELL_AREA_M2, 0.0, 1.0).astype(np.float32)


def bake_district(code: str, verbose: bool = True):
    d = districts_data.get(code)
    grid = Grid.for_district(d.lat, d.lon, d.area_km2, terrain=d.terrain)
    layers, got = {}, []
    for epoch in EPOCHS:
        arr = read_epoch(grid, epoch, verbose)
        if arr is None:
            continue
        layers["built_%d" % epoch] = arr
        got.append(epoch)
        if verbose:
            print("      %d: mean %.3f  max %.3f" % (epoch, arr.mean(), arr.max()))
    if not got:
        return None, []

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.npz" % code)
    np.savez_compressed(
        path, epochs=np.array(got),
        source="GHS-BUILT-S R2023A (JRC), 100 m, Mollweide",
        **layers)
    return path, got


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="*")
    args = ap.parse_args()

    try:
        import rasterio  # noqa: F401
    except ImportError:
        print("rasterio is needed at bake time:  pip install rasterio")
        return 1

    codes = args.codes or [d.code for d in districts_data.DISTRICTS]
    print("GHSL built-up surface -> district grids")
    print("%d district(s), epochs %s\n" % (len(codes), list(EPOCHS)))

    total, ok = 0, 0
    for code in codes:
        d = districts_data.get(code)
        print("  %-8s %s, %s" % (code, d.name, d.state))
        path, got = bake_district(code)
        if not path:
            print("      no tiles resolved — skipped")
            continue
        ok += 1
        total += os.path.getsize(path)

    print("\n%d/%d districts baked, %.1f MB in backend/app/data/ghsl/"
          % (ok, len(codes), total / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
