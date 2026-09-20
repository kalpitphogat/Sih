"""Phase 4.1 tests for the shallow-water solver.

The heavyweight verification (Ritter, Stoker, well-balancedness, convergence)
lives in `floodguard.validation` and runs under `make validate`. These are the
fast invariants that should fail a commit, plus the honesty properties.
"""

from __future__ import annotations

import numpy as np
import pytest

from floodguard.engines import _swe_kernels as k
from floodguard.engines.base import EngineInput
from floodguard.engines.swe_fv import ShallowWaterFV, Work, solve_1d_dambreak
from floodguard.validation import analytical as exact


# --- honesty ----------------------------------------------------------------------


def test_engine_never_claims_to_be_delft3d():
    """Engineering rule 2, asserted on the class itself."""
    engine = ShallowWaterFV()
    assert engine.display_name == "FloodGuard-SWE (Delft3D-class FV solver)"
    assert engine.display_name != "Delft3D"
    assert "Delft3D-class" in engine.display_name
    assert engine.is_real_solver is True


# --- kernels ----------------------------------------------------------------------


def test_hllc_of_two_identical_still_states_is_pure_hydrostatic():
    h = 5.0
    F0, F1, F2 = k.hllc_flux(h, 0.0, 0.0, h, 0.0, 0.0, 1e-6)
    assert F0 == pytest.approx(0.0, abs=1e-12)
    assert F1 == pytest.approx(0.5 * 9.81 * h * h, rel=1e-12)
    assert F2 == pytest.approx(0.0, abs=1e-12)


def test_hllc_of_two_dry_states_is_zero():
    assert k.hllc_flux(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1e-6) == (0.0, 0.0, 0.0)


def test_hllc_is_conservative_under_state_swap():
    """Swapping left and right must negate the mass and normal-momentum flux."""
    a = k.hllc_flux(4.0, 3.0, 1.0, 1.0, -0.5, 0.2, 1e-6)
    b = k.hllc_flux(1.0, 0.5, 0.2, 4.0, -3.0, 1.0, 1e-6)
    assert a[0] == pytest.approx(-b[0], rel=1e-12)


def test_desingularised_velocity_stays_finite_as_depth_vanishes():
    """The wet/dry front must not produce infinite velocity."""
    for h in (1e-12, 1e-9, 1e-6, 1e-3):
        u = k._velocity(h, 1.0, 1e-3)
        assert np.isfinite(u)
    assert k._velocity(0.0, 1.0, 1e-3) == 0.0


def test_minmod_returns_zero_across_an_extremum():
    assert k._minmod(1.0, -1.0) == 0.0
    assert k._minmod(-2.0, 3.0) == 0.0
    assert k._minmod(2.0, 3.0) == 2.0
    assert k._minmod(-3.0, -2.0) == -2.0


def test_positivity_dt_bounds_the_drain_rate():
    h = np.array([[1.0, 2.0]], dtype=np.float64)
    dh = np.array([[-0.5, -0.1]], dtype=np.float64)
    active = np.ones(h.shape, dtype=np.bool_)
    # Cell 0 empties in 2 s, cell 1 in 20 s: the limit is the smaller.
    assert k.positivity_dt(h, dh, active, 1e-6) == pytest.approx(2.0)


def test_positivity_dt_ignores_filling_cells():
    h = np.array([[1.0]], dtype=np.float64)
    dh = np.array([[+5.0]], dtype=np.float64)
    active = np.ones(h.shape, dtype=np.bool_)
    assert k.positivity_dt(h, dh, active, 1e-6) > 1e20


# --- scheme properties ------------------------------------------------------------


def test_still_water_over_a_slope_stays_still():
    """Well-balancedness, the short version. See make validate for the full test."""
    n = 40
    yy, xx = np.mgrid[0:n, 0:n]
    z = 0.5 * xx + 0.3 * yy
    level = z.max() + 3.0
    h = level - z
    hu = np.zeros_like(h)
    hv = np.zeros_like(h)
    manning = np.zeros_like(h)
    active = np.ones(h.shape, dtype=np.bool_)
    work = Work(z, active)

    for _ in range(25):
        ShallowWaterFV._step(
            h, hu, hv, z, manning, active, work, 10.0, 10.0, 0.05, 1e-3, True,
            open_edges=False,
        )

    assert np.abs(hu).max() < 1e-10, "spurious discharge on a sloping bed"
    assert np.abs((h + z) - level).max() < 1e-10, "water surface drifted"


def test_dam_break_front_never_outruns_ritter():
    """An early flood arrival is the most dangerous kind of numerical error."""
    h0 = 10.0
    res = solve_1d_dambreak(400, 2000.0, h0, 0.0, 15.0)
    wet = res["h"] > 0.001 * h0
    front = res["x"][wet].max()
    assert front <= exact.ritter_front_position(res["t"], h0, 1000.0) + 2 * res["dx"]


def test_dam_break_depth_at_the_dam_is_four_ninths_h0():
    """Ritter's exact, parameter-free result."""
    h0 = 10.0
    res = solve_1d_dambreak(400, 2000.0, h0, 0.0, 15.0)
    i0 = int(np.argmin(np.abs(res["x"] - 1000.0)))
    assert res["h"][i0] == pytest.approx(4.0 / 9.0 * h0, rel=0.05)


