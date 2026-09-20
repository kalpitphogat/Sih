"""Phase 8 tests: the GEE module is honest about being unconfigured."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from floodguard.data import gee

client = TestClient(app)


def test_status_reports_the_truth_about_this_machine():
    s = gee.status()
    assert set(s) >= {"configured", "package_installed", "credentials_present", "detail"}
    assert s["configured"] == (s["package_installed"] and s["credentials_present"])
    assert len(s["detail"]) > 20, "the detail must explain, not just flag"


def test_initialise_raises_with_a_reason_when_unconfigured():
    if gee.is_configured():
        pytest.skip("Earth Engine is configured on this machine")
    with pytest.raises(gee.GEEUnavailable) as excinfo:
        gee.initialise()
    message = str(excinfo.value).lower()
    assert "earthengine-api" in message or "credentials" in message


def test_detect_flood_refuses_rather_than_returning_a_cached_extent():
    """The central honesty property: no fake live feed."""
    if gee.is_configured():
        pytest.skip("Earth Engine is configured on this machine")
    with pytest.raises(gee.GEEUnavailable):
        gee.detect_flood((78.1, 29.9, 78.9, 30.7), "2024-08-01", "2024-08-10")


def test_monitoring_status_endpoint():
    r = client.get("/api/monitoring/status")
    assert r.status_code == 200
    body = r.json()
    assert "configured" in body
    assert body["collections"]["sentinel1"] == "COPERNICUS/S1_GRD"


def test_analyze_returns_503_not_500_when_unconfigured():
    """503 says the capability is absent; 500 would say the server is broken."""
    if gee.is_configured():
        pytest.skip("Earth Engine is configured on this machine")
    r = client.post(
        "/api/monitoring/analyze",
        json={"bbox": [78.1, 29.9, 78.9, 30.7], "post_start": "2024-08-01",
              "post_end": "2024-08-10"},
    )
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["error"] == "earth_engine_unavailable"
    assert "status" in detail


def test_permanent_water_threshold_is_stated():
    assert gee.PERMANENT_WATER_OCCURRENCE == 50


def test_collection_ids_are_pinned():
    """Pinned so a silent upstream rename shows up as a test failure."""
    assert gee.S1_GRD == "COPERNICUS/S1_GRD"
    assert gee.JRC_GSW.startswith("JRC/GSW")
    assert gee.CHIRPS == "UCSB-CHG/CHIRPS/DAILY"


def test_agreement_reports_not_computed_without_a_vectorised_extent(tmp_path):
    detection = gee.FloodDetection(
        aoi=(0, 0, 1, 1),
        pre_window=("2024-01-01", "2024-01-15"),
        post_window=("2024-02-01", "2024-02-10"),
        polarisation="VV",
        threshold_db=-15.0,
        threshold_method="test",
        flooded_area_km2=1.0,
        permanent_water_area_km2=0.5,
        scene_counts={"pre": 2, "post": 1},
        geojson=None,
    )
    result = gee.agreement_with_simulation(detection, tmp_path / "nope.tif")
    assert result["computed"] is False
    assert "reason" in result
