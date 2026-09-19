"""Phase 0 tests: the app boots and reports its capabilities truthfully."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from floodguard.engines.availability import EngineKind, probe_all, resolve

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "FloodGuard India"
    # swe_fv is our own solver; if numpy is installed it must be available.
    assert "swe_fv" in body["engines_available"]


def test_health_never_leaks_credential_values():
    body = client.get("/health").json()
    for value in body["credentials"].values():
        assert isinstance(value, bool)


def test_engine_health_endpoint_lists_every_engine():
    r = client.get("/api/health/engines")
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()["engines"]}
    assert ids == {"swe_fv", "anuga", "delft3d", "sph_pysph", "dualsphysics"}


def test_unavailable_delft3d_is_never_labelled_delft3d():
    """Engineering rule 2. The single most important test in Phase 0.

    If the dflowfm binary is absent, no user-visible string may claim 'Delft3D'
    as the thing that ran.
    """
    status = resolve("delft3d")
    if not status.available:
        assert "Delft3D-class" in status.display_name
        assert status.display_name != "Delft3D"
        assert status.is_real_solver is False
        assert status.substitute_id == "swe_fv"


def test_unavailable_engines_declare_a_substitute():
    for status in probe_all():
        if status.kind is EngineKind.UNAVAILABLE and status.id != "swe_fv":
            assert status.substitute_id, f"{status.id} is unavailable but names no substitute"


def test_resolve_rejects_unknown_engine():
    import pytest

    with pytest.raises(KeyError):
        resolve("definitely_not_an_engine")
