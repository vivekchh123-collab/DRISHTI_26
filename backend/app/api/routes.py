"""HTTP surface.

Thin by design: every endpoint resolves a cached :class:`Scenario` and shapes it
for the wire. No modelling happens here.

Raster layers are served as PNG rather than as GeoJSON because a 160x160 field
is 25,600 values — as polygons that is megabytes of JSON per layer and a
janky map, while as a PNG image overlay it is tens of kilobytes and the browser
composites it on the GPU. Vector output is reserved for things the user clicks:
boundaries, zones and facilities.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Response

from ..core import flood as flood_mod
from ..core import deforestation as deforestation_mod
from ..core import ml as ml_mod
from ..core import national as national_mod
from ..core import national_districts as national_districts_mod
from ..core import watch as watch_mod
from ..core import redzone as redzone_mod
from ..core import render, scenario
from ..core.impact import ROAD_IMPASSABLE_M, WEIGHTS as SCORE_WEIGHTS
from ..data import districts as districts_data
from ..data import methods as methods_data
from ..core import encroachment as encroachment_mod
from ..core import history as history_mod
from ..core import mitigation as mitigation_mod
from ..core import state_view as state_view_mod
from ..providers import CopernicusProvider, resolve, status_report
from ..providers import openmeteo as openmeteo_mod

router = APIRouter()

SEVERITIES = ("moderate", "severe", "extreme")

# name -> (title, ramp, unit, whether it needs a live event)
# Layers sourced from the standing multi-hazard assessment rather than from one
# event. These do not take a severity: a red zone is a property of the place.
ASSESSMENT_LAYERS = {
    "redzone":       ("Red zones — unsuitable for habitation", "risk", "index"),
    "recurrence":    ("Hazard recurrence interval", "hand", "years"),
    "landslide":     ("Landslide hazard (BIS LHEF)", "risk", "TEHD"),
    "erosion":       ("Shoreline retreat", "risk", "m/yr"),
    "suitability":   ("Relocation site suitability", "wetness", "index"),
}

LAYERS = {
    "basemap": ("Terrain relief", None, "", False),
    "depth": ("Flood depth", "depth", "m", True),
    "hand": ("Height above nearest drainage", "hand", "m", False),
    "wetness": ("Waterlogging susceptibility", "wetness", "index", False),
    "population": ("Population density", "risk", "people/cell", False),
    "duration": ("Inundation duration", "depth", "hours", True),
}


def _severity(value: str) -> str:
    if value not in SEVERITIES:
        raise HTTPException(400, "severity must be one of %s" % (SEVERITIES,))
    return value


def _district_or_404(code: str):
    try:
        return districts_data.get(code)
    except KeyError:
        raise HTTPException(404, "unknown district %r" % code)


def _scenario(code: str, severity: str) -> scenario.Scenario:
    try:
        districts_data.get(code)
    except KeyError:
        raise HTTPException(404, "unknown district %r" % code)
    return scenario.get(code, _severity(severity))


def _scenario_maybe_live(code: str, severity: str, live: bool
                         ) -> Tuple[scenario.Scenario, bool]:
    """The demo scenario, or the real one, on the same footing every route uses.

    Every endpoint the district screen calls - detail, assessment, timeline,
    the layer index, the layer images - has to agree on which scenario it is
    describing. Before this existed, only ``/assessment`` understood
    ``live=true``: the map and the time slider stayed on the synthetic event
    while the ranked-zone list claimed to be live, which is a worse failure
    than either being wrong on its own, because it looks like two different
    answers to the same question. Every caller now shares this one fallback
    rule instead of five copies of it.

    Returns ``(scenario, fell_back)`` - ``fell_back`` is True when live was
    requested but the feed could not be reached, so the caller can report that
    honestly rather than silently serving the design storm labelled live.
    """
    if live:
        sc = scenario.get_live(code)
        if sc is not None:
            return sc, False
        return _scenario(code, severity), True
    return _scenario(code, severity), False


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------

@router.get("/healthz", tags=["meta"])
def healthz() -> dict:
    return {"status": "ok", "districts": len(districts_data.DISTRICTS)}


@router.get("/api/methods", tags=["meta"])
def methods() -> dict:
    """Every algorithm, its source, and what it cannot do."""
    return {"methods": methods_data.table(),
            "providers": status_report(),
            "active_provider": resolve().name,
            "note": ("Input rasters are modelled in demo mode; the algorithms "
                     "listed here are the published methods and are the same in "
                     "either mode.")}


# ---------------------------------------------------------------------------
# districts
# ---------------------------------------------------------------------------

@router.get("/api/districts", tags=["districts"])
def list_districts(severity: str = Query("severe")) -> dict:
    """All modelled districts, with a risk row each, for the national screen."""
    return {"severity": _severity(severity),
            "districts": scenario.national_overview(severity),
            "states": districts_data.states()}


@router.get("/api/districts/{code}", tags=["districts"])
def district_detail(code: str, severity: str = Query("severe"),
                    live: bool = Query(False)) -> dict:
    sc, fell_back = _scenario_maybe_live(code, severity, live)
    body = {
        **sc.summary(),
        "boundary": sc.boundary.geojson({"code": sc.district.code,
                                         "name": sc.district.name}),
        "settlements": [
            {"id": s.id, "label": s.label, "lat": s.lat, "lon": s.lon,
             "population": s.population, "is_hq": s.is_hq}
            for s in sc.exposure.settlements],
        "facilities": [
            {"id": f.id, "kind": f.kind, "name": f.name, "lat": f.lat,
             "lon": f.lon, "capacity": f.capacity}
            for f in sc.exposure.facilities],
    }
    if fell_back:
        body["live_fallback"] = {"requested": "live", "served": "design storm"}
    return body


@router.get("/api/districts/{code}/assessment", tags=["districts"])
def assessment(code: str, severity: str = Query("severe"),
               live: bool = Query(False)) -> dict:
    """The main call: flood state, ranked zones and the response plan.

    ``live=true`` runs the identical physics on real observed and forecast
    rainfall instead of a design storm. If the feed is unreachable it falls back
    to the design storm and says so in ``provenance`` rather than silently
    serving a synthetic result labelled live.
    """
    sc, fell_back = _scenario_maybe_live(code, severity, live)
    body = {
        "summary": sc.summary(),
        "impact": sc.impact.to_dict(),
        "action_cards": [c.to_dict() for c in sc.action_cards],
        "score_weights": {
            "note": ("Composite priority weights, published so the ranking can "
                     "be checked rather than trusted."),
            "weights": SCORE_WEIGHTS,
            "road_impassable_m": ROAD_IMPASSABLE_M,
        },
    }
    if fell_back:
        body["live_fallback"] = {
            "requested": "live",
            "served": "design storm",
            "reason": openmeteo_mod.OpenMeteoProvider().status().reason,
            "rate_limit": openmeteo_mod.rate_limit_status(),
        }
    return body


@router.get("/api/districts/{code}/timeline", tags=["districts"])
def timeline(code: str, severity: str = Query("severe"),
            live: bool = Query(False)) -> dict:
    """Per-hour series driving the time slider."""
    sc, _ = _scenario_maybe_live(code, severity, live)
    return sc.timeline_series()


@router.get("/api/districts/{code}/deforestation", tags=["red zones"])
def deforestation(code: str,
                  clearance: float = Query(0.30, ge=0.0, le=1.0),
                  years_since: float = Query(8.0, ge=0.0, le=50.0)) -> dict:
    """What the forest is worth, as a counterfactual.

    Runs the hazard physics with the forest and again without it, and reports
    the difference. Turns "deforestation is bad" into a number a district
    planning committee can weigh against whatever the clearance was for.
    """
    try:
        d = districts_data.get(code)
    except KeyError:
        raise HTTPException(404, "unknown district %r" % code)
    b = scenario.base(code)
    real_canopy = deforestation_mod.load_worldcover_canopy(code, b.grid)
    state = deforestation_mod.build(b.terrain, b.exposure,
                                    canopy_raster=real_canopy)
    imp = deforestation_mod.impact(
        b.grid, b.terrain, b.exposure, d.urban_fraction, d.cropland_fraction,
        loss_fraction=clearance, years_since_loss=years_since)
    return {
        "district": code,
        "forest": state.summary(b.grid.cell_area_km2),
        "scenario": imp.to_dict(),
        "mechanisms": {
            "landslide": ("Root reinforcement is lost as roots decay, which "
                          "worsens the BIS IS 14496 land-use rating on slopes "
                          "above 12 degrees. Sidle & Ochiai (2006)."),
            "flood": ("Canopy and litter loss raises the SCS curve number, so "
                      "more of the same rain becomes runoff and it arrives "
                      "faster. Bosch & Hewlett (1982); USDA-NRCS NEH-630."),
        },
    }


# ---------------------------------------------------------------------------
# national live screen
# ---------------------------------------------------------------------------

@router.get("/api/national", tags=["national"])
def national(size_deg: float = Query(0.75, ge=0.25, le=2.0),
             live: bool = Query(True)) -> dict:
    """India tessellated into squares and scored on live weather.

    Cheap by design. The full physics costs seconds per district and cannot run
    continuously for the whole country; this runs every cycle and decides where
    the expensive model is worth spending.
    """
    screen = national_mod.get(size_deg, live)
    return {
        **screen.summary(),
        "cells": [c.to_dict() for c in screen.cells],
        "watchlist": [c.to_dict() for c in screen.watchlist],
    }


@router.get("/api/national/watchlist", tags=["national"])
def national_watchlist(size_deg: float = Query(0.75, ge=0.25, le=2.0)) -> dict:
    """Only the squares that fired — the short list worth modelling properly."""
    screen = national_mod.get(size_deg, True)
    return {**screen.summary(),
            "watchlist": [c.to_dict() for c in screen.watchlist]}


@router.get("/api/live/{code}", tags=["national"])
def live_district(code: str) -> dict:
    """Live weather over one district, from the real forecast model.

    This is the endpoint that makes the provenance badge switch to LIVE: the
    rainfall, convective energy and soil moisture here are observed and
    forecast, not synthesised.
    """
    try:
        d = districts_data.get(code)
    except KeyError:
        raise HTTPException(404, "unknown district %r" % code)
    prov = openmeteo_mod.OpenMeteoProvider()
    wx = prov.fetch([(d.lat, d.lon)], past_days=7, forecast_days=7)
    if not wx:
        return {"district": code, "live": False,
                "reason": prov.status().reason,
                "last_error": openmeteo_mod.LAST_ERROR}
    w = wx[0]
    band, colour = national_mod.rainfall_band(
        max(w.rain_next_24h, w.rain_24h))
    return {
        "district": code, "name": d.name, "state": d.state,
        "live": True,
        "source": ("Open-Meteo forecast model — no API key required. "
                   "Rainfall bands are IMD's official warning categories."),
        "observed_and_forecast": w.to_dict(),
        "imd_band": band, "imd_colour": colour,
        "cape_band": national_mod.cape_band(w.cape_max_next_24h),
        "static_normal_mm": d.rainfall_normal_mm,
        "note": ("Antecedent index and soil saturation here replace the "
                 "synthetic values the demo scenario uses. Feed them to "
                 "/api/districts/{code}/assessment to run the physics on live "
                 "inputs."),
    }


# ---------------------------------------------------------------------------
# machine learning
# ---------------------------------------------------------------------------

@router.get("/api/watch", tags=["national"])
def watch_board(date: Optional[str] = Query(None),
                river: bool = Query(True),
                force: bool = Query(False)) -> dict:
    """Which districts need attention right now, and exactly why.

    The live board. For each of the 22 modelled districts it reads the vertical
    structure of the cloud column, the forecast rainfall against IMD warning
    bands and against that place's own five-year norm for this calendar week,
    the soil saturation, and - where the cheap signals have already fired - a
    river stage routed from live rainfall through real 30 m terrain.

    Then it weights all of that by who is standing underneath: rain over empty
    ground is weather, and the same rain over a district where six lakh people
    live inside a modelled red zone is an emergency.

    **No machine learning in this path, and no gauge readings.** The cloud
    reading is textbook convective meteorology over published thresholds, and
    the river figure is modelled and labelled as modelled - there is no public
    CWC feed.

    ``date=YYYY-MM-DD`` replays a day that has already happened through the
    identical pipeline, from the ERA5 archive. Every response from that path is
    stamped ``mode: replay`` and can never be mistaken for live.

    Replay dates that have been pre-computed by ``scripts/bake_demo_boards.py``
    are served from disk in milliseconds and need no network. A replayed day
    cannot change, so that is the same arithmetic rather than a stale cache.

    ``force=true`` bypasses the 15-minute cache for a live read and re-runs the
    whole pipeline against Open-Meteo right now - the one moment a
    demonstration is better served by a genuine re-read than a cached one.
    Ignored for a replay, since a replayed day has nothing new to read.
    """
    if date:
        pre = watch_mod.baked(date)
        if pre is not None:
            return pre
    return watch_mod.get(with_river=river, date=date, force=force).to_dict()


@router.get("/api/national/districts", tags=["national"])
def national_districts(rings: bool = Query(False)) -> dict:
    """Every district in India, at two clearly-separated tiers.

    ``modelled`` districts carry the full hazard model. ``screening`` districts
    carry a live-weather score aggregated from the national grid and nothing
    else — it says where to look, not what the hazard is. Both tiers are
    labelled on every record, and the interface must keep them distinguishable.

    ``rings=true`` includes simplified boundary geometry for map rendering; it
    is off by default because it multiplies the payload.
    """
    return national_districts_mod.get(include_rings=rings)


@router.get("/api/states", tags=["state"])
def states_list() -> dict:
    """States with modelled districts, for the state selector."""
    return {"states": state_view_mod.states_with_coverage()}


@router.get("/api/states/{state}", tags=["state"])
def state_rollup(state: str) -> dict:
    """The sheet a State Disaster Management Authority argues a budget from.

    Districts ranked by need, caseload split by relocation horizon, cost banded
    across the state. Coverage is stated alongside the totals, because a
    caseload computed from a subset of districts is not the state's caseload.
    """
    rows = state_view_mod.build(state)
    if not rows["districts"]:
        raise HTTPException(404, "no modelled districts in state %r" % state)
    return rows


@router.get("/api/export/habitations.csv", tags=["state"])
def export_habitations(state: Optional[str] = Query(None),
                       code: Optional[str] = Query(None)) -> Response:
    """Ranked habitations as CSV, for a district, a state, or everywhere.

    An insight that cannot leave the screen is not actionable.
    """
    csv = state_view_mod.habitations_csv(state=state, code=code)
    name = "drishti-habitations-%s.csv" % (code or state or "all")
    return Response(
        content=csv, media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="%s"' % name})


@router.get("/api/districts/{code}/encroachment", tags=["red zones"])
def district_encroachment(code: str,
                          baseline: Optional[int] = Query(None)) -> dict:
    """New construction inside the Red Zone, from satellite built-up surface.

    The sharpest form of "dynamically update Red Zones": built-up area from the
    Global Human Settlement Layer, differenced between epochs and intersected
    with the Red Zones. It says where to stop issuing building permissions, and
    it quantifies the relocation caseload that is still growing.
    """
    d = _district_or_404(code)
    a = scenario.assessment(code)
    res = encroachment_mod.assess(
        a.grid, code, a.redzones, a.exposure.population,
        a.exposure.district_mask, habitations=a.habitations,
        baseline_epoch=baseline)
    return {"district": code, "name": d.name, "state": d.state,
            **res.to_dict()}


@router.get("/api/districts/{code}/mitigation", tags=["red zones"])
def district_mitigation(code: str, limit: int = Query(10, ge=1, le=50)) -> dict:
    """What is actually done about each Red Zone habitation, and by whom.

    Relocation is the measure of last resort, per the NDMA National Disaster
    Management Plan. Where the hazard recurs on a timescale a protection work
    can outlive, the plan protects the habitation rather than moving it, and
    says why. Every measure names an accountable authority and the statute it
    rests on.
    """
    d = _district_or_404(code)
    a = scenario.assessment(code)
    habs = [h.to_dict() for h in a.habitations]
    plans = mitigation_mod.plans_for(habs, limit=limit)
    return {
        "district": code, "name": d.name, "state": d.state,
        "summary": mitigation_mod.district_summary(plans),
        "plans": [p.to_dict() for p in plans],
    }


@router.get("/api/mitigation/authorities", tags=["red zones"])
def mitigation_authorities() -> dict:
    """Every authority a measure can be assigned to, and the statutes in play."""
    from ..data import mitigations as mit
    return {
        "authorities": [a.to_dict() for a in mit.AUTHORITIES],
        "instruments": mit.INSTRUMENTS,
        "families": list(mit.families()),
        "measures": len(mit.RULES),
    }


@router.get("/api/history", tags=["history"])
def history_timeline(start: str = Query("1990-01-01"),
                     end: str = Query("2099-12-31")) -> dict:
    """Observed disaster history: curated events plus real cyclone tracks.

    Two provenances, deliberately kept distinct. Cyclone geometry is live from
    IBTrACS (NOAA) and needs no credentials. Flood and landslide events are
    curated by hand with a citation on every row, because no free API carries
    that history for India — every candidate was probed and each either returns
    an error or is an alerting feed rather than an archive.
    """
    return history_mod.timeline(start, end)


@router.get("/api/history/validation", tags=["history"])
def history_validation() -> dict:
    """Do the modelled Red Zones agree with where disasters actually happened?

    Reported as measured, including when the answer is unflattering.
    """
    return history_mod.validate_all()


@router.get("/api/history/validation/{code}", tags=["history"])
def history_validation_district(code: str) -> dict:
    _district_or_404(code)
    return history_mod.validate_district(code)


@router.get("/api/districts/{code}/history", tags=["history"])
def district_history(code: str) -> dict:
    d = _district_or_404(code)
    return {
        "district": code, "name": d.name, "state": d.state,
        "events": history_mod.events(district=code),
        "validation": history_mod.validate_district(code),
    }


@router.get("/api/ml", tags=["meta"])
def ml_report() -> dict:
    """Training reports: metrics, the physics baseline, feature importances.

    Published in full because a model nobody can check is a model nobody should
    trust. Note the validation protocol: held out by district, never at random.
    """
    reports = ml_mod.report()
    return {
        "sklearn_available": ml_mod.available(),
        "models": reports,
        "role": ("ML refines measurement and provides a fast national "
                 "surrogate. It is deliberately absent from the decision path: "
                 "relocation horizons and priority rankings stay "
                 "physics-derived and auditable."),
        "trained": bool(reports),
        "how_to_train": "python scripts/train_models.py",
    }


# ---------------------------------------------------------------------------
# multi-hazard red zones and relocation  (SIH26191)
# ---------------------------------------------------------------------------

@router.get("/api/districts/{code}/redzones", tags=["red zones"])
def redzones(code: str) -> dict:
    """The standing multi-hazard assessment: what land is unfit to live on.

    Severity-independent by design. A red zone is a property of the place across
    events, not the outcome of one storm, so this endpoint takes no severity.
    """
    try:
        districts_data.get(code)
    except KeyError:
        raise HTTPException(404, "unknown district %r" % code)
    a = scenario.assessment(code)
    return {
        **a.summary(),
        "boundary": a.boundary.geojson({"code": a.district.code,
                                        "name": a.district.name}),
    }


@router.get("/api/districts/{code}/redzones/explain", tags=["red zones"])
def explain_redzone_cell(code: str, lat: float = Query(...),
                         lon: float = Query(...)) -> dict:
    """Why this exact point on the map is, or is not, a Red Zone.

    Click-to-justify: every number here is read directly off the same arrays
    the map overlay is painted from, so the answer can never disagree with
    what is on screen. This is the endpoint that turns "the map is red here"
    into something a District Magistrate - or a judge - can check line by line
    against a published threshold.
    """
    try:
        districts_data.get(code)
    except KeyError:
        raise HTTPException(404, "unknown district %r" % code)
    a = scenario.assessment(code)
    return a.explain_cell(lat, lon)


@router.get("/api/districts/{code}/habitations", tags=["red zones"])
def habitations(code: str) -> dict:
    """Vulnerable habitations, ranked, with a relocation horizon each."""
    try:
        districts_data.get(code)
    except KeyError:
        raise HTTPException(404, "unknown district %r" % code)
    a = scenario.assessment(code)
    return {
        "district": code,
        "horizons": [{"name": n, "advice": adv}
                     for _, n, adv in redzone_mod.HORIZONS],
        "priority_weights": {
            "note": ("Published so the ranking can be checked. Exposure is how "
                     "many people are in a red zone, frequency is how often it "
                     "recurs."),
            "population_exposed": 0.42, "frequency": 0.30,
            "unsuitability": 0.18, "fraction_exposed": 0.10,
        },
        "habitations": [h.to_dict() for h in a.habitations],
    }


@router.get("/api/districts/{code}/relocation", tags=["red zones"])
def relocation(code: str) -> dict:
    """Candidate relocation sites, their carrying capacity, and the allocation."""
    try:
        districts_data.get(code)
    except KeyError:
        raise HTTPException(404, "unknown district %r" % code)
    a = scenario.assessment(code)
    return {
        "district": code,
        **a.relocation.summary(a.grid.cell_area_km2),
        "sites": [s.to_dict() for s in a.relocation.sites],
        "plans": [p.to_dict() for p in a.relocation.plans],
    }


@router.get("/api/districts/{code}/assessment-layers", tags=["red zones"])
def assessment_layer_index(code: str) -> dict:
    a = scenario.assessment(code)
    out = []
    for name, (title, ramp, unit) in ASSESSMENT_LAYERS.items():
        vmin, vmax = _assessment_range(a, name)
        out.append({"name": name, "title": title, "unit": unit,
                    "url": "/api/districts/%s/assessment-layers/%s.png" % (code, name),
                    "legend": render.legend(ramp, vmin, vmax, unit)})
    return {"bounds": a.grid.leaflet_bounds, "layers": out}


def _assessment_range(a, name: str):
    if name == "redzone":
        return 0.0, 1.0
    if name == "recurrence":
        return 5.0, 100.0
    if name == "landslide":
        return 0.0, max(float(a.landslide.tehd.max()), 1.0)
    if name == "erosion":
        return 0.0, max(float(a.erosion.retreat_m_per_year.max()), 1.0)
    if name == "suitability":
        return 0.0, 1.0
    return 0.0, 1.0


@router.get("/api/districts/{code}/assessment-layers/{name}.png", tags=["red zones"])
def assessment_layer_png(code: str, name: str,
                         scale: int = Query(4, ge=1, le=8)) -> Response:
    if name not in ASSESSMENT_LAYERS:
        raise HTTPException(404, "unknown layer %r" % name)
    a = scenario.assessment(code)
    inside = a.exposure.district_mask
    vmin, vmax = _assessment_range(a, name)
    ramp = ASSESSMENT_LAYERS[name][1]

    if name == "redzone":
        field = a.redzones.unsuitability
        mask = inside & a.redzones.is_red
        alpha = np.clip(0.35 + 0.6 * field, 0, 0.92)
    elif name == "recurrence":
        # Shorter interval = more urgent, so invert for the ramp.
        rp = a.redzones.return_period
        field = np.where(np.isfinite(rp), np.clip(rp, 5, 100), 100).astype(np.float32)
        mask = inside & a.redzones.is_red
        alpha = np.full(field.shape, 0.85, np.float32)
    elif name == "landslide":
        field = a.landslide.tehd
        mask = inside & (field > 0)
        alpha = np.clip(0.3 + 0.65 * field / max(vmax, 1e-6), 0, 0.9)
    elif name == "erosion":
        field = a.erosion.retreat_m_per_year
        mask = inside & (field > 0.1)
        alpha = np.full(field.shape, 0.88, np.float32)
    else:  # suitability
        field = a.relocation.suitability
        mask = inside & a.relocation.eligible & (field > 0.2)
        alpha = np.clip(0.25 + 0.7 * field, 0, 0.9)

    rgba = render.colorize(field, ramp, vmin, vmax, alpha=alpha, mask=mask)
    smooth = name in ("landslide", "suitability", "redzone")
    return Response(render.png_layer(rgba, scale=scale, smooth=smooth),
                    media_type="image/png")


@router.get("/api/districts/{code}/scenes", tags=["districts"])
def scenes(code: str, days: int = Query(14, ge=1, le=60),
           severity: str = Query("severe")) -> dict:
    """Real Sentinel-1 granules over this district, from the Copernicus catalogue.

    This is a live query and needs no credentials — Copernicus allows anonymous
    catalogue search. It answers the question a District Magistrate actually has
    between passes: when was the last radar look, and how stale is what I am
    seeing? An empty list is a valid answer and means the catalogue was
    unreachable or nothing has passed over.
    """
    sc = _scenario(code, severity)
    found = CopernicusProvider().search_scenes(sc.grid.bbox, days=days, limit=8)
    return {
        "district": code,
        "bbox": list(sc.grid.bbox),
        "window_days": days,
        "catalogue": "Copernicus Data Space Ecosystem",
        "scenes": [s.to_dict() for s in found],
        "latest_age_hours": found[0].age_hours if found else None,
        "note": ("Granules listed here are real acquisitions. Demo mode does not "
                 "download or process them; it models the backscatter instead."),
    }


@router.get("/api/districts/{code}/waterlogging", tags=["districts"])
def waterlogging(code: str, severity: str = Query("severe")) -> dict:
    sc = _scenario(code, severity)
    return {"district": sc.district.code, **sc.waterlogging.summary()}


@router.get("/api/districts/{code}/zones/{zone_id}", tags=["districts"])
def zone_detail(code: str, zone_id: str, severity: str = Query("severe")) -> dict:
    sc = _scenario(code, severity)
    zone = next((z for z in sc.impact.zones if z.id == zone_id), None)
    if zone is None:
        raise HTTPException(404, "unknown zone %r" % zone_id)
    card = next((c for c in sc.action_cards if c.zone_id == zone_id), None)
    return {"zone": zone.to_dict(),
            "action_card": card.to_dict() if card else None}


# ---------------------------------------------------------------------------
# raster layers
# ---------------------------------------------------------------------------

@router.get("/api/districts/{code}/layers", tags=["layers"])
def layer_index(code: str, severity: str = Query("severe"),
                live: bool = Query(False)) -> dict:
    sc, _ = _scenario_maybe_live(code, severity, live)
    out = []
    for name, (title, ramp, unit, needs_event) in LAYERS.items():
        entry = {"name": name, "title": title, "unit": unit,
                 "url": "/api/districts/%s/layers/%s.png%s"
                        % (code, name, "?live=true" if live else "")}
        if ramp:
            vmin, vmax = _layer_range(sc, name)
            entry["legend"] = render.legend(ramp, vmin, vmax, unit)
        out.append(entry)
    return {"bounds": sc.grid.leaflet_bounds, "layers": out}


def _layer_range(sc: scenario.Scenario, name: str):
    if name == "depth":
        return 0.0, max(float(sc.timeline.peak_depth.max()), 0.5)
    if name == "hand":
        return 0.0, float(np.percentile(sc.terrain.hand, 95))
    if name == "wetness":
        return 0.0, 1.0
    if name == "population":
        return 0.0, float(np.percentile(sc.exposure.population, 99.5))
    if name == "duration":
        return 0.0, max(float(sc.timeline.duration_hours.max()), 1.0)
    return 0.0, 1.0


@router.get("/api/districts/{code}/layers/{name}.png", tags=["layers"])
def layer_png(code: str, name: str, severity: str = Query("severe"),
              hour: Optional[int] = Query(None, ge=0),
              scale: int = Query(4, ge=1, le=8),
              live: bool = Query(False)) -> Response:
    """One raster layer as an RGBA PNG, ready for a Leaflet image overlay."""
    if name not in LAYERS:
        raise HTTPException(404, "unknown layer %r" % name)
    sc, _ = _scenario_maybe_live(code, severity, live)
    t = sc.terrain
    inside = sc.exposure.district_mask

    if name == "basemap":
        # Relief is drawn beyond the boundary too, so the district reads as
        # sitting in a landscape rather than floating in a void.
        z = 1.0 if sc.district.terrain == "steep-mountain" else 7.0
        rgba = render.compose_basemap(
            t.dem, sc.grid.cell_m, t.accum,
            t.meta["stream_threshold_cells"], t.sea_mask,
            z_factor=z, scale=scale)
        return Response(render.png_bytes(rgba), media_type="image/png")

    vmin, vmax = _layer_range(sc, name)
    if name == "depth":
        field = (sc.timeline.peak_depth if hour is None
                 else sc.timeline.depth[min(hour, sc.timeline.hours - 1)])
        mask = inside & (field >= flood_mod.MIN_DEPTH_M)
        alpha = np.clip(0.42 + 0.5 * field / max(vmax, 0.1), 0, 0.94)
    elif name == "duration":
        field = sc.timeline.duration_hours.astype(np.float32)
        mask = inside & (field > 0)
        alpha = np.full(field.shape, 0.8, np.float32)
    elif name == "hand":
        field = t.hand
        mask = inside & ~t.sea_mask
        alpha = np.full(field.shape, 0.78, np.float32)
    elif name == "wetness":
        field = sc.waterlogging.susceptibility
        mask = inside & (field >= 0.35)
        alpha = np.clip(0.25 + 0.75 * field, 0, 0.9)
    else:  # population
        field = sc.exposure.population
        mask = inside & (field > np.percentile(field[inside], 55))
        alpha = np.clip(0.3 + 0.7 * field / max(vmax, 1.0), 0, 0.92)

    rgba = render.colorize(field, LAYERS[name][1], vmin, vmax,
                           alpha=alpha, mask=mask)
    # Masks must not interpolate or their edges grow a translucent halo.
    smooth = name in ("hand", "wetness", "population")
    return Response(render.png_layer(rgba, scale=scale, smooth=smooth),
                    media_type="image/png")
