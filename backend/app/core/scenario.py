"""Scenario orchestration: one district, one event, one cached assessment.

This is the only module that knows about all the others. Everything below it is
independently testable, and everything above it (the API, the frontend) sees
only the assembled result.

Assessments are cached by (district, severity, hours). A full district takes a
couple of seconds to build and is entirely deterministic for a given seed, so
recomputing it on every map pan would be pure waste — and determinism is what
makes the cache safe.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..data import districts as districts_data
from ..data import vulnerability as vulnerability_data
from ..data.districts import District
from . import boundary as boundary_mod
from . import cloudburst as cloudburst_mod
from . import erosion as erosion_mod
from . import exposure as exposure_mod
from . import flood as flood_mod
from . import hydrology as hydrology_mod
from . import impact as impact_mod
from . import landslide as landslide_mod
from . import rainfall as rainfall_mod
from . import redzone as redzone_mod
from . import relocation as relocation_mod
from . import response as response_mod
from . import waterlogging as waterlogging_mod
from .grid import Grid
from .terrain import Terrain, build_terrain

# Storm-surge peak by severity, for the coastal districts. Indicative of Fani,
# Amphan and Yaas, which produced surges in the 1.5-4 m band along this coast.
SURGE_BY_SEVERITY = {"moderate": 1.4, "severe": 2.6, "extreme": 4.0}

DEFAULT_HOURS = 96

# The severities the red-zone assessment is built from, shortest return period
# first. Recurrence is the whole point: a hazard that recurs every five years
# means something categorically different from one that recurs every hundred.
RECURRENCE_SEVERITIES = ("moderate", "severe", "extreme")

# Districts whose coast is deltaic, and therefore subsiding as well as drowning.
DELTAIC = {"WB-S24", "OD-KEN", "OD-PUR", "AP-NEL"}


@dataclass
class DistrictBase:
    """Everything about a district that does not depend on the event.

    Terrain, boundary and exposure are the expensive parts and they are
    identical at every severity, so building them once and reusing them turns a
    three-severity recurrence assessment from three full builds into one build
    plus three cheap event runs.
    """
    district: District
    grid: Grid
    terrain: Terrain
    boundary: boundary_mod.Boundary
    exposure: exposure_mod.Exposure


_BASE_CACHE: Dict[str, DistrictBase] = {}


def base(code: str) -> DistrictBase:
    """Cached event-independent layers for a district."""
    with _LOCK:
        hit = _BASE_CACHE.get(code)
    if hit is not None:
        return hit

    d = districts_data.get(code)
    grid = Grid.for_district(d.lat, d.lon, d.area_km2, terrain=d.terrain)
    terrain = build_terrain(grid, d.terrain, d.code, d.seed)
    bnd = boundary_mod.load(grid, d.code, d.lat, d.lon, d.area_km2, d.seed,
                            must_include=(d.hq_lat, d.hq_lon))
    exp = exposure_mod.build_exposure(
        grid, terrain, bnd,
        population_total=d.population_2025,
        urban_fraction=d.urban_fraction,
        cropland_fraction=d.cropland_fraction,
        hq_lat=d.hq_lat, hq_lon=d.hq_lon, hq_name=d.hq_name,
        district_code=d.code)

    built = DistrictBase(district=d, grid=grid, terrain=terrain,
                         boundary=bnd, exposure=exp)
    with _LOCK:
        _BASE_CACHE[code] = built
    return built


@dataclass
class Scenario:
    """Everything computed for one district under one event."""
    district: District
    grid: Grid
    terrain: Terrain
    boundary: boundary_mod.Boundary
    exposure: exposure_mod.Exposure
    event: rainfall_mod.RainfallEvent
    hydrograph: hydrology_mod.Hydrograph
    timeline: flood_mod.FloodTimeline
    impact: impact_mod.DistrictImpact
    action_cards: List[response_mod.ActionCard]
    waterlogging: waterlogging_mod.Waterlogging
    severity: str
    build_seconds: float
    # Live provenance, populated only when the event came from a real feed.
    live: bool = False
    live_source: str = ""

    # ---------- provenance ----------

    @property
    def provenance(self) -> dict:
        """Where every layer came from. Rendered as a badge on every screen.

        Nothing here is a single blanket statement any more. Elevation,
        boundaries, population, built-up land, cropland, roads and facilities
        each carry their own source for *this specific district*, because a
        district with GHS-POP baked but no OSM bake is genuinely real on one
        layer and modelled on another - collapsing that into one sentence would
        misstate whichever one happens to be true here.
        """
        exp_prov = dict(self.exposure.layer_provenance or {})
        # A layer with no entry means build_exposure() ran without a
        # district_code, which should not happen via the normal API path, but
        # the fallback keeps a caller from crashing on a missing key rather
        # than silently mislabelling the layer as real.
        for layer in ("population", "built_up", "cropland", "roads", "facilities"):
            exp_prov.setdefault(layer, "modelled (no district-specific bake)")

        modelled_layers = [k for k, v in exp_prov.items()
                          if "observed" not in v and "OpenStreetMap" not in v]
        real_layers = [k for k in exp_prov if k not in modelled_layers]

        base = {
            "dem_source": self.terrain.dem_source,
            "boundary_source": self.boundary.source,
            "population_source": exp_prov["population"],
            "built_up_source": exp_prov["built_up"],
            "cropland_source": exp_prov["cropland"],
            "roads_source": exp_prov["roads"],
            "facilities_source": exp_prov["facilities"],
            "population_reconciliation": self.exposure.population_reconciliation,
            "still_modelled": modelled_layers,
            "generated_at": self.generated_at,
        }
        note = ("Real, observed layers for this district: %s. Modelled, and "
                "labelled as such: %s."
                % (", ".join(real_layers) or "none",
                   ", ".join(modelled_layers) or "none"))

        if self.live:
            return {
                "data_mode": "live",
                "confidence": "observed-and-forecast rainfall",
                "rainfall_source": self.live_source,
                "note": note + (" Rainfall and antecedent soil wetness are also "
                                "real, observed and forecast, on top of the "
                                "district layers above."),
                **base,
            }
        return {
            "data_mode": "demo",
            "confidence": "modelled rainfall, real exposure where baked",
            "rainfall_source": "synthetic design storm (IMD normals + Gumbel DDF)",
            "note": note + (" Rainfall here is a synthetic design storm; add "
                            "?live=true for real observed and forecast rainfall "
                            "on top of the same district layers."),
            **base,
        }

    generated_at: str = ""

    # ---------- summaries ----------

    def summary(self) -> dict:
        d = self.district
        return {
            "district": {
                "code": d.code, "name": d.name, "state": d.state,
                "area_km2": d.area_km2,
                "population_2025_estimate": d.population_2025,
                "terrain": d.terrain, "flood_driver": d.flood_driver,
                "drainage": d.drainage,
                "rainfall_normal_mm": d.rainfall_normal_mm,
                "reference_event": d.reference_event,
                "centroid": {"lat": d.lat, "lon": d.lon},
                "headquarters": {"name": d.hq_name, "lat": d.hq_lat, "lon": d.hq_lon},
            },
            "grid": {
                "cells": self.grid.n,
                "cell_size_m": round(self.grid.cell_m, 1),
                "bounds": self.grid.leaflet_bounds,
            },
            "event": self.event.summary(),
            "flood": self.timeline.summary(),
            "impact": {k: v for k, v in self.impact.to_dict().items() if k != "zones"},
            "terrain": self.terrain.meta,
            "provenance": self.provenance,
            "build_seconds": round(self.build_seconds, 2),
        }

    def timeline_series(self) -> dict:
        """Per-hour series for the time slider."""
        tl, cell = self.timeline, self.grid.cell_area_km2
        pop = self.exposure.population
        affected = []
        for t in range(tl.hours):
            wet = tl.depth[t] >= flood_mod.MIN_DEPTH_M
            affected.append(int(round(float(pop[wet].sum()))))
        return {
            "hours": tl.hours,
            "peak_hour": tl.peak_hour,
            "area_km2": [round(float(a), 2) for a in tl.area_km2],
            "population_affected": affected,
            "rainfall_mm": [round(float(x), 2) for x in self.event.depth_mm],
            "max_depth_m": [round(float(tl.depth[t].max()), 2) for t in range(tl.hours)],
        }


# ---------------------------------------------------------------------------
# build + cache
# ---------------------------------------------------------------------------

_CACHE: Dict[Tuple[str, str, int], Scenario] = {}
_LOCK = threading.Lock()


def build(code: str, severity: str = "severe",
          hours: int = DEFAULT_HOURS) -> Scenario:
    """Compute a full district assessment. Deterministic for a given district."""
    t0 = time.time()
    b = base(code)
    d, grid, terrain, bnd, exp = (b.district, b.grid, b.terrain,
                                  b.boundary, b.exposure)

    event = rainfall_mod.build_event(
        grid, terrain, annual_normal_mm=d.rainfall_normal_mm,
        flood_driver=d.flood_driver, severity=severity, hours=hours, seed=d.seed)
    hyd = hydrology_mod.simulate(
        terrain, event, urban_fraction=d.urban_fraction,
        cropland_fraction=d.cropland_fraction, seed=d.seed)

    surge = (SURGE_BY_SEVERITY.get(severity, 2.6)
             if d.flood_driver == districts_data.SURGE else 0.0)
    timeline = flood_mod.build_timeline(terrain, hyd, surge_peak_m=surge)

    imp = impact_mod.assess(grid, exp, timeline)
    cards = response_mod.build_response_plan(
        imp.zones, exp, terrain, grid, timeline.peak_depth, d.name)
    wlog = waterlogging_mod.analyse(grid, terrain, exp, timeline,
                                    d.hq_lat, d.hq_lon, d.hq_name)

    return Scenario(
        district=d, grid=grid, terrain=terrain, boundary=bnd, exposure=exp,
        event=event, hydrograph=hyd, timeline=timeline, impact=imp,
        action_cards=cards, waterlogging=wlog, severity=severity,
        build_seconds=time.time() - t0,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def build_live(code: str, hours: int = DEFAULT_HOURS) -> Optional[Scenario]:
    """A full district assessment driven by real observed and forecast rainfall.

    Identical physics to :func:`build`. The only thing that changes is where the
    rainfall and the antecedent wetness come from, which is the point of having
    put the seam at the rainfall event rather than deeper in.

    Returns None if the live feed is unreachable, so the caller can fall back to
    a design storm and say so rather than silently serving stale numbers.
    """
    from ..providers.openmeteo import OpenMeteoProvider

    t0 = time.time()
    b = base(code)
    d = b.district
    provider = OpenMeteoProvider()

    clim = provider.climatology(d.lat, d.lon)
    event = rainfall_mod.build_live_event(b.grid, b.terrain, provider,
                                          hours=hours, climatology=clim)
    if event is None:
        return None

    hyd = hydrology_mod.simulate(
        b.terrain, event, urban_fraction=d.urban_fraction,
        cropland_fraction=d.cropland_fraction, seed=d.seed)

    # No surge without a cyclone forecast. Inventing one on a quiet day would
    # be the single most misleading thing this mode could do.
    timeline = flood_mod.build_timeline(b.terrain, hyd, surge_peak_m=0.0)

    imp = impact_mod.assess(b.grid, b.exposure, timeline)
    cards = response_mod.build_response_plan(
        imp.zones, b.exposure, b.terrain, b.grid, timeline.peak_depth, d.name)
    wlog = waterlogging_mod.analyse(b.grid, b.terrain, b.exposure, timeline,
                                    d.hq_lat, d.hq_lon, d.hq_name)

    return Scenario(
        district=d, grid=b.grid, terrain=b.terrain, boundary=b.boundary,
        exposure=b.exposure, event=event, hydrograph=hyd, timeline=timeline,
        impact=imp, action_cards=cards, waterlogging=wlog,
        severity="observed", build_seconds=time.time() - t0,
        live=True,
        live_source=("Open-Meteo observed + forecast rainfall, %d sample points "
                     "across the district; antecedent wetness from modelled "
                     "soil moisture" % event.sample_points),
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


_LIVE_CACHE: Dict[Tuple[str, int], Tuple[float, Scenario]] = {}
LIVE_TTL = 900.0        # 15 minutes; the forecast behind it updates hourly


def get_live(code: str, hours: int = DEFAULT_HOURS) -> Optional[Scenario]:
    """Cached :func:`build_live`, with a short TTL because it is live."""
    key = (code, hours)
    with _LOCK:
        hit = _LIVE_CACHE.get(key)
    if hit and (time.time() - hit[0]) < LIVE_TTL:
        return hit[1]
    built = build_live(code, hours)
    if built is not None:
        with _LOCK:
            _LIVE_CACHE[key] = (time.time(), built)
    return built


def get(code: str, severity: str = "severe",
        hours: int = DEFAULT_HOURS) -> Scenario:
    """Cached :func:`build`. Safe because the computation is deterministic."""
    key = (code, severity, hours)
    with _LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return hit
    built = build(code, severity, hours)
    with _LOCK:
        _CACHE[key] = built
    return built


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
        _BASE_CACHE.clear()
        _REDZONE_CACHE.clear()
        _LIVE_CACHE.clear()


# ---------------------------------------------------------------------------
# multi-hazard red-zone assessment  (SIH26191)
# ---------------------------------------------------------------------------

@dataclass
class DistrictAssessment:
    """The standing multi-hazard picture: what land is unfit to live on.

    Distinct from :class:`Scenario`, which describes one event. This describes
    the *place*, across events, which is what a relocation decision needs.
    """
    district: District
    grid: Grid
    terrain: Terrain
    boundary: boundary_mod.Boundary
    exposure: exposure_mod.Exposure
    redzones: redzone_mod.RedZones
    habitations: List[redzone_mod.Habitation]
    relocation: relocation_mod.RelocationAssessment
    landslide: landslide_mod.LandslideHazard
    cloudburst: cloudburst_mod.CloudburstHazard
    erosion: erosion_mod.CoastalErosion
    waterlogging: waterlogging_mod.Waterlogging
    build_seconds: float
    # Social vulnerability for the district. The problem statement names
    # "population vulnerability" alongside hazard intensity, and without this
    # the ranking treats every population of equal size as equally able to cope.
    vulnerability: Optional[dict] = None
    generated_at: str = ""

    @property
    def provenance(self) -> dict:
        exp_prov = dict(self.exposure.layer_provenance or {})
        for layer in ("population", "built_up", "cropland", "roads", "facilities"):
            exp_prov.setdefault(layer, "modelled (no district-specific bake)")
        modelled_layers = [k for k, v in exp_prov.items()
                          if "observed" not in v and "OpenStreetMap" not in v]
        real_layers = [k for k in exp_prov if k not in modelled_layers]

        return {
            "data_mode": "demo",
            "confidence": "modelled rainfall (recurrence, not one event); "
                          "real exposure where baked",
            "dem_source": self.terrain.dem_source,
            "boundary_source": self.boundary.source,
            "geology_source": ("not supplied — lithology and structure held at a "
                               "neutral LHEF rating"
                               if not self.landslide.complete else "supplied"),
            "population_source": exp_prov["population"],
            "built_up_source": exp_prov["built_up"],
            "cropland_source": exp_prov["cropland"],
            "roads_source": exp_prov["roads"],
            "facilities_source": exp_prov["facilities"],
            "population_reconciliation": self.exposure.population_reconciliation,
            "note": ("Algorithms are published and cited. Red zones are a "
                     "recurrence assessment across modelled 5-, 25- and "
                     "100-year rainfall events, on exposure layers that are "
                     "real where baked: %s. Modelled, and labelled: %s."
                     % (", ".join(real_layers) or "none",
                        ", ".join(modelled_layers) or "none")),
            "generated_at": self.generated_at,
        }

    def summary(self) -> dict:
        d = self.district
        cell = self.grid.cell_area_km2
        mask = self.exposure.district_mask
        immediate = [h for h in self.habitations if h.horizon == "immediate"]
        short = [h for h in self.habitations if h.horizon == "short-term"]
        medium = [h for h in self.habitations if h.horizon == "medium-term"]
        return {
            "district": {
                "code": d.code, "name": d.name, "state": d.state,
                "area_km2": d.area_km2,
                "population_2025_estimate": d.population_2025,
                "terrain": d.terrain,
                "hazards_documented": list(d.hazards),
                "reference_event": d.reference_event,
                "centroid": {"lat": d.lat, "lon": d.lon},
                "headquarters": {"name": d.hq_name,
                                 "lat": d.hq_lat, "lon": d.hq_lon},
            },
            "grid": {"cells": self.grid.n,
                     "cell_size_m": round(self.grid.cell_m, 1),
                     "bounds": self.grid.leaflet_bounds},
            "red_zones": self.redzones.summary(cell, mask),
            "landslide": self.landslide.summary(),
            "cloudburst": self.cloudburst.summary(cell),
            "vulnerability": self.vulnerability,
            "erosion": self.erosion.summary(cell, self.grid.cell_m),
            "relocation": {
                **self.relocation.summary(cell),
                "habitations_assessed": len(self.habitations),
                "immediate": len(immediate),
                "short_term": len(short),
                "medium_term": len(medium),
                "population_in_red_zone": sum(
                    h.population_in_red for h in self.habitations),
                "population_needing_immediate_relocation": sum(
                    h.population_in_red for h in immediate),
            },
            "provenance": self.provenance,
            "build_seconds": round(self.build_seconds, 2),
        }


_REDZONE_CACHE: Dict[str, DistrictAssessment] = {}


def build_assessment(code: str) -> DistrictAssessment:
    """Run every severity, combine into red zones, rank habitations."""
    t0 = time.time()
    b = base(code)
    d = b.district

    # Coastal erosion is a standing process, not an event outcome.
    ero = erosion_mod.assess(b.grid, b.terrain, d.code,
                             deltaic=d.code in DELTAIC)

    layers: List[redzone_mod.HazardLayers] = []
    last_wlog = None
    last_slide = None
    last_burst = None
    for sev in RECURRENCE_SEVERITIES:
        sc = get(code, sev)
        slide = landslide_mod.assess(
            b.grid, b.terrain, b.exposure,
            rainfall_mm=sc.event.depth_mm,
            antecedent_mm=sc.event.antecedent_mm)
        last_slide, last_wlog = slide, sc.waterlogging
        # A cloudburst is its own event, not the peak hour of the monsoon storm
        # above. Reading the design storm's maximum hour gives about 10 mm/hr
        # and therefore no cloudburst hazard anywhere, which is wrong: the
        # question is what happens *when* a cloudburst occurs over this terrain,
        # on the cadence at which they occur.
        burst = cloudburst_mod.assess(
            b.grid, b.terrain,
            rainfall_mm_hr=cloudburst_mod.DESIGN_CLOUDBURST_MM_HR[sev],
            cape_j_kg=getattr(sc.event, "cape_j_kg", None))
        last_burst = burst

        layers.append(redzone_mod.hazard_layers(
            rainfall_mod.SEVERITY[sev], sev,
            terrain=b.terrain,
            timeline=sc.timeline,
            landslide_affected=slide.affected,
            landslide_susceptibility=slide.susceptibility,
            waterlog_hours=sc.waterlogging.drain_down_hours,
            cloudburst_affected=burst.affected,
            cloudburst_susceptibility=burst.susceptibility,
            # Erosion does not vary with the storm; attach it to the most
            # frequent event so its return period is reported as ongoing.
            erosion_rate=(ero.retreat_m_per_year
                          if sev == RECURRENCE_SEVERITIES[0] else None)))

    red = redzone_mod.build(layers)
    vuln = vulnerability_data.index_for(d.state, d.urban_fraction, d.code)
    habs = redzone_mod.assess_habitations(b.grid, b.exposure, red,
                                          vulnerability=vuln)
    reloc = relocation_mod.assess(b.grid, b.terrain, b.exposure, red, habs,
                                  d.urban_fraction, d.hq_lat, d.hq_lon, d.hq_name)

    return DistrictAssessment(
        district=d, grid=b.grid, terrain=b.terrain, boundary=b.boundary,
        exposure=b.exposure, redzones=red, habitations=habs, relocation=reloc,
        landslide=last_slide, cloudburst=last_burst, erosion=ero, waterlogging=last_wlog,
        vulnerability=vuln,
        build_seconds=time.time() - t0,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def assessment(code: str) -> DistrictAssessment:
    """Cached :func:`build_assessment`."""
    with _LOCK:
        hit = _REDZONE_CACHE.get(code)
    if hit is not None:
        return hit
    built = build_assessment(code)
    with _LOCK:
        _REDZONE_CACHE[code] = built
    return built


def national_overview(severity: str = "severe") -> List[dict]:
    """A compact risk row per district, for the opening national screen.

    Deliberately cheap: it reads whatever is already cached and otherwise
    reports the district's static exposure, so opening the app does not trigger
    twenty-two full model runs.
    """
    out: List[dict] = []
    for d in districts_data.DISTRICTS:
        key = (d.code, severity, DEFAULT_HOURS)
        with _LOCK:
            sc = _CACHE.get(key)
        row = {
            "code": d.code, "name": d.name, "state": d.state,
            "lat": d.lat, "lon": d.lon,
            "area_km2": d.area_km2,
            "population_2025_estimate": d.population_2025,
            "flood_driver": d.flood_driver,
            "terrain": d.terrain,
            "hazards": list(d.hazards),
            "reference_event": d.reference_event,
            "peak_months": list(d.peak_months),
            "modelled": sc is not None,
        }
        if sc is not None:
            imp = sc.impact
            row.update({
                "population_affected": imp.population_affected,
                "population_affected_pct": round(
                    100.0 * imp.population_affected / max(imp.population_total, 1), 1),
                "area_flooded_km2": round(imp.area_flooded_km2, 1),
                "settlements_cut_off": imp.settlements_cut_off,
                "risk_score": round(min(100.0, 100.0 * imp.population_affected
                                        / max(imp.population_total, 1)), 1),
            })
        else:
            # Static standing exposure: density against the district's own
            # documented flood mechanism. Labelled `modelled: false` so the UI
            # can show it as an estimate rather than a model result.
            base = {"riverine": 62, "surge": 58, "pluvial": 54, "flash": 46}
            row["risk_score"] = round(min(100.0, base.get(d.flood_driver, 50)
                                          * (1 + min(d.density / 3000.0, 0.6))), 1)
        out.append(row)
    return out
