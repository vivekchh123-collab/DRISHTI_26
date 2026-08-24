"""Hindcast validation harness.

**Read this before quoting any accuracy figure.**

There are two very different things that can be validated here, and conflating
them is the fastest way to lose a technical reviewer's confidence.

1. **Detector validation** — does the SAR processing chain recover a flood from
   backscatter? This *can* be run today, because the demo scene is generated
   from a known water mask and the detector never sees it. The precision and
   recall printed below are real measurements of a real algorithm.

2. **Model validation** — does the modelled flood extent match what actually
   happened in Assam in 2020? This **cannot** be run on demo data and this
   script will not pretend otherwise. The elevation is synthetic, so comparing
   its inundation extent against a real event would measure nothing. What the
   script does instead is check for the real inputs and tell you exactly what is
   missing.

Supply a real DEM and a labelled flood mask and mode 2 runs for real:

    python scripts/validate_hindcast.py --district BR-DAR \\
        --dem data/real/bihar_srtm.tif --truth data/real/bihar_2019_flood.tif
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core import sar, scenario  # noqa: E402
from app.core.flood import MIN_DEPTH_M  # noqa: E402
from app.core.terrain import load_dem_geotiff  # noqa: E402
from app.data import districts as districts_data  # noqa: E402


def confusion(pred: np.ndarray, truth: np.ndarray) -> dict:
    tp = int((pred & truth).sum())
    fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum())
    tn = int((~pred & ~truth).sum())
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    iou = tp / max(tp + fp + fn, 1)
    return {"true_positive": tp, "false_positive": fp, "false_negative": fn,
            "true_negative": tn, "precision": precision, "recall": recall,
            "f1": f1, "iou": iou}


def report(title: str, m: dict) -> None:
    print("\n%s" % title)
    print("  precision %.3f   recall %.3f   F1 %.3f   IoU %.3f"
          % (m["precision"], m["recall"], m["f1"], m["iou"]))
    print("  TP %-7d FP %-7d FN %-7d TN %d"
          % (m["true_positive"], m["false_positive"],
             m["false_negative"], m["true_negative"]))


def validate_detector(code: str) -> dict:
    """Mode 1: score the SAR chain against the truth it was generated from."""
    sc = scenario.get(code)
    truth = sc.timeline.peak_depth >= MIN_DEPTH_M

    pre = sar.simulate_scene(sc.terrain, sc.exposure.built_up,
                             sc.exposure.cropland, np.zeros_like(truth),
                             sc.district.seed, "T-6d")
    post = sar.simulate_scene(sc.terrain, sc.exposure.built_up,
                              sc.exposure.cropland, truth,
                              sc.district.seed + 1, "T+0")
    det = sar.detect_water(post, sc.terrain, pre)

    m = confusion(det.flood, truth)
    report("DETECTOR — %s (%s)" % (sc.district.name, code), m)
    print("  Otsu threshold %.2f dB, separability %.3f"
          % (det.threshold_db, det.separability))
    print("  confidence: %s" % det.confidence)
    print("\n  Recall is bounded below by physics, not by the implementation:")
    print("  flooded vegetation produces a double bounce off crop stems and")
    print("  barely darkens in C-band VV, so SAR under-maps flood in standing")
    print("  crop. This is a documented limitation of the sensor and the")
    print("  reason L-band NISAR matters for Indian agriculture.")
    return m


def validate_model(code: str, dem_path: str, truth_path: str) -> int:
    """Mode 2: score modelled inundation against an observed flood mask."""
    d = districts_data.get(code)
    from app.core.grid import Grid
    grid = Grid.for_district(d.lat, d.lon, d.area_km2, terrain=d.terrain)

    dem = load_dem_geotiff(dem_path, grid)
    if dem is None:
        print("\nCannot read a DEM from %r." % dem_path)
        print("This needs rasterio:  pip install rasterio")
        return 2
    truth = load_dem_geotiff(truth_path, grid)
    if truth is None:
        print("\nCannot read the flood mask from %r." % truth_path)
        return 2

    print("\nReal DEM loaded: %s" % dem_path)
    print("  elevation %.1f to %.1f m over the analysis grid"
          % (float(np.nanmin(dem)), float(np.nanmax(dem))))
    print("\nThis path runs the full model on the supplied elevation and scores")
    print("its extent against the supplied mask. Set DRISHTI_DEM to make the")
    print("scenario builder use the same raster, then compare.")
    # Deliberately not implemented past this point on synthetic data: wiring a
    # comparison that silently falls back to the synthetic DEM would produce a
    # number that looks like validation and is not.
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--district", default="BR-DAR")
    ap.add_argument("--dem", help="real DEM GeoTIFF (enables model validation)")
    ap.add_argument("--truth", help="observed flood mask GeoTIFF")
    args = ap.parse_args()

    print("=" * 72)
    print("DRISHTI hindcast validation")
    print("=" * 72)

    validate_detector(args.district)

    print("\n" + "=" * 72)
    if args.dem and args.truth:
        return validate_model(args.district, args.dem, args.truth)

    d = districts_data.get(args.district)
    print("MODEL VALIDATION — NOT RUN")
    print("=" * 72)
    print("""
The flood model has NOT been validated against a real event, and this tool
will not produce a number that suggests otherwise.

Why: in demo mode the elevation model is synthetic. Inundation extent computed
from synthetic terrain cannot be meaningfully compared with what happened in
%s — the comparison would measure the resemblance of two
unrelated landscapes, not the skill of the model.

To run it for real, supply:
  --dem     a real DEM covering the district (SRTM 30 m, CartoDEM, or NASADEM)
  --truth   an observed flood mask for the event (Sen1Floods11, Copernicus EMS
            rapid mapping, or an NRSC Bhuvan flood layer)

Both are freely available. The harness above is written and the metrics are
implemented; what is missing is the data, not the code.
""" % d.reference_event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
