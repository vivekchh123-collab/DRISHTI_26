"""Convert the baked geoBoundaries ADM2 rings into the file the boundary
loader already knows how to read, closing the last "approximate-envelope" gap.

    python scripts/build_real_boundaries.py

**Nothing here is new data.** ``scripts/fetch_districts.py`` already baked all
735 district rings from geoBoundaries ADM2 into
``backend/app/data/boundaries/india_districts.json``, which
``core/national_districts.py`` reads directly. But
``core/boundary.py``'s ``load()`` — the function every *per-district*
assessment actually calls — reads a different file
(``data/boundaries/districts.geojson``, GeoJSON, matched on a ``code``
property) and falls back to a synthetic star-shaped envelope whenever it is
absent. It has been absent the whole time, so every district assessment has
been reporting ``boundary_source: "approximate-envelope"`` while the real ring
for that exact district sat baked on disk, three directories over.

This script is the missing adapter: for each of the 22 modelled districts, it
finds the matching geoBoundaries ring by name — reusing the same alias table
``national_districts.py`` already needed for the three names that don't match
verbatim (South 24 Parganas, Balasore, Nellore) — and writes a proper GeoJSON
FeatureCollection with a ``code`` property per feature. ``boundary.load()``
needs no code change to pick it up; it already looks for exactly this file.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.national_districts import NAME_ALIASES, _norm  # noqa: E402
from app.data import districts as districts_data  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "..", "backend", "app", "data",
                   "boundaries", "india_districts.json")
OUT = os.path.join(os.path.dirname(__file__), "..", "backend", "app", "data",
                   "boundaries", "districts.geojson")


def main() -> int:
    with open(SRC, encoding="utf-8") as fh:
        blob = json.load(fh)
    by_name = {}
    for row in blob["districts"]:
        by_name[_norm(row["name"])] = row
    # NAME_ALIASES maps a normalised geoBoundaries-side name to OUR canonical
    # name (e.g. "southtwentyfourparganas" -> "South 24 Parganas"). by_name is
    # keyed the other way, by geoBoundaries' own normalised name - so the fix
    # is to add an entry under OUR normalised name pointing at the row already
    # found under geoBoundaries' name.
    for gb_key, our_name in NAME_ALIASES.items():
        if gb_key in by_name:
            by_name[_norm(our_name)] = by_name[gb_key]

    features, missing = [], []
    for d in districts_data.DISTRICTS:
        row = by_name.get(_norm(d.name))
        if not row:
            missing.append(d.code)
            continue
        features.append({
            "type": "Feature",
            "properties": {"code": d.code, "name": d.name, "state": d.state,
                           "source": blob["source"]},
            "geometry": {"type": "Polygon", "coordinates": [row["ring"]]},
        })
        print("  %-8s %-24s <- %s" % (d.code, d.name, row["name"]))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "source": blob["source"],
                   "source_url": blob["source_url"], "features": features},
                  fh, separators=(",", ":"))

    print("\n%d/%d districts matched, %.0f KB -> %s"
          % (len(features), len(districts_data.DISTRICTS),
             os.path.getsize(OUT) / 1e3, os.path.abspath(OUT)))
    if missing:
        print("unmatched: %s" % ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
