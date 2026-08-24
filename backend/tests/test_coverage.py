"""National, state and encroachment coverage.

These three modules all do the same dangerous thing: they take a small number of
carefully modelled districts and present them alongside a large number of
places we know much less about. The tests here are mostly about that seam.

The one that matters most is
:func:`test_offline_screening_does_not_report_an_all_clear`. Without a live
weather connection every screening cell scores zero, and publishing 713 zeros
would render as a confident national all-clear — the most harmful output this
system could produce. Absence of information has to look like absence of
information.

No test here touches the network. The national screen is stubbed, which is also
what makes these fast.
"""

import pytest

from app.core import encroachment as enc_mod
from app.core import national_districts as nd
from app.core import scenario, state_view
from app.data import districts as districts_data


class FakeCell:
    def __init__(self, lat, lon, score, drivers=()):
        self.lat, self.lon = lat, lon
        self.score, self.drivers = score, list(drivers)


class FakeScreen:
    def __init__(self, live=True, cells=()):
        self.live, self.cells = live, list(cells)


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(nd, "_CACHE", None)
    from app.core import national as national_mod
    monkeypatch.setattr(national_mod, "get", lambda *a, **k: FakeScreen(False, []))


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(nd, "_CACHE", None)
    from app.core import national as national_mod
    # One cell per degree over the mainland, so every district bbox catches some.
    cells = [FakeCell(lat + 0.5, lon + 0.5, 40.0, ["test driver"])
             for lat in range(6, 36) for lon in range(68, 98)]
    monkeypatch.setattr(national_mod, "get", lambda *a, **k: FakeScreen(True, cells))


# ----------------------------------------------------------- the two tiers

def test_every_district_in_the_country_is_covered(online):
    out = nd.build()
    assert out["available"] is True
    assert out["counts"]["total"] > 700
    assert (out["counts"]["modelled"] + out["counts"]["screening"]
            == out["counts"]["total"])


def test_every_modelled_district_is_recognised(online):
    """All 22 districts we model must match a boundary by name.

    A name that fails to match falls silently into the screening tier and loses
    its physics without anything going wrong on screen, which is why the alias
    table exists and why this test guards it.
    """
    out = nd.build()
    modelled = {r["code"] for r in out["districts"] if r["tier"] == nd.MODELLED}
    assert modelled == {d.code for d in districts_data.DISTRICTS}


def test_tier_is_stated_on_every_record(online):
    for r in nd.build()["districts"]:
        assert r["tier"] in (nd.MODELLED, nd.SCREENING)
        assert r["basis"], "a score with no stated basis is not publishable"


def test_screening_records_carry_no_exposure_figures(online):
    """Screening has no terrain behind it, so it has no population-in-red-zone.

    If these keys ever appear on a screening record it means a number with no
    physics behind it is being presented as though it had some.
    """
    for r in nd.build()["districts"]:
        if r["tier"] == nd.SCREENING:
            assert "population_in_red_zone" not in r
            assert "red_zone_km2" not in r


def test_modelled_records_carry_the_full_caseload(online):
    for r in nd.build()["districts"]:
        if r["tier"] == nd.MODELLED:
            assert r["population_in_red_zone"] >= 0
            assert r["red_zone_km2"] >= 0
            assert r["immediate_habitations"] >= 0


# ------------------------------------------------- the offline failure mode

def test_offline_screening_does_not_report_an_all_clear(offline):
    """No live weather must read as "unknown", never as "nothing is wrong"."""
    out = nd.build()
    assert out["screening_available"] is False
    screening = [r for r in out["districts"] if r["tier"] == nd.SCREENING]
    assert screening, "expected screening districts to still be listed"
    assert all(r["score"] is None for r in screening), (
        "a zero score offline is a false all-clear for the whole country")
    assert "not an all-clear" in out["screening_note"]


def test_modelled_districts_survive_going_offline(offline):
    """Their physics is baked, so losing the network must not blank them."""
    modelled = [r for r in nd.build()["districts"] if r["tier"] == nd.MODELLED]
    assert len(modelled) == len(districts_data.DISTRICTS)
    assert all(r["score"] is not None for r in modelled)


def test_unscored_districts_sort_last(offline):
    scores = [r["score"] for r in nd.build()["districts"]]
    first_none = next((i for i, s in enumerate(scores) if s is None), len(scores))
    assert all(s is None for s in scores[first_none:]), (
        "an unscored district must never outrank a scored one")


# ------------------------------------------------------------- state view

@pytest.fixture(scope="module")
def assam():
    return state_view.build("Assam")


def test_state_rolls_up_its_districts(assam):
    assert assam["districts"], "Assam should have modelled districts"
    total = sum(d["population_in_red_zone"] for d in assam["districts"])
    assert assam["totals"]["population_in_red_zone"] == pytest.approx(total, rel=1e-6)


def test_state_ranks_by_need_not_by_cost(assam):
    """Ranking on cost would put the cheapest district first, which inverts it."""
    scores = [d["priority_score"] for d in assam["districts"]]
    assert scores == sorted(scores, reverse=True)


def test_state_export_names_every_habitation(assam):
    csv = state_view.habitations_csv("Assam")
    lines = [l for l in csv.strip().splitlines() if l]
    expected = sum(d["habitations"] for d in assam["districts"])
    assert len(lines) - 1 == expected, "header plus one row per habitation"


def test_state_export_is_not_silently_empty():
    for name in state_view.states_with_coverage():
        assert state_view.habitations_csv(name).strip().splitlines()[0].count(",") > 3


# ----------------------------------------------------------- encroachment

@pytest.fixture(scope="module")
def darbhanga():
    a = scenario.assessment("BR-DAR")
    return a, enc_mod.assess(a.grid, "BR-DAR", a.redzones, a.exposure.population,
                             a.exposure.district_mask, habitations=a.habitations)


def test_encroachment_is_bounded_by_the_red_zone(darbhanga):
    """New building *inside* red zones cannot exceed the red zone itself."""
    a, enc = darbhanga
    if not enc.available:
        pytest.skip("GHSL not baked for this district")
    red_km2 = float(a.redzones.is_red.sum()) * a.grid.cell_area_km2
    assert 0 <= enc.stats["new_build_in_red_zone_km2"] <= red_km2
    assert (enc.stats["new_build_in_red_zone_km2"]
            <= enc.stats["new_build_km2"] + 1e-6), (
        "new building inside red zones cannot exceed new building overall")


def test_encroachment_concentration_is_a_ratio(darbhanga):
    _, enc = darbhanga
    if not enc.available:
        pytest.skip("GHSL not baked for this district")
    assert 0.0 <= enc.stats["concentration_ratio"] <= 1.0
    assert enc.stats["concentration_reading"], (
        "a concentration figure needs a sentence saying what it means")


def test_encroachment_names_the_epochs_it_differenced(darbhanga):
    """A change figure with no dates behind it cannot be checked by anyone."""
    _, enc = darbhanga
    if not enc.available:
        pytest.skip("GHSL not baked for this district")
    assert enc.stats["baseline_epoch"] < enc.stats["latest_epoch"]
    assert "GHS-BUILT" in enc.stats["source"]
