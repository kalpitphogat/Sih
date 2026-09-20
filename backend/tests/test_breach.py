"""Phase 3 tests: breach parameters, outflow, and level-pool routing."""

from __future__ import annotations

import numpy as np
import pytest

from floodguard.breach import parameters as bp
from floodguard.breach import routing
from floodguard.breach import validation as bv
from floodguard.preprocess.reservoir import ElevationAreaCapacity
from floodguard.scenario import BreachGrowth, DamType, ScenarioType


def conic_curve(crest=100.0, storage_m3=1.0e9, n=80):
    """A conic reservoir with a known storage, for routing tests."""
    levels = np.linspace(0.0, crest, n)
    area_top = 3.0 * storage_m3 / crest
    areas = area_top * (levels / crest) ** 2
    volumes = areas * levels / 3.0
    return ElevationAreaCapacity(levels, areas, volumes, 900.0, 0.0)


# --- breach parameter models ------------------------------------------------------


def test_froehlich_reproduces_teton_within_published_uncertainty():
    """Teton 1976 is the canonical benchmark. Froehlich should be close.

    Observed average breach width 151 m. The stated uncertainty is roughly a
    factor of 1.5, so anything inside 100-230 m is a pass.
    """
    g = bp.froehlich_2008(
        height_m=bv.TETON.head_m,
        storage_m3=bv.TETON.storage_m3,
        scenario_type=ScenarioType.PIPING_FAILURE,
    )
    assert 100.0 < g.width_m < 230.0, f"Froehlich width {g.width_m:.0f} m off Teton 151 m"
    assert 30.0 < g.formation_time_min < 200.0


def test_piping_gives_a_narrower_breach_than_overtopping():
    """Froehlich k0 is 1.0 for piping and 1.3 for overtopping."""
    piping = bp.froehlich_2008(50.0, 1e8, ScenarioType.PIPING_FAILURE)
    overtop = bp.froehlich_2008(50.0, 1e8, ScenarioType.OVERTOPPING)
    assert piping.width_m < overtop.width_m
    assert piping.side_slope < overtop.side_slope


def test_concrete_dams_are_marked_inapplicable():
    """All three models are embankment regressions. Say so for concrete."""
    results = bp.predict_all(
        100.0, 1e9, DamType.CONCRETE_ARCH, ScenarioType.COMPLETE_DAM_BREAK
    )
    assert all(not r.applicable for r in results)
    assert all("EMBANKMENT" in r.caveats[0] for r in results)


def test_embankment_dams_are_marked_applicable():
    results = bp.predict_all(100.0, 1e9, DamType.EARTHFILL, ScenarioType.OVERTOPPING)
    assert all(r.applicable for r in results)


def test_von_thun_offset_is_a_step_function_of_storage():
    small = bp.von_thun_gillette_1990(50.0, 1.0e6, ScenarioType.OVERTOPPING)
    large = bp.von_thun_gillette_1990(50.0, 1.0e9, ScenarioType.OVERTOPPING)
    assert large.width_m > small.width_m
    # Same height, so the difference is exactly the storage offset.
    assert large.width_m - small.width_m == pytest.approx(54.9 - 6.1)


def test_consensus_reports_the_spread():
    models = bp.predict_all(100.0, 1e9, DamType.EARTHFILL, ScenarioType.OVERTOPPING)
    stats = bp.consensus(models)
    assert stats["width_m"]["spread_ratio"] >= 1.0
    assert stats["width_m"]["min"] <= stats["width_m"]["mean"] <= stats["width_m"]["max"]


# --- breach growth ----------------------------------------------------------------


@pytest.mark.parametrize("growth", list(BreachGrowth))
def test_growth_laws_run_from_zero_to_one(growth):
    assert routing.breach_fraction(0.0, 600.0, growth) == pytest.approx(0.0, abs=1e-9)
    assert routing.breach_fraction(600.0, 600.0, growth) == pytest.approx(1.0)
    assert routing.breach_fraction(1e6, 600.0, growth) == pytest.approx(1.0)


@pytest.mark.parametrize("growth", list(BreachGrowth))
def test_growth_laws_are_monotonic(growth):
    ts = np.linspace(0, 600, 50)
    f = [routing.breach_fraction(t, 600.0, growth) for t in ts]
    assert all(b >= a - 1e-12 for a, b in zip(f, f[1:]))


def test_instant_formation_is_fully_open_immediately():
    assert routing.breach_fraction(0.0, 0.0, BreachGrowth.LINEAR) == 1.0


# --- outflow ----------------------------------------------------------------------


def test_weir_outflow_is_zero_below_the_invert():
    state = routing.BreachState(bottom_width_m=100.0, invert_m=50.0, side_slope=1.0)
    assert routing.weir_outflow(49.0, state) == 0.0
    assert routing.weir_outflow(50.0, state) == 0.0
    assert routing.weir_outflow(51.0, state) > 0.0


def test_weir_outflow_follows_the_three_halves_power():
    """For a rectangular breach, doubling head must raise Q by 2^1.5."""
    state = routing.BreachState(bottom_width_m=100.0, invert_m=0.0, side_slope=0.0)
    q1 = routing.weir_outflow(1.0, state)
    q2 = routing.weir_outflow(2.0, state)
    assert q2 / q1 == pytest.approx(2.0**1.5, rel=1e-9)


def test_submergence_reduces_outflow():
    state = routing.BreachState(bottom_width_m=100.0, invert_m=0.0, side_slope=1.0)
    free = routing.weir_outflow(10.0, state, tailwater_m=0.0)
    drowned = routing.weir_outflow(10.0, state, tailwater_m=8.0)
    assert drowned < free


