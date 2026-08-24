"""Bake road networks and health/shelter facilities from OpenStreetMap.

    python scripts/fetch_osm.py
    python scripts/fetch_osm.py BR-DAR KL-WAY

**Replaces the two layers `core/exposure.py`'s own docstring names as least
defensible.** Roads today are least-cost paths computed between synthetic
settlement points, not surveyed alignments. Facilities are placed at Indian
Public Health Standards *provision rates* — a plausible count, at plausible
locations, observing nothing. Both come from Overpass (the OSM query API): no
account, no key, and India's road and facility tagging is, in practice, dense.

**Capacity is still estimated, and that is stated rather than hidden.** OSM
rarely tags `beds=` on a hospital node. An *observed* facility at an *inferred*
capacity is still a real improvement over an inferred facility at an inferred
capacity, and the IPHS provision-rate capacity figures already in
``exposure.py`` are kept as the estimate for exactly that reason — they are not
being replaced, only the thing they are attached to.

**Shelters map to schools**, which is not a guess: NDMA's own flood response
doctrine designates schools as the standard relief-camp building stock in
India, and this system's own SOP data (`data/sops.py`) already assumes it.

One combined Overpass query per district — roads and facilities together, not
two round trips — with the raw response cached to disk so a re-run after a
partial failure costs nothing. Overpass is a shared public service with real
rate limits; a pause sits between requests and failures are retried with
backoff rather than silently dropped, the same discipline as every other
fetch script in this round.

**Licence: OpenStreetMap contributors, ODbL.** Attribution is baked into the
output and must be carried through to the provenance block and the Evidence
screen — this is the one dataset in the system that legally requires it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np  # noqa: E402

from app.core.grid import Grid  # noqa: E402
from app.data import districts as districts_data  # noqa: E402

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "_cache_osm")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "app",
                       "data", "osm")

HIGHWAY_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary")

ATTRIBUTION = ("Map data © OpenStreetMap contributors, ODbL 1.0 - "
              "https://www.openstreetmap.org/copyright")


def query_for(bbox) -> str:
    s, w, n, e = bbox
    hw = "|".join(HIGHWAY_CLASSES)
    return (
        "[out:json][timeout:90];("
        'way["highway"~"^(%s)$"](%f,%f,%f,%f);'
        'node["amenity"~"^(hospital|clinic)$"](%f,%f,%f,%f);'
        'node["amenity"="school"](%f,%f,%f,%f);'
        ");out geom;"
        % (hw, s, w, n, e, s, w, n, e, s, w, n, e))


def cache_path(code: str) -> str:
    return os.path.join(CACHE_DIR, "%s.json" % code)


def fetch_overpass(code: str, bbox, retries: int = 3) -> dict:
    """Run one district's combined query, cached to disk.

    A re-run after a partial failure - the whole point of tonight's schedule -
    must not re-spend Overpass's goodwill re-fetching districts that already
    succeeded.
    """
    path = cache_path(code)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    q = query_for(bbox)
    data = urllib.parse.urlencode({"data": q}).encode("utf-8")
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                OVERPASS_URL, data=data,
                headers={"User-Agent": "drishti/1.0 (disaster-risk research)"})
            with urllib.request.urlopen(req, timeout=100) as r:
                body = r.read()
            result = json.loads(body)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body.decode("utf-8"))
            return result
        except urllib.error.HTTPError as exc:
            last_exc = exc
            # 429/504 are Overpass under load, not a real failure - worth a
            # longer wait than a generic retry.
            wait = 20.0 * (attempt + 1) if exc.code in (429, 504) else 5.0
            print("      HTTP %s, waiting %.0fs" % (exc.code, wait))
            time.sleep(wait)
        except Exception as exc:
            last_exc = exc
            time.sleep(5.0 * (attempt + 1))
    raise RuntimeError("Overpass query failed after %d attempts: %s"
                       % (retries, last_exc))


def rasterize_roads(elements, grid: Grid) -> np.ndarray:
    """Highway ways -> a boolean road mask on the analysis grid."""
    import rasterio.features
    from rasterio.transform import from_bounds

    n = grid.n
    lines = []
    for el in elements:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
        if len(coords) >= 2:
            lines.append({"type": "LineString", "coordinates": coords})

    if not lines:
        return np.zeros((n, n), bool)

    transform = from_bounds(grid.lon0, grid.lat0, grid.lon1, grid.lat1, n, n)
    mask = rasterio.features.rasterize(
        [(g, 1) for g in lines], out_shape=(n, n), transform=transform,
        fill=0, dtype=np.uint8, all_touched=True)
    return mask.astype(bool)


def extract_facilities(elements, grid: Grid):
    """Hospital/clinic/school nodes -> (kind, row, col, lat, lon, name) tuples."""
    out = []
    for el in elements:
        if el.get("type") != "node":
            continue
        tags = el.get("tags") or {}
        amenity = tags.get("amenity")
        if amenity in ("hospital", "clinic"):
            kind = "hospital"
        elif amenity == "school":
            kind = "shelter"
        else:
            continue
        lat, lon = el.get("lat"), el.get("lon")
        if lat is None or lon is None:
            continue
        row, col = grid.rowcol(lat, lon)
        name = tags.get("name", "")
        out.append((kind, row, col, float(lat), float(lon), name))
    return out


def bake_district(code: str, verbose: bool = True):
    d = districts_data.get(code)
    grid = Grid.for_district(d.lat, d.lon, d.area_km2, terrain=d.terrain)
    bbox = (grid.lat0, grid.lon0, grid.lat1, grid.lon1)

    result = fetch_overpass(code, bbox)
    elements = result.get("elements", [])

    road_mask = rasterize_roads(elements, grid)
    facilities = extract_facilities(elements, grid)
    n_hosp = sum(1 for f in facilities if f[0] == "hospital")
    n_shelter = sum(1 for f in facilities if f[0] == "shelter")

    if verbose:
        road_km = float(road_mask.sum()) * grid.cell_m / 1000.0
        print("      roads %.0f km on-grid, %d hospitals/clinics, "
              "%d schools (shelter candidates)" % (road_km, n_hosp, n_shelter))

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.npz" % code)

    kinds = np.array([f[0] for f in facilities])
    rows = np.array([f[1] for f in facilities], np.int32)
    cols = np.array([f[2] for f in facilities], np.int32)
    lats = np.array([f[3] for f in facilities], np.float64)
    lons = np.array([f[4] for f in facilities], np.float64)
    names = np.array([f[5] for f in facilities])

    np.savez_compressed(
        path, road_mask=road_mask, facility_kind=kinds,
        facility_row=rows, facility_col=cols,
        facility_lat=lats, facility_lon=lons, facility_name=names,
        source="OpenStreetMap contributors, via Overpass API",
        attribution=ATTRIBUTION)
    return path, n_hosp, n_shelter


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
    print("OpenStreetMap (Overpass) -> roads and facilities, district grids")
    print("%d district(s)\n" % len(codes))
    print(ATTRIBUTION + "\n")

    total, ok, failed = 0, 0, []
    for i, code in enumerate(codes):
        d = districts_data.get(code)
        print("  %-8s %s, %s" % (code, d.name, d.state))
        try:
            path, n_hosp, n_shelter = bake_district(code)
        except Exception as exc:
            print("      FAILED: %s" % exc)
            failed.append(code)
            continue
        ok += 1
        total += os.path.getsize(path)
        # Be a considerate client of a shared public service; skip the pause
        # entirely when the response came from cache.
        if not os.path.exists(cache_path(code)) or i < len(codes) - 1:
            time.sleep(2.0)

    print("\n%d/%d districts baked, %.1f MB in backend/app/data/osm/"
          % (ok, len(codes), total / 1e6))
    if failed:
        print("failed: %s - re-run to retry (already-cached ones are free)"
              % ", ".join(failed))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
