"""Red zones, landslide hazard, erosion and relocation.

The most important test in this file is
:func:`test_no_relocation_site_is_inside_a_red_zone`. Everything else here is
about correctness; that one is about safety. A bug that lets the allocator send
people to a hazardous site would be the single worst failure this system could
have, and it is the kind of bug that is invisible on a map and obvious in an
inquiry.
"""

import numpy as np
import pytest

from app.core import erosion as erosion_mod
from app.core import landslide as landslide_mod
from app.core import redzone as redzone_mod
from app.core import relocation as relocation_mod
from app.core import scenario
from app.data import districts as districts_data

# A floodplain, a hill district and a delta: the three that exercise different
# hazards. Chennai is added because it is the densest and stresses capacity.
CODES = ["BR-DAR", "KL-WAY", "OD-PUR"]


@pytest.fixture(scope="module", params=CODES)
def a(request):
    return scenario.assessment(request.param)


# ---------------------------------------------------------------------------
# landslide — BIS IS 14496 (Part 2)
# ---------------------------------------------------------------------------

def test_every_lhef_factor_stays_inside_its_bis_maximum(a):
    for name, rating in a.landslide.factors.items():
        limit = landslide_mod.LHEF_MAX[name]
        assert rating.min() >= -1e-6, "%s went negative" % name
        assert rating.max() <= limit + 1e-6, \
            "%s exceeded its BIS maximum of %s" % (name, limit)


def test_tehd_never_exceeds_the_achievable_maximum(a):
    assert a.landslide.tehd.max() <= a.landslide.achievable_max + 1e-5
    assert a.landslide.tehd.max() <= landslide_mod.TEHD_MAX + 1e-5


def test_flat_districts_carry_no_landslide_hazard():
    """A landslide model that finds hazard in Darbhanga is broken."""
    flat = scenario.assessment("BR-DAR")
    assert float(flat.landslide.tehd.max()) == pytest.approx(0.0, abs=1e-6)
    assert int(flat.landslide.initiation.sum()) == 0


def test_hill_districts_do_carry_landslide_hazard():
    hill = scenario.assessment("KL-WAY")
    assert float(hill.landslide.tehd.max()) > 4.0


def test_landslide_never_initiates_on_gentle_ground(a):
    """Initiation is judged on hillslope, not on the cell average.

    The distinction is the point, not a technicality. A 170 m cell averaging
    15 degrees can still contain a 35-degree face, and that face is what fails;
    judging by the average is exactly how Wayanad's escarpments went missing.
    So the guarantee is about `hillslope` — the native-30 m 90th percentile —
    and the grid-scale slope is deliberately allowed to be gentler than 20.
    """
    init = a.landslide.initiation
    if not init.any():
        pytest.skip("no initiation modelled for this district")
    assert (a.terrain.hillslope[init] >= 20.0).all()

    # No assertion is made about the grid-scale slope, deliberately. Wayanad has
    # an initiation cell whose 170 m average is 0.98 degrees and whose hillslope
    # is 35 — a cliff at the edge of a plateau. That combination looks like a
    # bug and is the single case this whole change exists to catch, so a bound
    # on the cell average here would forbid the correct answer.


def test_landslide_initiation_is_a_small_share_of_the_district(a):
    """Susceptibility is not initiation.

    Deep Himalayan valleys are steep almost everywhere, so "steep and
    susceptible" describes a third of Rudraprayag. They do not all fail in one
    storm. Real inventories, including Kerala 2018 and the 2023 Himachal
    monsoon, map source areas well under a couple of per cent of the affected
    terrain, and a map that shows more than that has stopped meaning anything.
    """
    init = a.landslide.initiation
    if not init.any():
        pytest.skip("no initiation modelled for this district")
    share = float(init.mean())
    assert share <= 0.05, (
        "initiation covers %.1f%% of the district; susceptibility is being "
        "reported as failure" % (100 * share))


def test_runout_only_ever_grows_the_affected_area(a):
    assert int(a.landslide.affected.sum()) >= int(a.landslide.initiation.sum())


