"""Phase 4.2-4.4 tests: adapters generate real decks and never fake a solve.

The deck generation is a deliverable in its own right, so it is tested as one.
The refusal to substitute silently is tested as the more important property.
"""

from __future__ import annotations

import numpy as np
import pytest

from floodguard.engines.availability import EngineUnavailable
from floodguard.engines.base import EngineInput


def make_spec(rows=20, cols=40, cell=50.0) -> EngineInput:
    from rasterio.transform import from_origin

    yy, xx = np.mgrid[0:rows, 0:cols]
    bed = 200.0 - 0.5 * xx + 0.8 * np.abs(yy - rows // 2)
    return EngineInput(
        bed_elevation=bed,
        manning_n=np.full((rows, cols), 0.035),
        active=np.ones((rows, cols), dtype=bool),
        cell_size_m=cell,
        transform=from_origin(500000.0, 3300000.0, cell, cell),
        crs="EPSG:32644",
        source_rc=(rows // 2, 2),
        source_cells=[(rows // 2, 2, 1.0), (rows // 2 + 1, 2, 0.8)],
        inflow_q=lambda t: 5000.0 if t < 1800 else 0.0,
        inflow_volume_m3=5000.0 * 1800,
        duration_s=3600.0,
        output_interval_s=300.0,
    )


# --- Delft3D -----------------------------------------------------------------------


def test_delft3d_writes_a_complete_deck(tmp_path):
    from floodguard.engines.delft3d_adapter import write_case

    files = write_case(make_spec(), tmp_path / "case")
    assert set(files) == {"net", "bc", "pli", "ext", "mdu", "dimr", "readme"}
    for path in files.values():
        assert path.exists() and path.stat().st_size > 0, path


def test_delft3d_net_file_is_valid_ugrid(tmp_path):
    """The grid must actually open as NetCDF with the UGRID roles set."""
    import netCDF4

    from floodguard.engines.delft3d_adapter import write_case

    spec = make_spec()
    files = write_case(spec, tmp_path / "case")

    with netCDF4.Dataset(files["net"]) as ds:
        assert ds.Conventions.startswith("CF-1.8 UGRID")
        assert ds.variables["Mesh2D"].cf_role == "mesh_topology"
        n_nodes = ds.dimensions["nNetNode"].size
        rows, cols = spec.shape
        assert n_nodes == (rows + 1) * (cols + 1)
        assert ds.dimensions["nNetElem"].size == int(spec.active.sum())
        # Bed levels must be real elevations, not fill values.
        z = ds.variables["NetNode_z"][:]
        assert np.isfinite(z).all()
        assert z.min() > 0


def test_delft3d_mdu_mirrors_our_solver_settings(tmp_path):
    """A comparison is only meaningful if both solvers solve the same problem."""
    from floodguard.engines.delft3d_adapter import write_case

    spec = make_spec()
    files = write_case(spec, tmp_path / "case")
    mdu = files["mdu"].read_text(encoding="utf-8")

    assert f"CFLMax                    = {spec.cfl}" in mdu
    assert f"Epshu                     = {spec.dry_tolerance_m}" in mdu
    assert f"TStop                     = {spec.duration_s:.0f}" in mdu
    assert "UnifFrictType             = 1" in mdu  # Manning


def test_delft3d_bc_carries_the_breach_hydrograph(tmp_path):
    from floodguard.engines.delft3d_adapter import write_case

    files = write_case(make_spec(), tmp_path / "case")
    bc = files["bc"].read_text(encoding="utf-8")
    assert "dischargebnd" in bc
    assert "timeseries" in bc
    # The 5000 m3/s plateau must appear in the series.
    assert "5000.000" in bc


def test_delft3d_refuses_to_run_without_a_binary(tmp_path, monkeypatch):
    """The central honesty property for this adapter."""
    from floodguard.engines import delft3d_adapter

    monkeypatch.setattr(delft3d_adapter, "find_binary", lambda: None)
    engine = delft3d_adapter.Delft3DAdapter(work_dir=tmp_path / "case")

    with pytest.raises(EngineUnavailable) as excinfo:
        engine.run(make_spec())

    message = str(excinfo.value)
    assert "no dflowfm binary" in message
    assert "no Delft3D solve was performed" in message
    # The deck is still written, because it is a deliverable on its own.
    assert (tmp_path / "case" / "floodguard.mdu").exists()


# --- DualSPHysics ------------------------------------------------------------------


def test_dualsphysics_writes_a_casedef(tmp_path):
    from floodguard.engines.dualsphysics_adapter import write_case

    files = write_case(make_spec(), tmp_path / "dsph", 5.0)
    xml = files["casedef"].read_text(encoding="utf-8")
    assert xml.startswith("<?xml")
    assert "<casedef>" in xml and "</case>" in xml
    assert "DeltaSPH" in xml
    assert "Wendland" in xml or 'value="2"' in xml


def test_dualsphysics_casedef_is_well_formed_xml(tmp_path):
    import xml.etree.ElementTree as ET

    from floodguard.engines.dualsphysics_adapter import write_case

    files = write_case(make_spec(), tmp_path / "dsph", 5.0)
    root = ET.parse(files["casedef"]).getroot()
    assert root.tag == "case"
    assert root.find("casedef") is not None
    assert root.find("execution") is not None


def test_dualsphysics_records_the_lgpl_constraint(tmp_path):
    """LGPL-2.1 is the binding licence constraint in this repository."""
    from floodguard.engines.dualsphysics_adapter import LICENCE_NOTE, write_case

    assert "LGPL-2.1" in LICENCE_NOTE
    assert "copies no DualSPHysics source" in LICENCE_NOTE
    files = write_case(make_spec(), tmp_path / "dsph", 5.0)
    assert "LGPL-2.1" in files["readme"].read_text(encoding="utf-8")


def test_dualsphysics_refuses_without_binaries(tmp_path, monkeypatch):
    from floodguard.engines import dualsphysics_adapter

    monkeypatch.setattr(
        dualsphysics_adapter,
        "find_binaries",
        lambda: {"gencase": None, "solver": None, "partvtk": None},
    )
    engine = dualsphysics_adapter.DualSPHysicsAdapter(work_dir=tmp_path / "dsph")

    with pytest.raises(EngineUnavailable) as excinfo:
        engine.run(make_spec())
    assert "not installed" in str(excinfo.value)
    assert (tmp_path / "dsph" / "CaseDef.xml").exists()


# --- PySPH -------------------------------------------------------------------------


def test_pysph_refuses_rather_than_substituting():
    """The defect found in a reference repo during the Phase 0 audit.

    That repository labelled a ballistic particle tracer — whose kernel
    function was never called — as the Monaghan SPH formulation. This engine
    reports itself unavailable instead.
    """
    from floodguard.engines.sph_pysph import PySPHEngine

    with pytest.raises(EngineUnavailable) as excinfo:
        PySPHEngine().run(make_spec())
    message = str(excinfo.value).lower()
    assert "sph" in message
    assert "depth-averaged" in message or "not installed" in message


def test_pysph_case_description_is_reviewable():
    """The case setup must be inspectable even where PySPH cannot run."""
    from floodguard.engines.sph_pysph import PySPHEngine

    case = PySPHEngine().describe_case(make_spec())
    assert "delta-SPH" in case["formulation"]
    assert "Monaghan" in case["reference"]
    assert case["estimated_particles"] > 0
    assert case["sound_speed_ms"] > 0
    assert len(case["coupling_assumptions"]) >= 3
    assert "feasibility_note" in case


def test_coupling_assumptions_are_stated_not_implied():
    from floodguard.engines.sph_pysph import COUPLING_ASSUMPTIONS

    joined = " ".join(COUPLING_ASSUMPTIONS).lower()
    assert "vertical" in joined  # the structure SPH resolves and the coupling discards
    assert "transfer section" in joined


# --- ANUGA -------------------------------------------------------------------------


def test_anuga_refuses_when_not_installed():
    from floodguard.engines.anuga_engine import AnugaEngine, anuga_available

    if anuga_available():
        pytest.skip("ANUGA is installed on this machine")

    with pytest.raises(EngineUnavailable) as excinfo:
        AnugaEngine().run(make_spec())
    assert "conda-forge" in str(excinfo.value)
    assert "may be labelled as ANUGA" in str(excinfo.value)


# --- registry ----------------------------------------------------------------------


def test_every_engine_id_has_an_implementation():
    from floodguard.engines.availability import probe_all
    from floodguard.pipeline import _engine_classes

    classes = _engine_classes()
    for status in probe_all():
        assert status.id in classes, f"{status.id} is probed but has no implementation"


def test_decks_are_written_for_requested_external_engines(tmp_path):
    from floodguard.pipeline import write_engine_decks

    written = write_engine_decks(["delft3d", "dualsphysics", "swe_fv"], make_spec(), tmp_path)
    assert set(written) == {"delft3d", "dualsphysics"}
    assert (tmp_path / "delft3d_case" / "floodguard.mdu").exists()
    assert (tmp_path / "dualsphysics_case" / "CaseDef.xml").exists()
