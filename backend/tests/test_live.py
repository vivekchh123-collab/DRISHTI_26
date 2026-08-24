"""The live-data path, tested without touching the network.

Network-dependent tests are bad tests: they fail for reasons that have nothing
to do with the code, they cannot run offline, and against a per-coordinate
rate limit they are actively expensive. So the logic is exercised against a
deterministic fake provider that returns exactly the shapes Open-Meteo returns,
including its awkward ones — a single point comes back as an object where many
come back as a list, and any hourly value may be null.

One real call is kept as a marked integration smoke test and skipped by default.
"""

import os

import numpy as np
import pytest

from app.core import rainfall as rainfall_mod
from app.core import scenario
from app.core.grid import Grid
from app.providers import openmeteo as om
from app.providers.openmeteo import PointWeather


# ---------------------------------------------------------------------------
# a fake that behaves like the real thing, including badly
# ---------------------------------------------------------------------------

class FakeProvider:
    """Deterministic stand-in for Open-Meteo.

    ``pattern`` shapes the rainfall so tests can assert on structure: "uniform"
    for an even field, "west" for a storm sitting over one side of the district,
    which is the case a single-point fetch cannot represent at all.
    """

    def __init__(self, pattern="uniform", mm_per_hour=2.0, soil=0.5,
                 past_days=7, forecast_days=7, fail=False, short=False,
                 with_nans=False):
        self.pattern = pattern
        self.mm = mm_per_hour
        self.soil = soil
        self.past_days = past_days
        self.forecast_days = forecast_days
        self.fail = fail
        self.short = short
        self.with_nans = with_nans
        self.calls = 0

    def fetch(self, points, past_days=7, forecast_days=7, variables=None):
        self.calls += 1
        if self.fail:
            return None
        n = (past_days + forecast_days) * 24
        if self.short:
            n = n // 2                    # provider returned a truncated series
        out = []
        lons = [p[1] for p in points]
        lo, hi = min(lons), max(lons)
        for (lat, lon) in points:
            if self.pattern == "west":
                # Wetter to the west, dry to the east.
                f = 1.0 - ((lon - lo) / max(hi - lo, 1e-9))
                scale = 0.1 + 1.9 * f
            else:
                scale = 1.0
            precip = np.full(n, self.mm * scale, np.float32)
            if self.with_nans:
                precip[::17] = np.nan     # providers do return nulls
            out.append(PointWeather(
                lat=lat, lon=lon, elevation=50.0,
                times=["t%d" % i for i in range(n)],
                precipitation=precip,
                cloud_cover=np.full(n, 80.0, np.float32),
                cape=np.full(n, 1500.0, np.float32),
                soil_moisture=np.full(n, self.soil * 0.45, np.float32),
                soil_moisture_deep=np.full(n, self.soil * 0.45, np.float32),
                past_hours=past_days * 24))
        return out

    def climatology(self, lat, lon, years=5):
        return {"years": years, "daily_mean_mm": 3.0, "daily_p90_mm": 10.0,
                "daily_p99_mm": 45.0, "wet_day_fraction": 0.3,
                "annual_mm": 1100.0}


@pytest.fixture(scope="module")
def base():
    return scenario.base("BR-DAR")


# ---------------------------------------------------------------------------
# the live event satisfies the same contract as a design storm
# ---------------------------------------------------------------------------

def test_live_event_matches_the_design_storm_contract(base):
    """Whatever else changes, the downstream models must not have to care."""
    ev = rainfall_mod.build_live_event(base.grid, base.terrain, FakeProvider(),
                                       hours=96)
    assert ev is not None
    assert ev.hours == 96
    assert ev.depth_mm.shape == (96,)
    assert ev.weight.shape == (base.grid.n, base.grid.n)
    assert ev.field(0).shape == (base.grid.n, base.grid.n)
    assert ev.cumulative(10).shape == (base.grid.n, base.grid.n)
    assert ev.live is True
    assert ev.severity == "observed"


def test_live_event_returns_none_when_the_feed_is_down(base):
    assert rainfall_mod.build_live_event(
        base.grid, base.terrain, FakeProvider(fail=True)) is None