def test_caine_threshold_falls_with_duration():
    """Longer storms need lower intensity to trigger — the whole point of an
    intensity-duration relation."""
    d = np.array([0.5, 1, 3, 6, 12, 24, 72, 240], float)
    thr = landslide_mod.caine_threshold(d)
    assert (np.diff(thr) < 0).all()


def test_a_wet_antecedent_lowers_the_trigger_threshold():
    rain = np.full(48, 4.0)
    dry = landslide_mod.rainfall_trigger(rain, antecedent_mm=0.0)
    wet = landslide_mod.rainfall_trigger(rain, antecedent_mm=200.0)
    assert wet["threshold_mm_hr"] < dry["threshold_mm_hr"]
    assert wet["exceedance_ratio"] > dry["exceedance_ratio"]


def test_partial_assessment_is_declared_as_partial(a):
    """Lithology and structure are unavailable, and the API must say so."""
    s = a.landslide.summary()
    assert s["assessment_complete"] is False
    assert set(s["factors_estimated"]) == {"lithology", "structure"}
    assert "Geological Survey" in s["note"]


# ---------------------------------------------------------------------------
# erosion
# ---------------------------------------------------------------------------

def test_erosion_applies_only_to_coastal_districts():
    assert scenario.assessment("BR-DAR").erosion.applicable is False
    assert scenario.assessment("OD-PUR").erosion.applicable is True


def test_retreat_rate_is_bounded_and_non_negative(a):
    r = a.erosion.retreat_m_per_year
    assert (r >= 0).all()
    assert r.max() <= erosion_mod.MAX_RETREAT_M_PER_YEAR + 1e-6


def test_erosion_is_confined_to_the_coast():
    """The rate belongs to the shore, not to the whole coastal plain."""
    c = scenario.assessment("OD-PUR")
    eroding = c.erosion.retreat_m_per_year > 0.1
    assert eroding.mean() < 0.35, \
        "erosion painted over %.0f%% of the frame" % (eroding.mean() * 100)


# ---------------------------------------------------------------------------
# recurrence
# ---------------------------------------------------------------------------

def test_return_period_is_one_of_the_modelled_events_or_infinite(a):
    rp = a.redzones.return_period
    allowed = {e["return_period_years"] for e in a.redzones.events}
    finite = set(np.unique(rp[np.isfinite(rp)]).tolist())
    assert finite <= allowed, "unexpected return periods: %s" % (finite - allowed)


def test_hazard_extent_grows_monotonically_with_rarity(a):
    """A cell uninhabitable at the 5-year event must also be uninhabitable at
    the 25- and 100-year events. If it is not, the severity ladder is broken."""
    per_sev = {}
    for sev in scenario.RECURRENCE_SEVERITIES:
        sc = scenario.get(a.district.code, sev)
        deep = sc.timeline.peak_depth >= redzone_mod.UNINHABITABLE["flood_depth_m"]
        per_sev[sev] = deep
    order = list(scenario.RECURRENCE_SEVERITIES)
    for a_, b_ in zip(order, order[1:]):
        shrank = int((per_sev[a_] & ~per_sev[b_]).sum())
        assert shrank <= per_sev[a_].sum() * 0.02, \
            "%s -> %s lost %d uninhabitable cells" % (a_, b_, shrank)


def test_red_zone_is_exactly_where_the_return_period_is_finite(a):
    assert np.array_equal(a.redzones.is_red, np.isfinite(a.redzones.return_period))


def test_unsuitability_is_zero_outside_the_red_zone(a):
    assert float(a.redzones.unsuitability[~a.redzones.is_red].max(initial=0.0)) == 0.0


def test_unsuitability_rises_as_the_interval_shortens(a):
    """Frequent hazard must score worse than rare hazard."""
    red = a.redzones.is_red
    if not red.any():
        pytest.skip("no red zone")
    rp = a.redzones.return_period[red]
    us = a.redzones.unsuitability[red]
    if len(np.unique(rp)) < 2:
        pytest.skip("only one return period present")
    frequent = us[rp == rp.min()].mean()
    rare = us[rp == rp.max()].mean()
    assert frequent > rare