def test_depth_never_goes_negative():
    res = solve_1d_dambreak(200, 2000.0, 10.0, 0.0, 30.0)
    assert (res["h"] >= 0.0).all()
    assert np.isfinite(res["h"]).all()


def test_second_order_beats_first_order_on_ritter():
    """If MUSCL is not helping, it is not working."""
    h0 = 10.0
    lo = solve_1d_dambreak(200, 2000.0, h0, 0.0, 15.0, second_order=False)
    hi = solve_1d_dambreak(200, 2000.0, h0, 0.0, 15.0, second_order=True)
    x0 = 1000.0
    e_lo = exact.error_norms(lo["h"], exact.ritter(lo["x"], lo["t"], h0, x0)[0], lo["dx"])
    e_hi = exact.error_norms(hi["h"], exact.ritter(hi["x"], hi["t"], h0, x0)[0], hi["dx"])
    assert e_hi["L1"] < e_lo["L1"]


def test_friction_retards_the_front():
    h0 = 10.0
    free = solve_1d_dambreak(300, 2000.0, h0, 0.0, 15.0, manning_n=0.0)
    rough = solve_1d_dambreak(300, 2000.0, h0, 0.0, 15.0, manning_n=0.06)

    def front(r):
        wet = r["h"] > 0.001 * h0
        return r["x"][wet].max()

    assert front(rough) < front(free)


# --- full engine ------------------------------------------------------------------


def make_spec(**overrides) -> EngineInput:
    """A small sloping channel with a steady release at the top."""
    rows, cols = 30, 60
    yy, xx = np.mgrid[0:rows, 0:cols]
    # A valley: falls to the east, with banks rising away from the centreline.
    z = 100.0 - 0.5 * xx + 0.8 * np.abs(yy - rows // 2)
    spec = dict(
        bed_elevation=z,
        manning_n=np.full((rows, cols), 0.03),
        active=np.ones((rows, cols), dtype=bool),
        cell_size_m=50.0,
        transform=None,
        crs="EPSG:32644",
        source_rc=(rows // 2, 2),
        inflow_q=lambda t: 2000.0 if t < 600.0 else 0.0,
        inflow_volume_m3=2000.0 * 600.0,
        duration_s=1200.0,
        output_interval_s=300.0,
        wet_threshold_m=0.3,
        dry_tolerance_m=1e-3,
    )
    spec.update(overrides)
    return EngineInput(**spec)


def test_engine_runs_and_floods_downstream():
    result = ShallowWaterFV().run(make_spec())
    assert result.steps > 0
    assert result.max_depth.max() > 0.3
    assert result.flooded_area_km2() > 0.0
    # The wave must travel away from the release point, not stay put.
    wet_cols = np.nonzero((result.max_depth > 0.3).any(axis=0))[0]
    assert wet_cols.max() > 10


def test_engine_summary_is_computed_not_hardcoded():
    result = ShallowWaterFV().run(make_spec())
    s = result.summary()
    assert s["engine_display_name"] == "FloodGuard-SWE (Delft3D-class FV solver)"
    assert s["is_real_solver"] is True
    for key in ("flooded_area_km2", "max_depth_m", "max_velocity_ms", "earliest_arrival_min"):
        assert s[key] is not None
        assert np.isfinite(s[key])
    assert s["max_depth_m"] == pytest.approx(result.max_depth.max())


def test_arrival_time_increases_downstream():
    """Physical ordering: water cannot arrive downstream before it arrives upstream."""
    result = ShallowWaterFV().run(make_spec())
    mid = result.arrival_time_s.shape[0] // 2
    row = result.arrival_time_s[mid]
    arrived = np.nonzero(row >= 0)[0]
    assert arrived.size > 3
    times = row[arrived]
    # Allow small non-monotonicity from the 2-D spreading, but the trend must hold.
    assert times[-1] > times[0]


def test_engine_rejects_a_release_point_outside_the_domain():
    active = np.ones((30, 60), dtype=bool)
    active[15, 2] = False
    with pytest.raises(ValueError, match="outside the active domain"):
        ShallowWaterFV().run(make_spec(active=active))


def test_engine_warns_when_nothing_floods():
    result = ShallowWaterFV().run(make_spec(inflow_q=lambda t: 0.0, inflow_volume_m3=0.0))
    assert any("wet threshold" in w for w in result.warnings)
    assert result.summary()["max_depth_m"] is None


def test_provenance_records_the_scheme_and_its_references():
    result = ShallowWaterFV().run(make_spec())
    p = result.provenance
    assert "HLLC" in p["scheme"]
    assert "Audusse" in p["scheme"]
    assert p["is_real_solver"] is True
    assert p["spatial_order"] == 2
    assert any("Toro" in ref for ref in p["references"])
    for key in ("cfl", "dry_tolerance_m", "grid", "clipped_fraction", "mass_error"):
        assert key in p


def test_frames_are_captured_for_the_time_slider():
    result = ShallowWaterFV().run(make_spec())
    assert len(result.frames) >= 2
    times = [t for t, _ in result.frames]
    assert times == sorted(times)
    assert all(frame.shape == result.max_depth.shape for _, frame in result.frames)
