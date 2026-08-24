"""Mitigation planning: strategy choice, accountability, and costing.

The rules are declarative predicates over a flat dict, so almost everything here
runs without touching the hazard model at all. That is the point of having built
them that way: the doctrine can be tested independently of the physics.

The test that matters most is
:func:`test_relocation_is_not_the_default_answer`. A system that recommends
moving every exposed village would be both naive and unaffordable, and it is the
failure mode a reviewer would spot immediately.
"""

import pytest

from app.core import mitigation
from app.data import mitigations as mit


def facts(**kw) -> dict:
    """A habitation record with sensible defaults."""
    base = {
        "id": "H1", "label": "test habitation",
        "population_in_red_zone": 500,
        "return_period_years": 25.0,
        "dominant_hazard": "flood",
        "relocation_horizon": "short-term",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# the rule table itself
# ---------------------------------------------------------------------------

def test_every_measure_names_a_real_authority():
    for r in mit.RULES:
        assert r.lead in mit.BY_ID, "%s has an unknown lead %s" % (r.id, r.lead)
        for s in r.supporting:
            assert s in mit.BY_ID, "%s has an unknown supporter %s" % (r.id, s)


def test_every_measure_cites_a_real_instrument():
    """An instruction that cites no statute cannot survive an audit."""
    for r in mit.RULES:
        assert r.instrument in mit.INSTRUMENTS, (
            "%s cites unknown instrument %s" % (r.id, r.instrument))


def test_measure_ids_are_unique():
    ids = [r.id for r in mit.RULES]
    assert len(ids) == len(set(ids))


def test_families_are_known():
    for r in mit.RULES:
        assert r.family in mit.families()


def test_authority_levels_are_valid():
    for a in mit.AUTHORITIES:
        assert a.level in (mit.NATIONAL, mit.STATE, mit.DISTRICT, mit.LOCAL)
        assert a.remit


def test_a_broken_predicate_costs_one_measure_not_the_page():
    """A missing fact must not take down an officer's whole plan."""
    out = mit.rules_for({})            # every predicate sees an empty dict
    assert isinstance(out, list)


# ---------------------------------------------------------------------------
# strategy — the doctrine
# ---------------------------------------------------------------------------

def test_relocation_is_not_the_default_answer():
    """NDMA treats relocation as the measure of last resort.

    A hazard that recurs on a generational timescale should be answered with
    protection, because an embankment outlives it and moving several hundred
    people does not become cheaper for having been recommended by software.
    """
    strategy, reason = mitigation.strategy_for(
        facts(return_period_years=25.0, population_in_red_zone=500))
    assert strategy == "protect"
    assert "service life" in reason


def test_frequent_hazard_triggers_relocation():
    strategy, reason = mitigation.strategy_for(
        facts(return_period_years=5.0, relocation_horizon="immediate"))
    assert strategy == "relocate"
    assert "service life" in reason


def test_rare_hazard_is_regulated_not_relocated():
    strategy, _ = mitigation.strategy_for(
        facts(return_period_years=90.0, relocation_horizon="medium-term"))
    assert strategy == "regulate"


def test_erosion_always_relocates():
    """Erosion removes the land itself; protection cannot restore ground."""
    strategy, reason = mitigation.strategy_for(
        facts(dominant_hazard="erosion", return_period_years=60.0))
    assert strategy == "relocate"
    assert "land itself" in reason


def test_uninhabited_red_zone_is_regulated():
    strategy, _ = mitigation.strategy_for(
        facts(population_in_red_zone=0, return_period_years=5.0))
    assert strategy == "regulate"


def test_no_modelled_hazard_means_monitor():
    strategy, _ = mitigation.strategy_for(
        facts(return_period_years=None, relocation_horizon="monitor"))
    assert strategy == "monitor"


def test_strategy_reason_is_always_given():
    for rp in (3.0, 12.0, 25.0, 60.0, None):
        _, reason = mitigation.strategy_for(facts(return_period_years=rp))
        assert reason and len(reason) > 20, "strategy given without a reason"


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------

def test_relocation_plan_covers_consent_entitlement_and_the_vacated_land():
    """The three things that make Indian resettlement fail when skipped."""
    plan = mitigation.plan_for(
        facts(return_period_years=5.0, relocation_horizon="immediate"))
    ids = {r.id for r in plan.measures}
    assert "REL-SIA" in ids, "no Gram Sabha consent step"
    assert "REL-ENTITLE" in ids, "no entitlement computation"
    assert "REL-VACATE" in ids, "vacated land not barred from re-occupation"


def test_protect_plan_contains_no_relocation_measures():
    plan = mitigation.plan_for(
        facts(return_period_years=25.0, relocation_horizon="short-term"))
    assert plan.strategy == "protect"
    assert all(r.family != mit.RELOCATION for r in plan.measures)


def test_measures_are_ordered_by_urgency():
    plan = mitigation.plan_for(
        facts(return_period_years=5.0, relocation_horizon="immediate"))
    order = {"immediate": 0, "short-term": 1, "medium-term": 2, "ongoing": 3}
    seq = [order.get(r.horizon, 9) for r in plan.measures]
    assert seq == sorted(seq)


def test_every_plan_has_at_least_one_accountable_authority():
    for rp, hz in [(5.0, "immediate"), (25.0, "short-term"), (90.0, "medium-term")]:
        plan = mitigation.plan_for(
            facts(return_period_years=rp, relocation_horizon=hz))
        d = plan.to_dict()
        assert d["authorities"], "plan with no accountable authority"
        assert any(a["leads"] for a in d["authorities"])


def test_landslide_plan_reaches_gsi():
    plan = mitigation.plan_for(
        facts(dominant_hazard="landslide", return_period_years=25.0,
              relocation_horizon="short-term"))
    leads = {r.lead for r in plan.measures}
    assert "GSI" in leads, "slope investigation not assigned to GSI"


def test_families_derived_from_population():
    plan = mitigation.plan_for(facts(population_in_red_zone=480))
    assert plan.families == pytest.approx(100, abs=2)


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------

def test_costs_declare_their_own_imprecision():
    """False precision is worse than an honest band, because it gets quoted."""
    plan = mitigation.plan_for(
        facts(return_period_years=5.0, relocation_horizon="immediate"))
    c = plan.to_dict()["cost_estimate"]
    assert c["low_inr"] < c["high_inr"]
    assert "not a tender estimate" in c["precision"].lower()


def test_relocation_costs_more_than_regulation():
    reloc = mitigation.plan_for(
        facts(return_period_years=5.0, relocation_horizon="immediate"))
    reg = mitigation.plan_for(
        facts(return_period_years=90.0, relocation_horizon="medium-term"))
    assert reloc.cost.high_inr > reg.cost.high_inr * 10, (
        "relocation is not being costed as the expensive option it is")


def test_cost_scales_with_population():
    small = mitigation.plan_for(
        facts(population_in_red_zone=100, return_period_years=5.0,
              relocation_horizon="immediate"))
    large = mitigation.plan_for(
        facts(population_in_red_zone=2000, return_period_years=5.0,
              relocation_horizon="immediate"))
    assert large.cost.low_inr > small.cost.low_inr * 5


def test_district_summary_states_the_doctrine():
    plans = [mitigation.plan_for(facts(return_period_years=rp,
                                       relocation_horizon=hz))
             for rp, hz in [(5.0, "immediate"), (25.0, "short-term"),
                            (90.0, "medium-term")]]
    s = mitigation.district_summary(plans)
    assert s["habitations_planned"] == 3
    assert "last resort" in s["doctrine"]
    assert s["by_strategy"].get("relocate") == 1
    assert s["by_strategy"].get("protect") == 1
