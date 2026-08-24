"""Machine learning, where it earns its place.

Two models, each with a job the physics cannot do as well, and each measured
against the physics baseline it is meant to beat.

**1. Water classifier.** Otsu picks a single global threshold on backscatter. It
cannot use the fact that a dark cell 40 m above its river is almost certainly
tarmac rather than flood, because a histogram has no idea where its pixels are.
A classifier given both radiometry *and* terrain can. The measurable claim is
whether it beats the Otsu-plus-masks baseline on held-out districts.

**2. Red-zone surrogate.** The full assessment costs several seconds per
district: three events, four hazards, depression filling, flow routing. That is
fine for twenty-two districts and impossible for seven hundred and eighty every
half hour. The surrogate learns the physics model's own output from cheap
terrain and climate features so the national screen can run continuously, and
the deep model is spent only where the screen fires. Physics remains the truth;
ML is the fast approximation of it.

**Evaluation is split by district, never at random.** Neighbouring cells are
massively autocorrelated — a random split puts a cell's own neighbours in the
training set and reports an accuracy that means nothing. Holding out whole
districts is the only honest test of whether the model generalises to a place it
has never seen, and it is why the numbers here are lower than a random split
would flatter them into being.

Nothing here sits in the decision path. Relocation horizons and priority
rankings stay physics-derived and auditable, because a family being moved out of
their home is entitled to a reason better than a model's activation.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

MODEL_DIR = os.environ.get("DRISHTI_MODELS") or os.path.join(
    os.path.dirname(__file__), "..", "..", "models")

# Cells are heavily autocorrelated, so a sample every Nth cell loses almost no
# information and keeps training tractable.
SUBSAMPLE = 3


def _sklearn():
    """Import sklearn lazily so the rest of the system runs without it."""
    try:
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        from sklearn.metrics import (f1_score, mean_absolute_error,
                                     precision_score, r2_score, recall_score)
        return {
            "RFC": RandomForestClassifier, "RFR": RandomForestRegressor,
            "f1": f1_score, "precision": precision_score, "recall": recall_score,
            "mae": mean_absolute_error, "r2": r2_score,
        }
    except ImportError:
        return None


def available() -> bool:
    return _sklearn() is not None


# ---------------------------------------------------------------------------
# feature extraction
# ---------------------------------------------------------------------------

WATER_FEATURES = ("vv_db", "vv_local_mean", "vv_local_std",
                  "hand", "slope", "twi", "accum_log")


def water_features(scene_db: np.ndarray, terrain) -> Tuple[np.ndarray, List[str]]:
    """Per-cell features for water detection: radiometry plus terrain context.

    The terrain columns are the point. Backscatter alone cannot separate a
    flooded field from a dry airstrip; height above the nearest drainage can.
    """
    from .terrain import smooth

    local_mean = smooth(scene_db, radius=2)
    local_sq = smooth(scene_db * scene_db, radius=2)
    local_std = np.sqrt(np.maximum(local_sq - local_mean ** 2, 0.0))

    cols = [
        scene_db,
        local_mean,
        local_std,
        np.clip(terrain.hand, 0, 60),
        np.clip(terrain.slope, 0, 45),
        terrain.twi,
        np.log1p(terrain.accum),
    ]
    return np.stack([c.ravel() for c in cols], axis=1), list(WATER_FEATURES)


DISTRICT_FEATURES = (
    "elev_mean", "elev_range", "slope_mean", "slope_p90",
    "relief_mean", "hand_p10", "hand_p50", "twi_mean", "twi_p90",
    "drainage_density_mean", "stream_fraction", "sea_fraction",
    "rainfall_normal_mm", "urban_fraction", "cropland_fraction",
    "population_density", "cell_size_m",
)


def district_features(sc_base, district) -> Tuple[np.ndarray, List[str]]:
    """Cheap district-level descriptors for the national surrogate.

    Every one of these is obtainable for any district in India from a DEM and
    published statistics — no flood model required. That is what makes the
    surrogate applicable beyond the districts we have modelled.
    """
    t = sc_base.terrain
    from .landslide import relative_relief

    relief = relative_relief(t.dem)
    v = [
        float(t.dem.mean()), float(t.dem.max() - t.dem.min()),
        float(t.slope.mean()), float(np.percentile(t.slope, 90)),
        float(relief.mean()),
        float(np.percentile(t.hand, 10)), float(np.percentile(t.hand, 50)),
        float(t.twi.mean()), float(np.percentile(t.twi, 90)),
        float(t.drainage_density.mean()),
        float(t.streams.mean()), float(t.sea_mask.mean()),
        float(district.rainfall_normal_mm), float(district.urban_fraction),
        float(district.cropland_fraction), float(district.density),
        float(sc_base.grid.cell_m),
    ]
    return np.array(v, np.float64), list(DISTRICT_FEATURES)


CELL_FEATURES = (
    "hand", "slope", "twi", "curvature", "accum_log", "drainage_density",
    "relief", "elev_rel", "built_up", "cropland",
    "rainfall_normal_mm", "urban_fraction", "dist_to_stream",
)


def cell_features(sc_base, district) -> Tuple[np.ndarray, List[str]]:
    """Per-cell features for the red-zone surrogate.

    All terrain and land cover, plus two district constants so one model can
    span districts with very different climates.
    """
    from .landslide import relative_relief
    from .relocation import _distance_field

    t, e = sc_base.terrain, sc_base.exposure
    n = sc_base.grid.n
    relief = relative_relief(t.dem)
    dist = _distance_field(t.streams)
    dist = np.where(np.isfinite(dist), dist, 40.0)
    elev_rel = t.dem - float(np.percentile(t.dem, 5))

    cols = [
        np.clip(t.hand, 0, 80), np.clip(t.slope, 0, 45), t.twi,
        np.clip(t.curvature, -50, 50), np.log1p(t.accum), t.drainage_density,
        np.clip(relief, 0, 1500), np.clip(elev_rel, -50, 2000),
        e.built_up, e.cropland,
        np.full((n, n), district.rainfall_normal_mm, np.float32),
        np.full((n, n), district.urban_fraction, np.float32),
        dist.astype(np.float32),
    ]
    return np.stack([c.ravel() for c in cols], axis=1), list(CELL_FEATURES)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

# Metrics where a smaller number is a better model. Without this the report
# prints "worse" next to an MAE that halved.
LOWER_IS_BETTER = {"mae", "rmse", "logloss"}


@dataclass
class TrainResult:
    name: str
    task: str
    metrics: Dict[str, float]
    baseline: Dict[str, float]
    importances: List[Tuple[str, float]]
    n_train: int
    n_test: int
    train_districts: List[str]
    test_districts: List[str]
    seconds: float
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model": self.name, "task": self.task,
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "baseline": {k: round(v, 4) for k, v in self.baseline.items()},
            "improvement": {
                k: round((self.baseline[k] - self.metrics[k])
                         if k in LOWER_IS_BETTER
                         else (self.metrics[k] - self.baseline[k]), 4)
                for k in self.metrics if k in self.baseline},
            "lower_is_better": sorted(
                k for k in self.metrics if k in LOWER_IS_BETTER),
            "feature_importance": [
                {"feature": f, "importance": round(v, 4)}
                for f, v in self.importances],
            "samples_train": self.n_train, "samples_test": self.n_test,
            "held_out_districts": self.test_districts,
            "train_seconds": round(self.seconds, 2),
            "validation": ("Split by district, not at random. Neighbouring "
                           "cells are autocorrelated, so a random split trains "
                           "on a cell's own neighbours and reports an accuracy "
                           "that means nothing."),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# 1. water classifier
# ---------------------------------------------------------------------------

class WaterClassifier:
    """Random forest over radiometry and terrain, versus the Otsu baseline."""

    name = "sar-water-rf"

    def __init__(self, model=None, features: Optional[List[str]] = None):
        self.model = model
        self.features = features or list(WATER_FEATURES)

    def predict(self, scene_db: np.ndarray, terrain) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("model not trained or loaded")
        X, _ = water_features(scene_db, terrain)
        p = self.model.predict_proba(X)[:, 1]
        return p.reshape(scene_db.shape).astype(np.float32)

    # ---- training ----

    @classmethod
    def train(cls, codes: Sequence[str], holdout: Sequence[str],
              severity: str = "severe", seed: int = 0) -> Tuple["WaterClassifier", TrainResult]:
        sk = _sklearn()
        if sk is None:
            raise RuntimeError("scikit-learn is not installed")
        from . import sar as sar_mod
        from . import scenario as scenario_mod
        from .flood import MIN_DEPTH_M

        t0 = time.time()
        train_codes = [c for c in codes if c not in holdout]

        def build(code_list):
            Xs, ys, base = [], [], []
            for code in code_list:
                sc = scenario_mod.get(code, severity)
                truth = sc.timeline.peak_depth >= MIN_DEPTH_M
                pre = sar_mod.simulate_scene(
                    sc.terrain, sc.exposure.built_up, sc.exposure.cropland,
                    np.zeros_like(truth), sc.district.seed, "pre")
                post = sar_mod.simulate_scene(
                    sc.terrain, sc.exposure.built_up, sc.exposure.cropland,
                    truth, sc.district.seed + 1, "post")
                filt = sar_mod.lee_filter(post.vv_db)
                X, _ = water_features(filt, sc.terrain)
                det = sar_mod.detect_water(post, sc.terrain, pre)
                Xs.append(X[::SUBSAMPLE])
                ys.append(truth.ravel()[::SUBSAMPLE])
                base.append(det.flood.ravel()[::SUBSAMPLE])
            return (np.concatenate(Xs), np.concatenate(ys), np.concatenate(base))

        Xtr, ytr, _ = build(train_codes)
        Xte, yte, base_te = build(list(holdout))

        clf = sk["RFC"](n_estimators=140, max_depth=16, min_samples_leaf=4,
                        n_jobs=-1, random_state=seed, class_weight="balanced")
        clf.fit(Xtr, ytr)
        pred = clf.predict(Xte)

        metrics = {
            "precision": float(sk["precision"](yte, pred, zero_division=0)),
            "recall": float(sk["recall"](yte, pred, zero_division=0)),
            "f1": float(sk["f1"](yte, pred, zero_division=0)),
        }
        baseline = {
            "precision": float(sk["precision"](yte, base_te, zero_division=0)),
            "recall": float(sk["recall"](yte, base_te, zero_division=0)),
            "f1": float(sk["f1"](yte, base_te, zero_division=0)),
        }
        imp = sorted(zip(WATER_FEATURES, clf.feature_importances_),
                     key=lambda kv: -kv[1])

        res = TrainResult(
            name=cls.name, task="binary classification: flooded / not flooded",
            metrics=metrics, baseline=baseline,
            importances=[(f, float(v)) for f, v in imp],
            n_train=len(ytr), n_test=len(yte),
            train_districts=list(train_codes), test_districts=list(holdout),
            seconds=time.time() - t0,
            notes=["Baseline is the physics chain: Lee filter, Otsu threshold, "
                   "change detection against a pre-event scene, then the "
                   "permanent-water, slope and HAND masks.",
                   "Labels come from the simulated flood the detector was never "
                   "shown. In live mode these would be Sen1Floods11 or "
                   "Copernicus EMS rapid-mapping labels."])
        return cls(clf, list(WATER_FEATURES)), res


# ---------------------------------------------------------------------------
# 2. red-zone surrogate
# ---------------------------------------------------------------------------

class RedZoneSurrogate:
    """Predicts red-zone unsuitability from cheap terrain features."""

    name = "redzone-surrogate-rf"

    def __init__(self, model=None, features: Optional[List[str]] = None):
        self.model = model
        self.features = features or list(CELL_FEATURES)

    def predict(self, sc_base, district) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("model not trained or loaded")
        X, _ = cell_features(sc_base, district)
        p = self.model.predict(X)
        n = sc_base.grid.n
        return np.clip(p.reshape(n, n), 0, 1).astype(np.float32)

    @classmethod
    def train(cls, codes: Sequence[str], holdout: Sequence[str],
              seed: int = 0) -> Tuple["RedZoneSurrogate", TrainResult]:
        sk = _sklearn()
        if sk is None:
            raise RuntimeError("scikit-learn is not installed")
        from . import scenario as scenario_mod
        from ..data import districts as districts_data

        t0 = time.time()
        train_codes = [c for c in codes if c not in holdout]

        def build(code_list):
            Xs, ys = [], []
            for code in code_list:
                b = scenario_mod.base(code)
                a = scenario_mod.assessment(code)
                d = districts_data.get(code)
                X, _ = cell_features(b, d)
                y = a.redzones.unsuitability.ravel()
                inside = b.exposure.district_mask.ravel()
                Xs.append(X[inside][::SUBSAMPLE])
                ys.append(y[inside][::SUBSAMPLE])
            return np.concatenate(Xs), np.concatenate(ys)

        Xtr, ytr = build(train_codes)
        Xte, yte = build(list(holdout))

        reg = sk["RFR"](n_estimators=150, max_depth=18, min_samples_leaf=5,
                        n_jobs=-1, random_state=seed)
        reg.fit(Xtr, ytr)
        pred = np.clip(reg.predict(Xte), 0, 1)

        # The baseline a surrogate has to beat is predicting the training mean
        # everywhere. Anything that cannot beat that has learned nothing.
        mean_pred = np.full_like(yte, float(ytr.mean()))
        metrics = {"r2": float(sk["r2"](yte, pred)),
                   "mae": float(sk["mae"](yte, pred))}
        baseline = {"r2": float(sk["r2"](yte, mean_pred)),
                    "mae": float(sk["mae"](yte, mean_pred))}
        imp = sorted(zip(CELL_FEATURES, reg.feature_importances_),
                     key=lambda kv: -kv[1])

        res = TrainResult(
            name=cls.name, task="regression: red-zone unsuitability, 0-1",
            metrics=metrics, baseline=baseline,
            importances=[(f, float(v)) for f, v in imp],
            n_train=len(ytr), n_test=len(yte),
            train_districts=list(train_codes), test_districts=list(holdout),
            seconds=time.time() - t0,
            notes=["Target is the physics model's own output. The surrogate "
                   "exists to run nationally at speed, not to replace the "
                   "physics — where it fires, the full model is run.",
                   "MAE is the number to read. It is the average error in an "
                   "index that runs 0 to 1."])
        return cls(reg, list(CELL_FEATURES)), res


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def save(model_obj, result: TrainResult, directory: str = MODEL_DIR) -> str:
    import joblib
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "%s.joblib" % result.name)
    joblib.dump({"model": model_obj.model, "features": model_obj.features}, path)
    with open(os.path.join(directory, "%s.json" % result.name), "w",
              encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2)
    return path


def load(cls, directory: str = MODEL_DIR):
    """Load a trained model, or None if it has not been trained yet."""
    import joblib
    path = os.path.join(directory, "%s.joblib" % cls.name)
    if not os.path.exists(path):
        return None
    try:
        blob = joblib.load(path)
        return cls(blob["model"], blob["features"])
    except Exception:
        return None


def report(directory: str = MODEL_DIR) -> List[dict]:
    """Training reports for every model that has been fitted."""
    out = []
    if not os.path.isdir(directory):
        return out
    for fn in sorted(os.listdir(directory)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(directory, fn), encoding="utf-8") as fh:
                    out.append(json.load(fh))
            except Exception:
                pass
    return out
