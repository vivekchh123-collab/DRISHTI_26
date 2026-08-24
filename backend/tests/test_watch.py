"""The live board: cloud-pattern reading, and what gets put in front of a duty officer.

Two things are being defended here.

The first is that **the cloud read is a stated rule over a stated number**, not a
model anybody has to take on faith. Every test below fixes the inputs by hand and
asserts the state that the documented rule requires. If someone later replaces
the rules with something fitted, these fail.

The second is :func:`test_missing_cape_does_not_read_as_a_calm_sky`. The ERA5
archive returns CAPE as a column of NaN rather than as an error. Treated as zero,
a sky filled to 100% at every level classifies as ``quiet`` — which is how the
morning of the Wayanad landslide would have read as a calm day. That failure is
silent, plausible, and would have survived into a demonstration.

No network here. Weather records are constructed directly.
"""

import numpy as np
import pytest

from app.core import cloudwatch, watch


class FakeWeather:
    """An hourly record shaped like PointWeather, built by hand."""

    def __init__(self, low, mid, high, cape, hours=48, now=24,
                 rain=0.0, freezing=4000.0, soil=0.4, cape_nan=False):
        n = hours
        f = lambda v: np.full(n, v, np.float32)
        self.lat, self.lon, self.elevation = 26.0, 85.0, 50.0
        self.times = ["2024-07-30T%02d:00" % (i % 24) for i in range(n)]
        self.past_hours = now
        self.cloud_low, self.cloud_mid, self.cloud_high = f(low), f(mid), f(high)
        self.cape = np.full(n, np.nan, np.float32) if cape_nan else f(cape)
        self.freezing_level = f(freezing)
        self.precipitation = f(rain)
        self.cloud_cover = f(max(low, mid, high))
        self.soil_moisture = f(soil)
        self.soil_moisture_deep = f(soil)

    # The properties watch.py reads off a real PointWeather.
    @property
    def rain_24h(self):
        return float(self.precipitation[:self.past_hours].sum())

    @property
    def rain_next_24h(self):
        return float(self.precipitation[self.past_hours:self.past_hours + 24].sum())

    @property
    def rain_next_72h(self):
        return float(self.precipitation[self.past_hours:].sum())

    @property
    def peak_hourly_next_24h(self):
        w = self.precipitation[self.past_hours:self.past_hours + 24]
        return float(w.max()) if w.size else 0.0

    @property
    def soil_saturation(self):
        return float(self.soil_moisture[self.past_hours])

    @property
    def antecedent_index_mm(self):
        return self.rain_24h


# --------------------------------------------------------- the four states

def test_a_shallow_sky_is_quiet():
    r = cloudwatch.read(FakeWeather(20, 10, 5, 200))
    assert r.state == "quiet"
    assert r.levels_filled == 0


def test_cloud_without_energy_is_not_a_storm():
    """A filled column with no CAPE is overcast, not convection."""
    r = cloudwatch.read(FakeWeather(90, 90, 90, 300))
    assert r.state == "quiet", "cloud alone must not read as a storm"


def test_energy_without_structure_is_only_building():
    r = cloudwatch.read(FakeWeather(80, 20, 10, 3000))
    assert r.state == "building"


def test_a_filled_column_with_strong_cape_is_deep_convection():
    r = cloudwatch.read(FakeWeather(80, 80, 80, 3000))
    assert r.state == "deep convection"
    assert r.levels_filled == 3


def test_an_anvil_over_deep_convection_is_a_mature_system():
    """High cloud far ahead of low cloud is cirrus outflow from a topped-out storm."""
    r = cloudwatch.read(FakeWeather(60, 80, 99, 3000))
    assert r.anvil is True
    assert r.state == "mature system"


def test_states_escalate_in_order():
    seq = [cloudwatch.read(FakeWeather(*a)).escalation for a in
           [(20, 10, 5, 200), (80, 20, 10, 3000),
            (80, 80, 80, 3000), (60, 80, 99, 3000)]]
    assert seq == sorted(seq) and seq[0] == 0 and seq[-1] == 3


# --------------------------------------------- the silent-failure this exists for

def test_missing_cape_does_not_read_as_a_calm_sky():
    """The archive serves CAPE as NaN. Zero would make a full sky look quiet.

    This is the morning of the Wayanad landslide: cloud at 100% through the
    whole column, and no CAPE record to confirm it. Reporting "quiet" would be
    both wrong and completely plausible on screen.
    """
    r = cloudwatch.read(FakeWeather(100, 100, 100, 0, cape_nan=True))
    assert r.cape_available is False
    assert r.state == "deep convection", (
        "a troposphere-deep column must not classify as quiet just because "
        "CAPE has no archive record")
    assert "CAPE unavailable" in " ".join(r.observations)


