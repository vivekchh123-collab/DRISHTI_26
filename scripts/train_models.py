"""Train and evaluate the ML models, then save them.

    python scripts/train_models.py

Evaluation holds out whole districts. That is the only honest test: cells are
spatially autocorrelated, so a random split trains on a cell's own neighbours
and reports an accuracy that means nothing.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core import ml  # noqa: E402
from app.data import districts as districts_data  # noqa: E402

# One district from each terrain archetype is held out, so the test asks whether
# the model generalises to an unseen *kind* of place, not merely an unseen one.
HOLDOUT = ["BR-SIT", "HP-MAN", "OD-BAL", "TN-CHN"]


def show(res: ml.TrainResult) -> None:
    d = res.to_dict()
    print("\n" + "=" * 74)
    print("%s  —  %s" % (d["model"], d["task"]))
    print("=" * 74)
    print("held out: %s" % ", ".join(d["held_out_districts"]))
    print("samples : %s train / %s test    (%.1fs)"
          % (f"{d['samples_train']:,}", f"{d['samples_test']:,}",
             d["train_seconds"]))
    print("\n%-12s %10s %10s %10s" % ("metric", "model", "baseline", "delta"))
    print("-" * 46)
    for k, v in d["metrics"].items():
        b = d["baseline"].get(k)
        delta = d["improvement"].get(k)
        arrow = ""
        if delta is not None:
            arrow = "  better" if delta > 0 else ("  worse" if delta < 0 else "")
        print("%-12s %10.4f %10.4f %+10.4f%s"
              % (k, v, b if b is not None else float("nan"),
                 delta if delta is not None else float("nan"), arrow))
    print("\ntop features:")
    for f in d["feature_importance"][:7]:
        bar = "#" * int(round(f["importance"] * 50))
        print("  %-22s %6.3f  %s" % (f["feature"], f["importance"], bar))
    for n in d["notes"]:
        print("\n  note: %s" % n)


def main() -> int:
    if not ml.available():
        print("scikit-learn is not installed:  pip install scikit-learn")
        return 1

    codes = [d.code for d in districts_data.DISTRICTS]
    print("DRISHTI model training")
    print("%d districts, %d held out" % (len(codes), len(HOLDOUT)))

    print("\n[1/2] water classifier — building scenes and fitting ...")
    wc, wres = ml.WaterClassifier.train(codes, HOLDOUT)
    ml.save(wc, wres)
    show(wres)

    print("\n[2/2] red-zone surrogate — running assessments and fitting ...")
    rz, rres = ml.RedZoneSurrogate.train(codes, HOLDOUT)
    ml.save(rz, rres)
    show(rres)

    print("\n" + "=" * 74)
    print("models written to backend/models/ ; reports served at /api/ml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
