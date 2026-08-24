"""Per-stage timings for every district.

Run this before a demonstration and quote the number. "A district solves in
about two seconds on a laptop, with no GPU" is a claim worth being able to
support on the spot.

    python scripts/bench.py
    python scripts/bench.py --district BR-DAR --repeat 3
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core import (boundary, exposure, flood, hydrology, impact,  # noqa: E402
                      rainfall, response, scenario, waterlogging)
from app.core.grid import Grid  # noqa: E402
from app.core.terrain import build_terrain  # noqa: E402
from app.data import districts as districts_data  # noqa: E402


def bench_one(d, severity="severe"):
    t = {}
    t0 = time.perf_counter()

    grid = Grid.for_district(d.lat, d.lon, d.area_km2, terrain=d.terrain)
    mark = time.perf_counter(); t["grid"] = mark - t0

    terrain = build_terrain(grid, d.terrain, d.code, d.seed)
    t["terrain"] = time.perf_counter() - mark; mark = time.perf_counter()

    bnd = boundary.load(grid, d.code, d.lat, d.lon, d.area_km2, d.seed,
                        must_include=(d.hq_lat, d.hq_lon))
    t["boundary"] = time.perf_counter() - mark; mark = time.perf_counter()

    exp = exposure.build_exposure(
        grid, terrain, bnd, population_total=d.population_2025,
        urban_fraction=d.urban_fraction, cropland_fraction=d.cropland_fraction,
        hq_lat=d.hq_lat, hq_lon=d.hq_lon, hq_name=d.hq_name)
    t["exposure"] = time.perf_counter() - mark; mark = time.perf_counter()

    ev = rainfall.build_event(grid, terrain, annual_normal_mm=d.rainfall_normal_mm,
                              flood_driver=d.flood_driver, severity=severity,
                              seed=d.seed)
    hyd = hydrology.simulate(terrain, ev, urban_fraction=d.urban_fraction,
                             cropland_fraction=d.cropland_fraction, seed=d.seed)
    t["hydrology"] = time.perf_counter() - mark; mark = time.perf_counter()

    surge = (scenario.SURGE_BY_SEVERITY.get(severity, 2.6)
             if d.flood_driver == districts_data.SURGE else 0.0)
    tl = flood.build_timeline(terrain, hyd, surge_peak_m=surge)
    t["flood"] = time.perf_counter() - mark; mark = time.perf_counter()

    imp = impact.assess(grid, exp, tl)
    t["impact"] = time.perf_counter() - mark; mark = time.perf_counter()

    response.build_response_plan(imp.zones, exp, terrain, grid,
                                 tl.peak_depth, d.name)
    t["response"] = time.perf_counter() - mark; mark = time.perf_counter()

    waterlogging.analyse(grid, terrain, exp, tl, d.hq_lat, d.hq_lon, d.hq_name)
    t["waterlogging"] = time.perf_counter() - mark

    t["TOTAL"] = time.perf_counter() - t0
    return t


STAGES = ["terrain", "exposure", "hydrology", "flood", "impact",
          "response", "waterlogging", "TOTAL"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--district", help="one district code; default is all")
    ap.add_argument("--severity", default="severe")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    targets = ([districts_data.get(args.district)] if args.district
               else districts_data.DISTRICTS)

    print("DRISHTI benchmark — %d district(s), severity=%s, %d run(s) each"
          % (len(targets), args.severity, args.repeat))
    header = "%-9s %-22s " % ("code", "district") + "".join("%10s" % s for s in STAGES)
    print(header)
    print("-" * len(header))

    totals = []
    for d in targets:
        runs = [bench_one(d, args.severity) for _ in range(args.repeat)]
        best = {k: min(r[k] for r in runs) for k in STAGES}
        totals.append(best["TOTAL"])
        print("%-9s %-22s " % (d.code, d.name[:22])
              + "".join("%9.0f " % (best[s] * 1000) for s in STAGES))

    print("-" * len(header))
    print("milliseconds. total: median %.0f ms, worst %.0f ms, all %d districts %.1f s"
          % (statistics.median(totals) * 1000, max(totals) * 1000,
             len(totals), sum(totals)))
    print("\nNo GPU. No GDAL. Single process, single core.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