def test_a_rule_quoted_without_cape_does_not_cite_cape():
    """The stated reason must describe a test that was actually run."""
    r = cloudwatch.read(FakeWeather(100, 100, 100, 0, cape_nan=True))
    assert "2500" not in r.state_rule
    assert "unavailable" in r.state_rule.lower()


# ------------------------------------------------------------- the anomaly

def test_anomaly_is_measured_against_this_place_and_week():
    wk = str(cloudwatch._week_of_year())
    norms = {"weekly": {wk: {"column_depth_mean": 40.0, "cloud_high_mean": 45.0}}}
    r = cloudwatch.read(FakeWeather(90, 90, 90, 3000), norms)
    assert r.anomaly_available is True
    assert r.depth_above_norm == pytest.approx(50.0, abs=0.5)
    assert "deeper than normal" in r.anomaly_note


def test_a_normal_monsoon_sky_is_not_called_anomalous():
    """Otherwise the board fires every day of the monsoon and nobody reads it."""
    wk = str(cloudwatch._week_of_year())
    norms = {"weekly": {wk: {"column_depth_mean": 88.0, "cloud_high_mean": 90.0}}}
    r = cloudwatch.read(FakeWeather(90, 90, 90, 3000), norms)
    assert r.depth_above_norm < cloudwatch.DEPTH_ANOMALY_PTS
    assert "normal for this location" in r.anomaly_note


def test_no_baseline_says_so_rather_than_claiming_normality():
    r = cloudwatch.read(FakeWeather(90, 90, 90, 3000), {"weekly": {}})
    assert r.anomaly_available is False
    assert "no baked norm" in r.anomaly_note


# ------------------------------------------------------------- the scoring

def _read(**kw):
    return cloudwatch.read(FakeWeather(**kw))


def test_people_underneath_change_the_ranking():
    """Rain over empty ground is weather; the same rain over a red zone is not."""
    cloud = _read(low=80, mid=80, high=80, cape=3000)
    rain = {"next_24h_mm": 120.0, "next_72h_mm": 200.0, "peak_hourly_mm": 20.0,
            "imd_band": "very heavy", "vs_normal": None}
    soil = {"saturation": 0.9, "reading": "saturated"}

    empty, _ = watch._score(cloud, rain, soil, {})
    peopled, why = watch._score(cloud, rain, soil,
                                {"population_in_red_zone": 900_000})
    assert peopled > empty
    assert any("live inside" in w for w in why)


def test_action_needs_both_urgency_and_lead_time():
    assert watch._action(70.0, 6) == watch.ACT
    assert watch._action(70.0, 40) == watch.PREPARE, (
        "a severe signal two days out is a preparation, not an evacuation")
    assert watch._action(45.0, 3) == watch.PREPARE
    assert watch._action(30.0, 3) == watch.WATCH
    assert watch._action(5.0, None) == watch.ROUTINE


def test_every_action_says_what_it_means():
    for a in (watch.ACT, watch.PREPARE, watch.WATCH, watch.ROUTINE):
        assert watch.ACTION_MEANING[a]


# ------------------------------------------------- offline and provenance

def test_offline_board_reports_nothing_rather_than_zeroes(monkeypatch):
    """A board of zeroes reads as a national all-clear. It must stay empty."""
    from app.providers import openmeteo
    monkeypatch.setattr(openmeteo.OpenMeteoProvider, "fetch",
                        lambda *a, **k: None)
    monkeypatch.setattr(watch, "_CACHE", {})
    board = watch.build(with_river=False)
    assert board.live is False
    assert board.districts == []
    assert "all-clear" in board.reason
    assert "does not guess" in board.headline()


def test_river_is_never_presented_as_a_gauge_reading():
    """There is no public CWC feed. A modelled stage must say it is modelled."""
    board = watch.WatchBoard(districts=[], live=True, generated_at="x",
                             seconds=0.0, source="s", river_runs=0)
    assert "no gauge readings" in board.to_dict()["method"]


def test_replay_is_labelled_replay_everywhere():
    board = watch.WatchBoard(districts=[], live=True, generated_at="x",
                             seconds=0.0, source="s", river_runs=0,
                             replay_date="2024-07-30")
    d = board.to_dict()
    assert d["mode"] == "replay"
    assert d["replay_date"] == "2024-07-30"
    assert "not live" in d["headline"].lower()


def test_soil_moisture_is_actually_requested():
    """It carries 0.18 of the score, and leaving it out read as bone-dry ground."""
    assert any("soil_moisture" in v for v in cloudwatch.HOURLY_CLOUD)


# --------------------------------------------------------- the river reach