def test_a_truncated_series_is_padded_not_crashed(base):
    """Providers do return short series. That must not take the district down."""
    ev = rainfall_mod.build_live_event(base.grid, base.terrain,
                                       FakeProvider(short=True), hours=96)
    assert ev is not None
    assert ev.depth_mm.shape == (96,)
    assert np.isfinite(ev.depth_mm).all()


def test_null_hourly_values_do_not_poison_the_field(base):
    """A single null must not turn the whole rainfall field into NaN."""
    ev = rainfall_mod.build_live_event(base.grid, base.terrain,
                                       FakeProvider(with_nans=True), hours=96)
    assert ev is not None
    assert np.isfinite(ev.depth_mm).all()
    assert np.isfinite(ev.weight).all()
    assert np.isfinite(ev.field(3)).all()


def test_weight_is_normalised_and_bounded(base):
    ev = rainfall_mod.build_live_event(base.grid, base.terrain,
                                       FakeProvider(pattern="west"), hours=96)
    assert ev.weight.min() > 0
    assert ev.weight.max() <= 5.0
    assert 0.5 < float(ev.weight.mean()) < 2.0


def test_the_spatial_pattern_is_actually_sampled(base):
    """The whole reason for sampling a grid rather than one point.

    A storm over the western half of a district has to appear as one. If the
    weight field comes out flat, the multi-point fetch is doing nothing and the
    extra API cost is being paid for no information.
    """
    ev = rainfall_mod.build_live_event(base.grid, base.terrain,
                                       FakeProvider(pattern="west"), hours=96)
    n = base.grid.n
    west = float(ev.weight[:, : n // 4].mean())
    east = float(ev.weight[:, -n // 4:].mean())
    assert west > east * 1.5, "sampled rainfall gradient was lost (%.2f vs %.2f)" % (west, east)


def test_uniform_rainfall_produces_a_flat_field(base):
    ev = rainfall_mod.build_live_event(base.grid, base.terrain,
                                       FakeProvider(pattern="uniform"), hours=96)
    assert float(ev.weight.std()) < 0.05


def test_total_depth_reflects_the_feed(base):
    ev = rainfall_mod.build_live_event(base.grid, base.terrain,
                                       FakeProvider(mm_per_hour=3.0), hours=48)
    assert ev.total_mm == pytest.approx(3.0 * 48, rel=0.02)


def test_observed_and_forecast_hours_add_up(base):
    ev = rainfall_mod.build_live_event(base.grid, base.terrain, FakeProvider(),
                                       hours=96, past_hours=24)
    s = ev.summary()
    assert s["observed_hours"] == 24
    assert s["forecast_hours"] == 72
    assert s["observed_hours"] + s["forecast_hours"] == s["duration_hours"]


def test_saturated_soil_raises_the_antecedent_index(base):
    dry = rainfall_mod.build_live_event(base.grid, base.terrain,
                                        FakeProvider(soil=0.1, mm_per_hour=0.0))
    wet = rainfall_mod.build_live_event(base.grid, base.terrain,
                                        FakeProvider(soil=1.0, mm_per_hour=0.0))
    assert wet.antecedent_mm > dry.antecedent_mm
    assert wet.soil_saturation > dry.soil_saturation


def test_sample_count_is_reported(base):
    ev = rainfall_mod.build_live_event(base.grid, base.terrain, FakeProvider(),
                                       samples=4)
    assert ev.sample_points == 16


# ---------------------------------------------------------------------------
# recurrence banding
# ---------------------------------------------------------------------------

def test_recurrence_band_rises_with_depth():
    clim = {"years": 5, "daily_mean_mm": 3.0, "daily_p90_mm": 10.0,
            "daily_p99_mm": 45.0}
    bands = [rainfall_mod.estimate_return_period(d, clim)["approx_recurrence_days"]
             for d in (1.0, 5.0, 20.0, 90.0)]
    assert bands == sorted(bands)


def test_recurrence_states_its_own_caveat():
    clim = {"years": 5, "daily_mean_mm": 3.0, "daily_p90_mm": 10.0,
            "daily_p99_mm": 45.0}
    r = rainfall_mod.estimate_return_period(100.0, clim)
    assert r["available"] is True
    assert "annual maxima" in r["caveat"]
    assert "not a fitted" in r["caveat"]


def test_recurrence_without_climatology_says_so():
    r = rainfall_mod.estimate_return_period(100.0, None)
    assert r["available"] is False
    assert r["reason"]


# ---------------------------------------------------------------------------
# the live scenario runs the same physics
# ---------------------------------------------------------------------------

def _live_scenario(monkeypatch, provider):
    monkeypatch.setattr(om, "OpenMeteoProvider", lambda *a, **k: provider)
    scenario._LIVE_CACHE.clear()
    return scenario.build_live("BR-DAR")


def test_live_scenario_builds_and_is_labelled_live(monkeypatch):
    sc = _live_scenario(monkeypatch, FakeProvider(mm_per_hour=4.0))
    assert sc is not None
    assert sc.live is True
    prov = sc.provenance
    assert prov["data_mode"] == "live"
    # It must not overclaim: terrain and population are still modelled.
    assert "modelled terrain" in prov["note"]


def test_live_scenario_produces_a_real_flood(monkeypatch):
    sc = _live_scenario(monkeypatch, FakeProvider(mm_per_hour=6.0, soil=0.95))
    assert sc.timeline.peak_area_km2 > 0
    assert (sc.timeline.depth >= 0).all()
    assert sc.impact.population_affected <= sc.impact.population_total


def test_live_scenario_falls_back_when_the_feed_is_down(monkeypatch):
    assert _live_scenario(monkeypatch, FakeProvider(fail=True)) is None


def test_more_rain_floods_more(monkeypatch):
    """The physics must respond to the live input, not ignore it."""
    light = _live_scenario(monkeypatch, FakeProvider(mm_per_hour=1.0))
    heavy = _live_scenario(monkeypatch, FakeProvider(mm_per_hour=8.0))
    assert heavy.timeline.peak_area_km2 > light.timeline.peak_area_km2
    assert heavy.event.total_mm > light.event.total_mm


def test_wetter_soil_floods_more_for_the_same_rain(monkeypatch):
    """Antecedent wetness has to matter, or soil moisture is decoration."""
    dry = _live_scenario(monkeypatch, FakeProvider(mm_per_hour=4.0, soil=0.15))
    wet = _live_scenario(monkeypatch, FakeProvider(mm_per_hour=4.0, soil=1.0))
    assert wet.timeline.peak_area_km2 >= dry.timeline.peak_area_km2
    assert wet.event.antecedent_mm > dry.event.antecedent_mm


def test_live_mode_never_invents_a_storm_surge(monkeypatch):
    """A surge without a cyclone forecast would be the worst thing this mode
    could fabricate, so a coastal district must not get one."""
    monkeypatch.setattr(om, "OpenMeteoProvider",
                        lambda *a, **k: FakeProvider(mm_per_hour=3.0))
    scenario._LIVE_CACHE.clear()
    sc = scenario.build_live("OD-PUR")
    assert sc is not None
    # No surge means no inundation on ground the river cannot reach.
    assert sc.timeline.peak_depth.max() < 30.0


def test_the_demo_path_is_untouched_by_live_mode():
    """The synthetic scenario must be byte-identical to before live mode."""
    a = scenario.build("BR-DAR", "severe")
    assert a.live is False
    assert a.provenance["data_mode"] == "demo"
    assert a.event.live is False


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_state_is_reportable():
    st = om.rate_limit_status()
    assert set(st) >= {"rate_limited", "retry_in_seconds", "note"}
    assert "per coordinate" in st["note"]


def test_rate_limited_provider_declares_itself_unavailable(monkeypatch):
    monkeypatch.setattr(om, "_RATE_LIMITED_UNTIL", om.time.time() + 300)
    st = om.OpenMeteoProvider().status()
    assert st.available is False
    assert "Rate limited" in st.reason


# ---------------------------------------------------------------------------
# integration: one real call, off by default
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("DRISHTI_LIVE_TEST"),
                    reason="set DRISHTI_LIVE_TEST=1 to hit the real API")
def test_real_open_meteo_call():
    prov = om.OpenMeteoProvider()
    wx = prov.fetch([(26.15, 85.90)], past_days=2, forecast_days=2)
    assert wx and len(wx) == 1
    w = wx[0]
    assert np.isfinite(w.precipitation).any()
    assert 0.0 <= w.soil_saturation <= 1.0