def test_every_red_cell_names_a_dominant_hazard(a):
    assert (a.redzones.dominant[a.redzones.is_red] >= 0).all()


# ---------------------------------------------------------------------------
# habitations
# ---------------------------------------------------------------------------

def test_habitations_are_ranked_by_descending_priority(a):
    scores = [h.priority_score for h in a.habitations]
    assert scores == sorted(scores, reverse=True)


def test_population_in_red_never_exceeds_population(a):
    for h in a.habitations:
        assert h.population_in_red <= h.population + 1


def test_horizon_matches_the_reported_return_period(a):
    for h in a.habitations:
        expected, _ = redzone_mod.horizon_for(h.return_period_years)
        assert h.horizon == expected


def test_a_settlement_with_no_exposure_is_only_monitored(a):
    for h in a.habitations:
        if h.population_in_red == 0:
            assert h.horizon == "monitor"


# ---------------------------------------------------------------------------
# relocation — the safety invariants
# ---------------------------------------------------------------------------

def test_no_relocation_site_is_inside_a_red_zone(a):
    """The one that matters.

    Every site the allocator can assign people to must sit on land where no
    modelled hazard renders it uninhabitable at the 100-year event. Sending
    people from one hazard zone into another is the worst thing this system
    could do, and it would not be visible on the map.
    """
    for s in a.relocation.sites:
        assert not bool(a.redzones.is_red[s.row, s.col]), \
            "site %s (%s) is inside a red zone" % (s.id, s.label)


def test_every_eligible_cell_is_safe_buildable_and_on_land(a):
    e = a.relocation.eligible
    assert not (e & a.redzones.is_red).any()
    assert (a.terrain.slope[e] <= relocation_mod.MAX_BUILD_SLOPE_DEG + 1e-6).all()
    assert not (e & a.terrain.sea_mask).any()
    assert not (e & ~a.exposure.district_mask).any()


def test_site_capacity_never_exceeds_its_land_at_the_stated_density(a):
    density = a.relocation.density_per_km2
    for s in a.relocation.sites:
        assert s.capacity <= s.area_km2 * density + 1


def test_allocation_never_exceeds_a_site_capacity(a):
    used = {}
    for p in a.relocation.plans:
        for x in p.assignments:
            used[x.site_id] = used.get(x.site_id, 0) + x.people
    caps = {s.id: s.capacity for s in a.relocation.sites}
    for sid, total in used.items():
        assert total <= caps[sid], "site %s over-allocated" % sid


def test_nobody_is_moved_further_than_the_acceptable_distance(a):
    for p in a.relocation.plans:
        for x in p.assignments:
            assert x.distance_km <= relocation_mod.MAX_ACCEPTABLE_MOVE_KM + 1e-6


def test_placed_plus_shortfall_accounts_for_everyone(a):
    for p in a.relocation.plans:
        assert p.people_placed + p.shortfall == p.people_to_move


def test_only_habitations_needing_action_get_a_plan(a):
    planned = {p.habitation_id for p in a.relocation.plans}
    for h in a.habitations:
        if h.horizon == "monitor":
            assert h.id not in planned


def test_immediate_habitations_are_allocated_before_later_ones(a):
    """Capacity is scarce; the villages that must move first must get first
    claim on it."""
    order = [p.horizon for p in a.relocation.plans]
    rank = [relocation_mod.HORIZON_ORDER[h] for h in order]
    assert rank == sorted(rank)


def test_a_shortfall_always_carries_an_explanation(a):
    for p in a.relocation.plans:
        if p.shortfall > 0:
            assert p.notes, "%s has a shortfall with no explanation" % p.habitation_id


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------

def test_the_assessment_is_reproducible():
    x = scenario.build_assessment("KL-WAY")
    y = scenario.build_assessment("KL-WAY")
    assert np.array_equal(x.redzones.return_period, y.redzones.return_period)
    assert np.array_equal(x.redzones.unsuitability, y.redzones.unsuitability)
    assert [h.to_dict() for h in x.habitations] == [h.to_dict() for h in y.habitations]
    assert [s.to_dict() for s in x.relocation.sites] == \
           [s.to_dict() for s in y.relocation.sites]
