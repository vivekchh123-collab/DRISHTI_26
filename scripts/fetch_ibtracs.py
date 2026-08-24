"""Bake observed cyclone tracks from IBTrACS.

    python scripts/fetch_ibtracs.py

Downloads NOAA's North Indian Ocean best-track archive, keeps the storms that
passed near India, and writes them to
``backend/app/data/tracks/ibtracs_ni.json`` so the application can draw the
history timeline with no network.

Free, no account, no key. This is the only live historical hazard source the
project has — flood and landslide history had to be curated by hand because no
free API carries it for India.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.providers import ibtracs  # noqa: E402


def main() -> int:
    print("IBTrACS v04r01 — North Indian Ocean")
    print("downloading ...")
    t0 = time.time()
    text = ibtracs.fetch_remote()
    if text is None:
        print("could not reach NCEI. Check the network and retry:")
        print("   " + ibtracs.CSV_URL)
        return 1
    print("  %.1f MB in %.1fs" % (len(text) / 1e6, time.time() - t0))

    tracks = ibtracs.parse_csv(text)
    if not tracks:
        print("parsed no tracks — the CSV layout may have changed")
        return 1

    out = os.path.abspath(ibtracs.BAKED)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    blob = {
        "source": "IBTrACS v04r01, NOAA NCEI",
        "source_url": ibtracs.CSV_URL,
        "basin": "North Indian Ocean",
        "baked_at": time.strftime("%Y-%m-%d", time.gmtime()),
        "earliest_season": ibtracs.EARLIEST_SEASON,
        "bbox_filter": list(ibtracs.INDIA_BBOX),
        "note": ("Geometry and intensity only. Death and displacement figures "
                 "are not in IBTrACS and come from the curated register in "
                 "app/data/disasters.py, joined by name and year."),
        "tracks": [t.to_dict() for t in tracks],
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(blob, fh, separators=(",", ":"))

    points = sum(len(t.points) for t in tracks)
    landfall = sum(1 for t in tracks if t.made_landfall)
    named = [t for t in tracks if t.name]
    named.sort(key=lambda t: -t.peak_wind_kt)

    print("\n  %d storms kept, %d track points, %d made landfall"
          % (len(tracks), points, landfall))
    print("  seasons %d-%d" % (tracks[0].season, tracks[-1].season))
    print("  written to %s  (%.1f MB)" % (out, os.path.getsize(out) / 1e6))

    print("\n  strongest named storms in the archive:")
    for t in named[:10]:
        print("    %-14s %d  %3d pts  peak %3.0f kt  %s"
              % (t.name.title(), t.season, len(t.points), t.peak_wind_kt,
                 t.category))
    return 0


if __name__ == "__main__":
    sys.exit(main())
