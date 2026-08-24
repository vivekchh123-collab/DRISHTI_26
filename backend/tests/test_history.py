"""Disaster history: register integrity, track parsing, and validation.

No network. The IBTrACS parser is exercised against an inline fixture in exactly
the CSV shape NOAA publishes, including the awkward part — row 1 is a *units*
row, not data, and reading it as a record yields a storm at latitude
"degrees_north".
"""

import numpy as np
import pytest

from app.core import history
from app.data import disasters as disasters_data
from app.providers import ibtracs


# ---------------------------------------------------------------------------
# the curated register
# ---------------------------------------------------------------------------

def test_every_event_carries_a_citation():
    """The rule the register exists under.

    A hand-entered casualty figure without a source is indistinguishable from an
    invented one, and this file is the only place in the system where numbers
    are typed in by a person rather than computed.
    """
    for e in disasters_data.EVENTS:
        assert e.source, "%s has no source" % e.id
        assert e.source_url.startswith("http"), "%s has no source URL" % e.id


def test_event_ids_are_unique():
    ids = [e.id for e in disasters_data.EVENTS]
    assert len(ids) == len(set(ids))


def test_dates_are_ordered_and_well_formed():
    for e in disasters_data.EVENTS:
        assert len(e.start) == 10 and len(e.end) == 10
        assert e.end >= e.start, "%s ends before it starts" % e.id
        assert e.duration_days >= 1


def test_coordinates_are_inside_india():
    for e in disasters_data.EVENTS:
        assert 6.0 <= e.lat <= 37.0, "%s latitude outside India" % e.id
        assert 68.0 <= e.lon <= 98.0, "%s longitude outside India" % e.id


def test_district_codes_reference_real_districts():
    from app.data import districts as districts_data
    known = {d.code for d in districts_data.DISTRICTS}
    for e in disasters_data.EVENTS:
        for code in e.districts:
            assert code in known, "%s references unknown district %s" % (e.id, code)


def test_casualty_figures_are_plausible():
    for e in disasters_data.EVENTS:
        if e.deaths is not None:
            assert 0 <= e.deaths <= 100000, "%s implausible toll" % e.id
        if e.affected is not None and e.deaths is not None:
            assert e.affected >= e.deaths, "%s: fewer affected than dead" % e.id


def test_coverage_declares_why_it_is_curated():
    c = disasters_data.coverage()
    assert c["events"] == len(disasters_data.EVENTS)
    assert "no free api" in c["why_curated"].lower()
    assert "vary" in c["caveat"].lower()


def test_lookup_helpers():
    assert disasters_data.for_district("KL-WAY")
    assert disasters_data.for_state("Assam")
    assert disasters_data.in_window("2020-01-01", "2020-12-31")
    assert disasters_data.for_district("NOT-A-DISTRICT") == []


# ---------------------------------------------------------------------------
# IBTrACS parsing
# ---------------------------------------------------------------------------

FIXTURE = "\n".join([
    "SID,SEASON,NUMBER,BASIN,NAME,ISO_TIME,LAT,LON,WMO_WIND,WMO_PRES,DIST2LAND",
    ",year,,,,,degrees_north,degrees_east,kts,mb,km",
    "2019X,2019,1,NI,FANI,2019-05-02 06:00:00,15.0,86.0,110,940,220",
    "2019X,2019,1,NI,FANI,2019-05-03 00:00:00,18.0,86.2,115,932,60",
    "2019X,2019,1,NI,FANI,2019-05-03 12:00:00,19.8,85.8,100,950,0",
    "2019X,2019,1,NI,FANI,2019-05-04 00:00:00,21.5,86.5,70,975,-10",
    "1985Y,1985,2,NI,OLD,1985-06-01 00:00:00,16.0,88.0,40,995,300",
    "1985Y,1985,2,NI,OLD,1985-06-01 12:00:00,17.0,88.5,45,990,250",
    "1985Y,1985,2,NI,OLD,1985-06-02 00:00:00,18.0,89.0,50,985,200",
    "1985Y,1985,2,NI,OLD,1985-06-02 12:00:00,19.0,89.5,45,990,180",
    "2020Z,2020,3,NI,FARAWAY,2020-01-01 00:00:00,2.0,60.0,50,990,500",
    "2020Z,2020,3,NI,FARAWAY,2020-01-01 12:00:00,2.5,60.5,55,985,480",
    "2020Z,2020,3,NI,FARAWAY,2020-01-02 00:00:00,3.0,61.0,50,990,470",
    "2020Z,2020,3,NI,FARAWAY,2020-01-02 12:00:00,3.5,61.5,45,995,460",
])


