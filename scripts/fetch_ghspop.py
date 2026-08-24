"""Bake observed population counts from the JRC Global Human Settlement Layer.

    python scripts/fetch_ghspop.py
    python scripts/fetch_ghspop.py BR-DAR KL-WAY

**This replaces the largest inferred layer in the system.** Until now population
was the district's Census total spread across the grid *dasymetrically* — in
proportion to a built-up surface that was itself generated from the terrain. That
is a defensible way to produce a plausible surface and it is not the same thing
as observing one. GHS-POP is an observation: residential population per 100 m
cell, derived from census and built-up surface by the Joint Research Centre.

Everything here is copied deliberately from ``fetch_ghsl.py`` — same Mollweide
sphere, same 1,000 km tile grid, same ``/vsizip//vsicurl/`` windowed read, same
mosaicking across the several tiles a district usually spans. Only the product
path and the resampling rule differ.

**The resampling rule is the one thing that is genuinely different, and getting
it wrong would be silent.** Built-up surface is a *density* and is resampled by
averaging. Population is a *count*, and averaging counts onto a coarser grid
throws away most of the people. ``Resampling.sum`` aggregates the 100 m cells
falling inside each analysis cell, which is what conserves the district total —
and that total is then checked against the Census projection, because two real
sources disagreeing is information and quietly trusting one is not.

Two epochs: 2025 for current exposure, 2015 so that population growth *inside*
red zones can be measured against the built-up change already tracked by
``core/encroachment.py``.
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
        "GHS_POP_GLOBE_R2023A")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "app",
                       "data", "pop")

# 2025 drives exposure; 2015 makes population growth inside red zones
# measurable against the GHS-BUILT-S change already baked.
EPOCHS = (2015, 2025)

# GHSL's Mollweide sphere radius and its 1,000 km tile grid. Identical to
# GHS-BUILT-S — the two products share a grid, which is why a district's
# population and built-up layers line up cell for cell.
R_MOLLWEIDE = 6371007.181
TILE_M = 1_000_000
X0 = 18_041_000
Y0 = 9_000_000


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
    """Every Mollweide tile the grid's corners, edges and centre fall in."""
    out = set()
    lats = (grid.lat0, 0.5 * (grid.lat0 + grid.lat1), grid.lat1)
    lons = (grid.lon0, 0.5 * (grid.lon0 + grid.lon1), grid.lon1)
    for la in lats:
        for lo in lons:
            out.add(tile_of(lo, la))
    return sorted(out)


def tile_url(epoch: int, row: int, col: int) -> str:
    name = ("GHS_POP_E%d_GLOBE_R2023A_54009_100_V1_0_R%d_C%d"
            % (epoch, row, col))
    return ("/vsizip//vsicurl/%s/GHS_POP_E%d_GLOBE_R2023A_54009_100/V1-0/"
            "tiles/%s.zip/%s.tif" % (BASE, epoch, name, name))


def read_epoch(grid: Grid, epoch: int, retries: int = 2):
    """Population count per analysis cell for one epoch, or None if no tiles.

    Failures are reported rather than swallowed. A ``continue`` behind a bare
    ``except`` is how one dropped tile in a mosaic of several turns into a
    silently truncated district total - the array still looks valid, it is
    just short by however many people were on the tile that failed. Every
    dropped tile is printed, and a transient failure gets one retry after a
    short pause before it counts as dropped.
    """
    import time as _time

    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject

    n = grid.n
    dst_transform = from_bounds(grid.lon0, grid.lat0, grid.lon1, grid.lat1, n, n)
    mosaic = np.zeros((n, n), np.float32)
    covered = np.zeros((n, n), bool)
    used, dropped = 0, []

    for row, col in tiles_for(grid):
        part = None
        last_exc = None
        for attempt in range(retries + 1):
            try:
                with rasterio.open(tile_url(epoch, row, col)) as src:
                    part = np.zeros((n, n), np.float32)
                    reproject(source=rasterio.band(src, 1), destination=part,
                              dst_transform=dst_transform, dst_crs="EPSG:4326",
                              src_nodata=src.nodata, dst_nodata=0,
                              # SUM, not average. These are people, not a
                              # density: averaging counts onto a coarser cell
                              # discards most of them and the error is silent -
                              # the map still looks like a population map, it
                              # is just wrong by the cell-area ratio.
                              resampling=Resampling.sum)
                break
            except Exception as exc:
                last_exc = exc
                part = None
                if attempt < retries:
                    _time.sleep(2.0 * (attempt + 1))

        if part is None:
            dropped.append((row, col, str(last_exc)[:70]))
            continue

        # GHS-POP uses a negative nodata over ocean; clamp before mosaicking so
        # a nodata margin cannot subtract real people at a tile seam.
        part = np.where(np.isfinite(part) & (part > 0), part, 0.0)
        got = part > 0
        mosaic = np.where(got & ~covered, part, mosaic)
        covered |= got
        used += 1

    if dropped:
        for row, col, msg in dropped:
            print("      WARNING tile R%d_C%d dropped after retries: %s"
                  % (row, col, msg))
    if not used:
        return None
    return mosaic.astype(np.float32)


def bake_district(code: str, verbose: bool = True):
    d = districts_data.get(code)
    grid = Grid.for_district(d.lat, d.lon, d.area_km2, terrain=d.terrain)
    layers, got = {}, []

    for epoch in EPOCHS:
        arr = read_epoch(grid, epoch)
        if arr is None:
            continue
        layers["pop_%d" % epoch] = arr
        got.append(epoch)
        if verbose:
            print("      %d: total %s  peak cell %.0f"
                  % (epoch, "{:,}".format(int(arr.sum())), arr.max()))

    if not got:
        return None, [], {}

    # Reconcile against the Census projection. The grid frame is square and a
    # district is not, so the frame total legitimately exceeds the district
    # total - this is a sanity check on the resampling, not a correction.
    latest = layers["pop_%d" % got[-1]]
    census = float(d.population_2025)
    frame = float(latest.sum())
    check = {
        "census_2025_projection": census,
        "ghspop_frame_total": frame,
        "ratio_frame_to_census": (frame / census) if census else 0.0,
    }
    if verbose:
        print("      census %s  vs frame %s  (x%.2f)"
              % ("{:,}".format(int(census)), "{:,}".format(int(frame)),
                 check["ratio_frame_to_census"]))

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.npz" % code)
    np.savez_compressed(
        path, epochs=np.array(got),
        source="GHS-POP R2023A (JRC), 100 m, Mollweide, residential population",
        census_2025_projection=np.float64(census),
        ghspop_frame_total=np.float64(frame),
        **layers)
    return path, got, check


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
    print("GHS-POP residential population -> district grids")
    print("%d district(s), epochs %s\n" % (len(codes), list(EPOCHS)))

    total, ok, ratios = 0, 0, []
    for code in codes:
        d = districts_data.get(code)
        print("  %-8s %s, %s" % (code, d.name, d.state))
        path, got, check = bake_district(code)
        if not path:
            print("      no tiles resolved - skipped")
            continue
        ok += 1
        total += os.path.getsize(path)
        ratios.append(check["ratio_frame_to_census"])

    print("\n%d/%d districts baked, %.1f MB in backend/app/data/pop/"
          % (ok, len(codes), total / 1e6))
    if ratios:
        ratios.sort()
        print("frame/census ratio: min %.2f  median %.2f  max %.2f"
              % (ratios[0], ratios[len(ratios) // 2], ratios[-1]))
        print("A square frame around a non-square district holds more people "
              "than the district does, so ratios above 1 are expected. A ratio "
              "far below 1 would mean the resampling is losing people.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