def test_river_reads_the_trunk_reach_not_a_hillside_puddle():
    """The hydrology is spatial; "the river level" needs a cell chosen for it.

    This also guards a bug that shipped once: ``bankfull_m`` is an (n, n) grid,
    not a scalar, and ``float(x or 0.0)`` on an array raises. It only fired on
    the live path, because every earlier test ran with the river simulation off.
    """
    r = watch._river_read("BR-DAR")
    if not r["available"]:
        pytest.skip(r.get("reason", "live feed unavailable"))

    assert r["gauged"] is False, "a modelled stage must never claim to be gauged"
    assert "NOT a gauge reading" in r["provenance"]
    assert r["catchment_km2"] > 100, (
        "the chosen reach should be the trunk river, not an upland cell")
    assert r["bankfull_m"] > 0
    assert isinstance(r["peak_stage_m"], float)
    assert r["peak_stage_m"] >= r["stage_now_m"] - 1e-6
    assert r["reading"]


# ------------------------------------------------- the demonstration path

def test_replay_boards_are_baked_and_load_without_a_network(monkeypatch):
    """The whole replay path must survive the venue wifi dying.

    This is the claim a panel will actually test: pull the cable and see what
    happens. Every network call is made to raise here, so if any part of serving
    a replayed board still reaches for the internet, this fails.
    """
    import urllib.request

    dates = watch.baked_dates()
    assert dates, ("no replay boards baked - run scripts/bake_demo_boards.py; "
                   "without them a demonstration waits minutes for each board")

    def no_network(*a, **k):
        raise AssertionError("a baked board must not touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)

    for row in dates:
        board = watch.baked(row["date"])
        assert board is not None, "index lists %s but no board on disk" % row["date"]
        assert board["mode"] == "replay"
        assert board["replay_date"] == row["date"]
        assert board["counts"]["assessed"] == 22
        assert board["districts"], "a baked board with no districts is useless"


def test_baked_board_carries_the_evidence_not_just_a_score():
    """A number nobody can interrogate is a number nobody should believe."""
    board = watch.baked("2024-07-30")
    if board is None:
        pytest.skip("boards not baked")

    listed = [d for d in board["districts"] if d["action"] != "ROUTINE"]
    assert listed, "the Wayanad replay should have districts on the board"

    for d in listed:
        assert d["why"], "%s is on the board with no stated reason" % d["name"]
        assert d["cloud"]["state_rule"], "no rule behind the cloud state"
        assert d["cloud"]["anomaly"]["basis"], "no basis behind the anomaly"
        assert d["action_meaning"]
        assert d["river"]["gauged"] is False


def test_wayanad_is_on_the_board_the_morning_it_happened():
    """The 2024 landslide is the case this whole reading exists to catch.

    Not asserting it ranks first: on 30 July 2024 much of eastern India was
    genuinely wetter, and tuning the weights until Wayanad won would be exactly
    the retrofitting this project disclaims elsewhere. Asserting it was *listed*,
    which is the honest and still-useful claim.
    """
    board = watch.baked("2024-07-30")
    if board is None:
        pytest.skip("boards not baked")

    way = next((d for d in board["districts"] if d["code"] == "KL-WAY"), None)
    assert way is not None
    assert way["action"] in (watch.ACT, watch.PREPARE), (
        "Wayanad read as %s on the morning of the landslide" % way["action"])
    assert way["cloud"]["column"]["levels_filled"] == 3
    assert way["cloud"]["anomaly"]["points_above_normal"] > 20


# ------------------------------------------------------- force refresh

def test_force_bypasses_the_cache(monkeypatch):
    """A demonstration's "watch me refresh this live" moment must be real.

    A force-refresh that quietly served the same cached object would be a
    trick, not a demonstration - the button would look like it did something
    while actually doing nothing. This asserts the underlying build actually
    runs again rather than being skipped.
    """
    monkeypatch.setattr(watch, "_CACHE", {})
    calls = {"n": 0}
    real_build = watch.build

    def counting_build(*a, **k):
        calls["n"] += 1
        return real_build(*a, **k)

    monkeypatch.setattr(watch, "build", counting_build)

    watch.get(with_river=False)
    assert calls["n"] == 1, "first call should build"
    watch.get(with_river=False)
    assert calls["n"] == 1, "second ordinary call should hit the cache"
    watch.get(with_river=False, force=True)
    assert calls["n"] == 2, "force=True must trigger a genuine rebuild"
    watch.get(with_river=False)
    assert calls["n"] == 2, "the forced result should itself be cached"


def test_force_is_a_no_op_on_a_replay():
    """A replayed day cannot change, so there is nothing force should re-read."""
    board = watch.baked("2024-07-30")
    if board is None:
        pytest.skip("boards not baked")
    # Replays are served straight from the baked file at the API layer and
    # never reach watch.get(); this only guards that get() itself does not
    # crash or misbehave if ever called with both a date and force=True.
    watch.get(with_river=False, date="2024-07-30", force=True)