def test_side_slopes_add_discharge():
    rect = routing.BreachState(100.0, 0.0, 0.0)
    trap = routing.BreachState(100.0, 0.0, 1.0)
    assert routing.weir_outflow(10.0, trap) > routing.weir_outflow(10.0, rect)


# --- routing ----------------------------------------------------------------------


def test_routing_conserves_mass():
    """The single most important property: volume out equals storage lost."""
    curve = conic_curve(crest=100.0, storage_m3=1.0e9)
    geom = bp.BreachGeometry(
        "test", width_m=100.0, depth_m=100.0, side_slope=1.0,
        formation_time_min=30.0, reference="test",
    )
    h = routing.route(
        curve, geom, initial_level_m=100.0, crest_elevation_m=100.0,
        scenario_type=ScenarioType.OVERTOPPING, duration_s=12 * 3600.0,
    )
    assert h.mass_error < 1e-3, f"mass closure {h.mass_error:.2e}"
    assert not any("mass balance" in w for w in h.warnings)


def test_routing_drains_the_reservoir_to_the_invert():
    curve = conic_curve(crest=100.0, storage_m3=1.0e9)
    geom = bp.BreachGeometry(
        "test", width_m=200.0, depth_m=100.0, side_slope=1.0,
        formation_time_min=15.0, reference="test",
    )
    h = routing.route(
        curve, geom, initial_level_m=100.0, crest_elevation_m=100.0,
        scenario_type=ScenarioType.OVERTOPPING, duration_s=24 * 3600.0,
    )
    assert h.final_level_m < 5.0, "a full-height breach should empty the reservoir"
    assert h.total_volume_m3 == pytest.approx(1.0e9, rel=0.05)


def test_peak_is_insensitive_to_the_step_cap():
    """Adaptive stepping exists so the peak is physics, not numerics.

    Halving the maximum permitted level drop per step must not move the peak
    discharge appreciably. If it does, the integrator is stepping over it.
    """
    curve = conic_curve(crest=100.0, storage_m3=5.0e8)
    geom = bp.BreachGeometry(
        "test", width_m=150.0, depth_m=100.0, side_slope=1.0,
        formation_time_min=20.0, reference="test",
    )
    kw = dict(
        initial_level_m=100.0, crest_elevation_m=100.0,
        scenario_type=ScenarioType.OVERTOPPING, duration_s=12 * 3600.0,
    )
    coarse = routing.route(curve, geom, max_level_drop_per_step_m=0.10, **kw)
    fine = routing.route(curve, geom, max_level_drop_per_step_m=0.02, **kw)
    rel = abs(coarse.peak_discharge_m3s - fine.peak_discharge_m3s) / fine.peak_discharge_m3s
    assert rel < 0.02, f"peak moved {rel:.1%} when the step was refined"


def test_a_slower_breach_gives_a_lower_peak():
    curve = conic_curve(crest=100.0, storage_m3=1.0e9)
    kw = dict(
        initial_level_m=100.0, crest_elevation_m=100.0,
        scenario_type=ScenarioType.OVERTOPPING, duration_s=24 * 3600.0,
    )
    fast = routing.route(curve, bp.BreachGeometry("f", 150.0, 100.0, 1.0, 10.0, "t"), **kw)
    slow = routing.route(curve, bp.BreachGeometry("s", 150.0, 100.0, 1.0, 180.0, "t"), **kw)
    assert slow.peak_discharge_m3s < fast.peak_discharge_m3s
    # Same reservoir, so the same total volume leaves either way.
    assert slow.total_volume_m3 == pytest.approx(fast.total_volume_m3, rel=0.05)


def test_a_wider_breach_gives_a_higher_peak():
    curve = conic_curve(crest=100.0, storage_m3=1.0e9)
    kw = dict(
        initial_level_m=100.0, crest_elevation_m=100.0,
        scenario_type=ScenarioType.OVERTOPPING, duration_s=24 * 3600.0,
    )
    narrow = routing.route(curve, bp.BreachGeometry("n", 50.0, 100.0, 1.0, 30.0, "t"), **kw)
    wide = routing.route(curve, bp.BreachGeometry("w", 400.0, 100.0, 1.0, 30.0, "t"), **kw)
    assert wide.peak_discharge_m3s > narrow.peak_discharge_m3s


def test_hydrograph_interpolates_and_is_zero_outside_its_window():
    curve = conic_curve()
    h = routing.route(
        curve, bp.BreachGeometry("t", 100.0, 100.0, 1.0, 30.0, "t"),
        initial_level_m=100.0, crest_elevation_m=100.0,
        scenario_type=ScenarioType.OVERTOPPING, duration_s=6 * 3600.0,
    )
    assert h.q_at(-100.0) == 0.0
    assert h.q_at(1e9) == 0.0
    assert h.q_at(h.time_to_peak_s) == pytest.approx(h.peak_discharge_m3s, rel=0.05)


def test_routing_records_provenance():
    curve = conic_curve()
    h = routing.route(
        curve, bp.BreachGeometry("froehlich_2008", 100.0, 100.0, 1.0, 30.0, "ref"),
        initial_level_m=100.0, crest_elevation_m=100.0,
        scenario_type=ScenarioType.OVERTOPPING, duration_s=3600.0,
    )
    for key in ("breach_model", "integration", "weir_coefficients", "initial_storage_mcm"):
        assert key in h.provenance


# --- historical validation --------------------------------------------------------


def test_historical_validation_runs_and_scores_every_model():
    results = bv.run_all()
    assert len(results) == len(bv.CASES)
    for r in results:
        assert set(r.scores) == {
            "froehlich_2008",
            "von_thun_gillette_1990",
            "macdonald_langridge_monopolis_1984",
        }
        for s in r.scores.values():
            assert np.isfinite(s["width_error_pct"])
