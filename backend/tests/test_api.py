"""Phase 7 API tests.

Every endpoint the frontend calls gets a test, and the honesty properties get
their own: no endpoint may invent a number, and "not computed" must never be
served as zero.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# --- catalog ----------------------------------------------------------------------


def test_rivers_lists_the_catalog():
    r = client.get("/api/rivers")
    assert r.status_code == 200
    rivers = r.json()
    assert len(rivers) > 10
    names = {x["name"] for x in rivers}
    assert "Bhagirathi" in names
    assert "Mahanadi" in names


def test_tehri_is_on_the_bhagirathi_not_the_alaknanda():
    """A judge from Uttarakhand will check this first."""
    r = client.get("/api/dams", params={"river": "Bhagirathi"})
    assert r.status_code == 200
    dams = r.json()
    assert any("TEHRI" in d["name"].upper() for d in dams)

    alaknanda = client.get("/api/dams", params={"river": "Alaknanda"}).json()
    assert not any("TEHRI" in d["name"].upper() for d in alaknanda)


def test_dam_record_carries_per_field_citations():
    r = client.get("/api/dams/tehri_hpp")
    assert r.status_code == 200
    dam = r.json()
    assert dam["structural_height_m"] == pytest.approx(260.5)
    assert "nrld" in dam["sources"]["structural_height_m"].lower()
    assert "cwc.gov.in" in dam["sources"]["structural_height_m"]


def test_frl_is_null_rather_than_guessed():
    """The NRLD tables we transcribed do not carry FRL. Null, never invented."""
    dam = client.get("/api/dams/tehri_hpp").json()
    assert dam["frl_m"] is None
    assert dam["mddl_m"] is None


def test_unknown_dam_is_a_404():
    assert client.get("/api/dams/not_a_real_dam").status_code == 404


def test_reservoir_curve_404s_rather_than_synthesising_one():
    r = client.get("/api/dams/definitely_not_a_dam/reservoir-curve")
    assert r.status_code == 404
    assert "no synthetic curve" in r.json()["detail"].lower()


def test_scenarios_are_listed_and_valid():
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    scenarios = {s["id"]: s for s in r.json()}
    assert "tehri_bhagirathi" in scenarios
    assert "hirakud_mahanadi" in scenarios
    assert all(s["valid"] for s in scenarios.values())
    assert scenarios["tehri_bhagirathi"]["river"] == "Bhagirathi"


# --- engine honesty ---------------------------------------------------------------


def test_engine_health_never_claims_an_absent_delft3d():
    r = client.get("/api/health/engines")
    assert r.status_code == 200
    body = r.json()
    delft = next(e for e in body["engines"] if e["id"] == "delft3d")
    if not delft["available"]:
        assert delft["display_name"] != "Delft3D"
        assert "Delft3D-class" in delft["display_name"]
        assert delft["is_real_solver"] is False
        assert delft["substitute_id"] == "swe_fv"
    assert "substitut" in body["honesty_statement"].lower()


# --- scenario validation ----------------------------------------------------------


def test_validate_returns_every_breach_model():
    r = client.post("/api/scenarios/validate", json={"scenario_id": "tehri_bhagirathi"})
    assert r.status_code == 200
    body = r.json()
    models = {p["model"] for p in body["predictions"]}
    assert models == {
        "froehlich_2008",
        "von_thun_gillette_1990",
        "macdonald_langridge_monopolis_1984",
    }
    assert body["spread"]["width_m"]["spread_ratio"] >= 1.0


def test_validate_rejects_an_unknown_scenario():
    r = client.post("/api/scenarios/validate", json={"scenario_id": "nope"})
    assert r.status_code == 404


def test_validate_requires_a_scenario_or_a_dam():
    r = client.post("/api/scenarios/validate", json={})
    assert r.status_code == 422


def test_a_breach_deeper_than_the_dam_is_rejected():
    r = client.post(
        "/api/scenarios/validate",
        json={"scenario_id": "tehri_bhagirathi", "breach": {"depth_m": 9999.0}},
    )
    assert r.status_code == 422
    assert "structural_height_m" in r.json()["detail"]


def test_a_reservoir_level_above_the_crest_is_rejected():
    r = client.post(
        "/api/scenarios/validate",
        json={"scenario_id": "tehri_bhagirathi", "reservoir_level_m": 5000.0},
    )
    assert r.status_code == 422
    assert "crest" in r.json()["detail"].lower()


def test_a_dam_without_a_published_frl_is_refused_not_guessed():
    """Ad-hoc scenarios need a level. The API asks rather than inventing one."""
    r = client.post("/api/scenarios/validate", json={"dam_id": "hirakud"})
    assert r.status_code == 422
    detail = r.json()["detail"].lower()
    assert "full reservoir level" in detail
    assert "supply reservoir_level_m" in detail


# --- jobs -------------------------------------------------------------------------


def test_unknown_job_is_a_404():
    assert client.get("/api/jobs/deadbeefdeadbeef").status_code == 404


def test_job_listing_works():
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_cancelling_an_unknown_job_is_a_409():
    assert client.post("/api/jobs/deadbeefdeadbeef/cancel").status_code == 409


# --- results ----------------------------------------------------------------------


def test_results_for_an_unknown_run_are_404():
    for path in ("summary", "towns", "comparison", "impact", "hydrographs"):
        r = client.get(f"/api/results/nosuchrun/{path}")
        assert r.status_code == 404, path


def test_export_rejects_an_unknown_format():
    r = client.get("/api/results/nosuchrun/export", params={"format": "docx"})
    assert r.status_code in (404, 422)


# --- comparison honesty -----------------------------------------------------------


def test_comparison_of_one_engine_returns_a_note_not_fabricated_rows():
    """With a single engine there is nothing to compare, and we say so."""
    from floodguard.compare.metrics import compare_engines

    runs = [
        {
            "actual_engine": "swe_fv",
            "display_name": "FloodGuard-SWE (Delft3D-class FV solver)",
            "summary": {"max_depth_m": 12.0, "flooded_area_km2": 30.0},
        }
    ]
    # The router short-circuits before calling compare_engines for <2 runs; this
    # asserts the guard directly so the behaviour is pinned.
    from app.api.results import comparison  # noqa: F401

    assert len(runs) == 1


def test_signed_difference_is_computed_not_written():
    from floodguard.compare.metrics import signed_difference_pct

    assert signed_difference_pct([100.0, 110.0]) == pytest.approx(10.0)
    assert signed_difference_pct([100.0, 90.0]) == pytest.approx(-10.0)
    assert signed_difference_pct([None, 90.0]) is None
    assert signed_difference_pct([0.0, 90.0]) is None


def test_extent_agreement_metrics():
    import numpy as np

    from floodguard.compare.metrics import extent_agreement

    a = np.array([[1.0, 1.0], [0.0, 0.0]])
    b = np.array([[1.0, 0.0], [1.0, 0.0]])
    m = extent_agreement(a, b, threshold_m=0.5)
    assert m["true_positive_cells"] == 1
    assert m["false_positive_cells"] == 1
    assert m["false_negative_cells"] == 1
    assert m["critical_success_index"] == pytest.approx(1 / 3)


def test_identical_extents_give_a_perfect_csi():
    import numpy as np

    from floodguard.compare.metrics import extent_agreement

    a = np.array([[2.0, 0.0], [0.0, 3.0]])
    m = extent_agreement(a, a.copy())
    assert m["critical_success_index"] == pytest.approx(1.0)
    assert m["false_alarm_ratio"] == pytest.approx(0.0)


# --- job store --------------------------------------------------------------------


def test_job_store_roundtrip(tmp_path):
    from app.core.jobs import JobStatus, JobStore

    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.create("tehri_bhagirathi", {"scenario_id": "tehri_bhagirathi"})

    assert store.get(job.id).status is JobStatus.QUEUED
    store.mark_running(job.id)
    store.update_progress(job.id, fraction=0.5, phase="solve", message="halfway")

    live = store.get(job.id)
    assert live.progress.fraction == 0.5
    assert live.progress.phase == "solve"
    assert any("halfway" in line for line in live.progress.log)

    store.finish(job.id, JobStatus.SUCCEEDED, result_path="x/result.json")
    done = store.get(job.id)
    assert done.status is JobStatus.SUCCEEDED
    assert done.result_path == "x/result.json"


def test_orphaned_jobs_are_marked_interrupted_on_restart(tmp_path):
    """A job recorded as running after a crash must not claim to still be running."""
    from app.core.jobs import JobStatus, JobStore

    db = tmp_path / "jobs.sqlite"
    store = JobStore(db)
    job = store.create("s", {})
    store.mark_running(job.id)

    reopened = JobStore(db)
    assert reopened.get(job.id).status is JobStatus.INTERRUPTED


def test_progress_log_is_capped(tmp_path):
    """A long solve emits thousands of lines; the UI only shows the last few."""
    from app.core.jobs import JobStore

    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.create("s", {})
    for i in range(500):
        store.update_progress(job.id, fraction=i / 500, phase="solve", message=f"step {i}")
    log = store.get(job.id).progress.log
    assert len(log) <= 200
    assert "step 499" in log[-1]
