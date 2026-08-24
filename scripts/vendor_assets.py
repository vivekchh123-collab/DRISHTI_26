"""Download the front end's third-party assets into ``frontend/vendor``.

Run once, on a machine with network access. After that the whole application
runs with no internet at all, which is the deployment target: a district
emergency operations centre during a flood is exactly when connectivity fails.

    python scripts/vendor_assets.py

Re-running is safe; existing files are skipped unless --force is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR = os.path.join(ROOT, "frontend", "vendor")

ASSETS = [
    ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.js", "leaflet.js"),
    ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css", "leaflet.css"),
    ("https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
     "images/marker-icon.png"),
    ("https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
     "images/marker-icon-2x.png"),
    ("https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
     "images/marker-shadow.png"),
    ("https://unpkg.com/leaflet@1.9.4/dist/images/layers.png", "images/layers.png"),
    ("https://unpkg.com/leaflet@1.9.4/dist/images/layers-2x.png",
     "images/layers-2x.png"),
]


def fetch(url: str, dest: str, force: bool) -> bool:
    path = os.path.join(VENDOR, dest)
    if os.path.exists(path) and not force:
        print("  skip   %s (already present)" % dest)
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "drishti-vendor"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except Exception as exc:
        print("  FAILED %s -> %s" % (dest, exc))
        return False
    with open(path, "wb") as fh:
        fh.write(data)
    print("  ok     %-28s %7.1f KB  sha256:%s"
          % (dest, len(data) / 1024.0, hashlib.sha256(data).hexdigest()[:12]))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="re-download assets that are already present")
    args = ap.parse_args()

    print("vendoring front-end assets into %s" % VENDOR)
    ok = all([fetch(url, dest, args.force) for url, dest in ASSETS])
    if not ok:
        print("\nSome assets could not be downloaded. The application still runs:\n"
              "the front end falls back to its built-in canvas map renderer and\n"
              "reports reduced map interaction. Re-run this script when a network\n"
              "is available.")
        return 1
    print("\nDone. The application now runs fully offline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
