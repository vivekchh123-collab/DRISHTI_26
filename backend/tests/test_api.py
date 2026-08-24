"""API contract.

The front end is written against these shapes, so a change that breaks them
breaks the dashboard silently. Provenance in particular is asserted here: a
response that forgets to say the data is modelled is a correctness bug, not a
cosmetic one.
"""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
CODE = "BR-DAR"


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["districts"] == 22


def test_methods_table_is_complete():
    r = client.get("/api/methods")
    assert r.status_code == 200
    methods = r.json()["methods"]
    assert len(methods) >= 12
    for m in methods:
        assert m["source"].strip(), "%s has no citation" % m["step"]
        assert m["limitation"].strip(), "%s has no stated limitation" % m["step"]


def test_district_list():
    r = client.get("/api/districts")
    assert r.status_code == 200
    body = r.json()
    assert len(body["districts"]) == 22
    for d in body["districts"]:
        assert {"code", "name", "state", "lat", "lon", "risk_score"} <= set(d)


def test_district_detail_declares_its_provenance():
    r = client.get("/api/districts/%s" % CODE)
    assert r.status_code == 200
    body = r.json()
    prov = body["provenance"]
    assert prov["data_mode"] in ("demo", "live")
    assert prov["boundary_source"]
    assert prov["dem_source"]
    assert "modelled" in prov["note"].lower()
    assert body["boundary"]["geometry"]["type"] == "Polygon"


def test_assessment_shape():
    r = client.get("/api/districts/%s/assessment" % CODE)
    assert r.status_code == 200
    body = r.json()
    zones = body["impact"]["zones"]
    assert zones and zones[0]["rank"] == 1
    for z in zones:
        assert set(z["score_parts"]) == {
            "population", "depth", "duration", "isolation", "vulnerability"}
    assert body["action_cards"]
    card = body["action_cards"][0]
    assert card["zone_id"] == zones[0]["id"]
    assert card["alert_text"]
    assert card["resources"]
    assert body["score_weights"]["weights"]


def test_timeline_series_are_all_the_same_length():
    body = client.get("/api/districts/%s/timeline" % CODE).json()
    n = body["hours"]
    for key in ("area_km2", "population_affected", "rainfall_mm", "max_depth_m"):
        assert len(body[key]) == n, "%s has the wrong length" % key
    assert 0 <= body["peak_hour"] < n
    assert all(v >= 0 for v in body["population_affected"])


def test_waterlogging_hotspots_explain_themselves():
    body = client.get("/api/districts/%s/waterlogging" % CODE).json()
    assert body["weights"]
    for h in body["hotspots"]:
        assert h["drivers"], "hotspot %s gives no reason" % h["id"]
        assert h["drain_down_hours"] >= 0


@pytest.mark.parametrize(
    "layer", ["basemap", "depth", "hand", "wetness", "population", "duration"])
def test_every_layer_returns_a_real_png(layer):
    r = client.get("/api/districts/%s/layers/%s.png" % (CODE, layer))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG signature"
    assert len(r.content) > 500


def test_layer_index_carries_bounds_and_legends():
    body = client.get("/api/districts/%s/layers" % CODE).json()
    (s, w), (n, e) = body["bounds"]
    assert s < n and w < e
    depth = next(l for l in body["layers"] if l["name"] == "depth")
    assert len(depth["legend"]) >= 4


def test_zone_detail_matches_the_assessment():
    zones = client.get("/api/districts/%s/assessment" % CODE).json()["impact"]["zones"]
    zid = zones[0]["id"]
    body = client.get("/api/districts/%s/zones/%s" % (CODE, zid)).json()
    assert body["zone"]["id"] == zid
    assert body["action_card"]["zone_id"] == zid


def test_unknown_district_is_a_404():
    assert client.get("/api/districts/ZZ-NOPE").status_code == 404


def test_unknown_zone_is_a_404():
    assert client.get("/api/districts/%s/zones/Z99" % CODE).status_code == 404


def test_bad_severity_is_rejected():
    assert client.get("/api/districts/%s?severity=apocalyptic" % CODE).status_code == 400


def test_unknown_layer_is_a_404():
    assert client.get("/api/districts/%s/layers/nope.png" % CODE).status_code == 404


def test_frontend_is_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "DRISHTI" in r.text


# ---------------------------------------------------------------------------
# red zones and relocation  (SIH26191)
# ---------------------------------------------------------------------------

def test_redzones_endpoint_declares_its_method_and_provenance():
    r = client.get("/api/districts/%s/redzones" % CODE)
    assert r.status_code == 200
    body = r.json()
    rz = body["red_zones"]
    assert rz["events_modelled"], "must say which events it was built from"
    assert set(rz["by_horizon_km2"]) == {
        "immediate", "short-term", "medium-term", "monitor"}
    # Bound to the module rather than to a literal, so adding a hazard updates
    # the contract instead of breaking an otherwise unrelated API test.
    from app.core.redzone import HAZARDS
    assert set(rz["by_dominant_hazard_km2"]) == set(HAZARDS)
    assert "cloudburst" in HAZARDS, "the problem statement names cloudbursts"
    assert body["provenance"]["data_mode"] in ("demo", "live")
    assert body["landslide"]["standard"].startswith("BIS IS 14496")


def test_habitations_are_ranked_and_carry_a_horizon():
    body = client.get("/api/districts/%s/habitations" % CODE).json()
    habs = body["habitations"]
    assert habs and habs[0]["rank"] == 1
    assert body["priority_weights"]
    valid = {h["name"] for h in body["horizons"]}
    for h in habs:
        assert h["relocation_horizon"] in valid
        assert h["population_in_red_zone"] <= h["population"] + 1


def test_relocation_reports_capacity_and_the_binding_constraint():
    body = client.get("/api/districts/%s/relocation" % CODE).json()
    assert body["sites"]
    assert body["binding_constraint"]
    assert body["district_headroom"] >= 0
    assert body["people_placeable"] + body["unplaced"] == body["people_to_relocate"]
    for p in body["plans"]:
        assert p["people_placed"] + p["shortfall"] == p["people_to_move"]


@pytest.mark.parametrize(
    "layer", ["redzone", "recurrence", "landslide", "erosion", "suitability"])
def test_every_assessment_layer_returns_a_real_png(layer):
    r = client.get("/api/districts/%s/assessment-layers/%s.png" % (CODE, layer))
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_assessment_layer_index_has_legends():
    body = client.get("/api/districts/%s/assessment-layers" % CODE).json()
    assert len(body["layers"]) == 5
    for l in body["layers"]:
        assert l["legend"] and len(l["legend"]) >= 4


def test_redzone_endpoints_reject_an_unknown_district():
    for path in ("redzones", "habitations", "relocation"):
        assert client.get("/api/districts/ZZ-NOPE/%s" % path).status_code == 404