def test_units_row_is_not_parsed_as_a_storm():
    """Row 1 of an IBTrACS CSV is units, not data."""
    tracks = ibtracs.parse_csv(FIXTURE)
    for t in tracks:
        for p in t.points:
            assert isinstance(p.lat, float)
            assert -90 <= p.lat <= 90


def test_parses_a_track_with_its_intensity():
    tracks = ibtracs.parse_csv(FIXTURE)
    fani = [t for t in tracks if t.name == "FANI"]
    assert len(fani) == 1
    t = fani[0]
    assert t.season == 2019
    assert len(t.points) == 4
    assert t.peak_wind_kt == 115
    assert t.start == "2019-05-02" and t.end == "2019-05-04"


def test_landfall_detected_from_distance_to_land():
    t = [x for x in ibtracs.parse_csv(FIXTURE) if x.name == "FANI"][0]
    assert t.made_landfall is True


def test_storms_before_the_earliest_season_are_dropped():
    names = {t.name for t in ibtracs.parse_csv(FIXTURE)}
    assert "OLD" not in names, "1985 storm kept despite the season floor"


def test_storms_outside_the_india_window_are_dropped():
    names = {t.name for t in ibtracs.parse_csv(FIXTURE)}
    assert "FARAWAY" not in names


def test_imd_categories_are_ordered():
    winds = [20, 40, 55, 75, 100, 130]
    names = [ibtracs.category_for(w) for w in winds]
    assert names[0] == "depression"
    assert names[-1] == "super cyclonic storm"
    assert len(set(names)) == len(names), "categories collapse"


def test_parser_survives_a_truncated_file():
    assert ibtracs.parse_csv("") == []
    assert ibtracs.parse_csv("SID,SEASON\n,year") == []


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_spearman_matches_a_hand_computable_case():
    assert history._spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert history._spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert history._spearman([1, 2], [1, 2]) is None       # too few points


def test_spearman_handles_ties():
    rho = history._spearman([1, 1, 2, 2], [1, 1, 2, 2])
    assert rho is not None and rho == pytest.approx(1.0)


def test_validation_reports_its_sample_size_and_baseline():
    """A percentile without its N is not a result."""
    res = history.validate_district("KL-WAY")
    assert res["chance_baseline_percentile"] == 50.0
    assert res["point_events_tested"] >= 1
    assert "caveat" in res and "conclusive" in res["caveat"]
    assert "why_hit_rate_is_not_the_measure" in res


def test_validation_percentiles_are_percentages():
    res = history.validate_district("KL-WAY")
    for v in res["verdicts"]:
        p = v["susceptibility_percentile"]
        if p is not None:
            assert 0.0 <= p <= 100.0


def test_wayanad_2024_is_rated_above_chance():
    """The one event we can check hardest, and the reason the fix mattered.

    The 2024 Chooralmala-Mundakkai slide happened on a steep escarpment that a
    340 m grid averaged into a plateau. After moving hill districts to a finer
    grid and measuring slope at the DEM's native 30 m, the model should rate
    that exact location well above the district background. If this regresses,
    the scale fix has been undone.
    """
    res = history.validate_district("KL-WAY")
    v = [x for x in res["verdicts"] if x["event_id"] == "LS-KL-2024"]
    assert v, "Wayanad 2024 missing from the register"
    pct = v[0]["susceptibility_percentile"]
    assert pct is not None and pct > 80.0, (
        "Wayanad 2024 rated at the %.1f percentile; the escarpment is being "
        "averaged away again" % pct)


def test_timeline_carries_both_provenances():
    t = history.timeline("2018-01-01", "2026-12-31")
    assert t["events"] and all(e["provenance"] == "curated-cited"
                               for e in t["events"])
    assert "curated" in t["sources"]
    for row in t["counts_per_year"]:
        assert row["count"] >= 1
