"""Bake the boundaries of every district in India.

    python scripts/fetch_districts.py

Fetches ADM2 boundaries from geoBoundaries (open, CC-BY, no account) and stores
a compact local copy: name, state where derivable, centroid, bounding box and a
simplified ring per district.

This is what lets the national screen address **districts** rather than grid
squares. The problem statement names State Disaster Management Authorities as
the consumer, and an SDMA thinks in districts — a screen that can only offer
27 km tiles is not speaking their language.

**Two tiers, and the distinction must survive into the interface.** The 22
districts with baked elevation get the full physics: HAND, flow routing, the BIS
landslide rating, recurrence across three return periods. The rest get a
*screening* score aggregated from the live weather grid. Screening is useful —
it says where to look — but it is not a hazard assessment, and anything that
displays the two identically would be actively misleading.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

META = "https://www.geoboundaries.org/api/current/gbOpen/IND/ADM2/"
OUT = os.path.join(os.path.dirname(__file__), "..", "backend", "app", "data",
                   "boundaries", "india_districts.json")

# Ring simplification tolerance in degrees. About 2 km, which is invisible at
# national zoom and cuts the payload by roughly an order of magnitude.
TOLERANCE_DEG = 0.02


def fetch(url: str, timeout: float = 60.0):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "drishti/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read())


def rings(geom):
    """Every outer ring of a Polygon or MultiPolygon."""
    t = geom.get("type")
    if t == "Polygon":
        return [geom["coordinates"][0]]
    if t == "MultiPolygon":
        return [p[0] for p in geom["coordinates"]]
    return []


def simplify(ring, tol: float):
    """Ramer-Douglas-Peucker, iterative so a long coastline cannot blow the stack."""
    if len(ring) < 4:
        return ring

    def perp(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        px, py = x1 + t * dx, y1 + t * dy
        return ((x - px) ** 2 + (y - py) ** 2) ** 0.5

    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        worst, idx = 0.0, i
        for k in range(i + 1, j):
            d = perp(ring[k], ring[i], ring[j])
            if d > worst:
                worst, idx = d, k
        if worst > tol:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(ring, keep) if k]


def main() -> int:
    print("geoBoundaries ADM2 — India district boundaries")
    meta = fetch(META)
    if isinstance(meta, list):
        meta = meta[0]
    url = meta.get("simplifiedGeometryGeoJSON") or meta.get("gjDownloadURL")
    print("  source: %s" % meta.get("boundaryName"))

    t0 = time.time()
    gj = fetch(url, timeout=120)
    feats = gj.get("features", [])
    print("  %d districts in %.1fs" % (len(feats), time.time() - t0))

    out, dropped, pts_before, pts_after = [], 0, 0, 0
    for f in feats:
        props = f.get("properties", {})
        name = (props.get("shapeName") or "").strip()
        geom = f.get("geometry") or {}
        rs = rings(geom)
        if not name or not rs:
            dropped += 1
            continue

        main_ring = max(rs, key=len)
        pts_before += len(main_ring)
        simple = simplify(main_ring, TOLERANCE_DEG)
        pts_after += len(simple)

        xs = [c[0] for c in main_ring]
        ys = [c[1] for c in main_ring]
        out.append({
            "id": props.get("shapeID", ""),
            "name": name,
            "centroid": [round(sum(ys) / len(ys), 4), round(sum(xs) / len(xs), 4)],
            "bbox": [round(min(xs), 4), round(min(ys), 4),
                     round(max(xs), 4), round(max(ys), 4)],
            "ring": [[round(c[0], 3), round(c[1], 3)] for c in simple],
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    blob = {
        "source": "geoBoundaries gbOpen ADM2 (CC-BY 4.0)",
        "source_url": "https://www.geoboundaries.org/",
        "baked_at": time.strftime("%Y-%m-%d", time.gmtime()),
        "simplify_tolerance_deg": TOLERANCE_DEG,
        "note": ("Administrative boundaries only. Districts here without baked "
                 "elevation receive a screening score from the live weather "
                 "grid, not a hazard assessment."),
        "districts": out,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(blob, fh, separators=(",", ":"))

    print("  simplified %d -> %d points (%.0f%% smaller)"
          % (pts_before, pts_after, 100 * (1 - pts_after / max(pts_before, 1))))
    print("  %d districts written, %d dropped, %.1f MB"
          % (len(out), dropped, os.path.getsize(OUT) / 1e6))
    print("  -> %s" % os.path.abspath(OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
