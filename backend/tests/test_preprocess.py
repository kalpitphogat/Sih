"""Phase 2 tests on synthetic terrain with known analytical answers.

Synthetic rather than real DEM so the expected values are exact: a real DEM
test can only assert "looks plausible", which is not a test.
"""

from __future__ import annotations

import numpy as np
import pytest

from floodguard.preprocess import hydro, reservoir, roughness


def inclined_plane(rows=40, cols=40, slope=0.01, cell=10.0):
    """A plane tilting down to the east. D8 must point due east everywhere."""
    x = np.arange(cols) * cell
    return np.tile(100.0 - slope * x, (rows, 1))


def test_fill_leaves_a_drainable_surface_untouched():
    dem = inclined_plane()
    nodata = np.zeros(dem.shape, dtype=bool)
    filled = hydro.fill_depressions(dem, nodata)
    assert np.allclose(filled, dem, atol=1e-9)


def test_fill_removes_a_pit_without_lowering_anything():
    dem = inclined_plane()
    dem[20, 20] -= 30.0
    nodata = np.zeros(dem.shape, dtype=bool)
    filled = hydro.fill_depressions(dem, nodata)
    assert filled[20, 20] > dem[20, 20], "the pit was not filled"
    assert (filled >= dem - 1e-9).all(), "filling must never lower a cell"


def test_d8_points_downhill_on_a_plane():
    dem = inclined_plane()
    nodata = np.zeros(dem.shape, dtype=bool)
    direction = hydro.d8_flow_direction(dem, nodata)
    interior = direction[1:-1, 1:-2]
    assert (interior == 1).all(), "east-facing plane must give D8 code 1 (east)"


def test_d8_distance_weighting_prefers_the_cardinal_neighbour():
    """A diagonal must be sqrt(2) times lower to beat a cardinal neighbour.

    Here east is 1.0 m lower and south-east is 1.2 m lower. Unweighted, the
    diagonal wins. Correctly weighted, east wins because 1.0 > 1.2/sqrt(2).
    """
    dem = np.full((3, 3), 10.0)
    dem[1, 2] = 9.0    # east
    dem[2, 2] = 8.8    # south-east
    nodata = np.zeros(dem.shape, dtype=bool)
    assert hydro.d8_flow_direction(dem, nodata)[1, 1] == 1


def test_accumulation_conserves_cells():
    """Every cell drains somewhere, so the outlet must collect them all."""
    dem = inclined_plane(rows=10, cols=10)
    nodata = np.zeros(dem.shape, dtype=bool)
    direction = hydro.d8_flow_direction(dem, nodata)
    accum = hydro.flow_accumulation(direction, dem, nodata)
    # Each row drains east independently, so the last column holds one full row.
    assert accum[5, -1] == pytest.approx(10.0)


def test_watershed_of_an_outlet_is_everything_upstream():
    dem = inclined_plane(rows=10, cols=10)
    nodata = np.zeros(dem.shape, dtype=bool)
    direction = hydro.d8_flow_direction(dem, nodata)
    ws = hydro.upstream_watershed(direction, 5, 9)
    # Row 5 drains east into (5, 9); other rows do not.
    assert ws[5, :].all()
    assert not ws[0, 0]


def test_trace_follows_the_slope_and_stops_at_the_edge():
    dem = inclined_plane(rows=10, cols=20, cell=10.0)
    nodata = np.zeros(dem.shape, dtype=bool)
    direction = hydro.d8_flow_direction(dem, nodata)
    path, length = hydro.trace_downstream(direction, 5, 0, 10.0, 1e6)
    assert path[0].tolist() == [5, 0]
    assert path[-1][1] == 19, "should run to the eastern edge"
    assert length == pytest.approx(19 * 10.0)


def test_pool_does_not_leak_past_the_dam():
    """The bug that inflated Tehri's storage ~90x, as a regression test.

    A bowl upstream, a ridge (the dam), and a deep valley downstream. Without
    the catchment restriction, filling to the ridge crest also floods the
    downstream valley, because that valley is lower than the reservoir.
    """
    dem = np.full((20, 40), 200.0)
    dem[:, :15] = 150.0     # reservoir basin
    dem[:, 15] = 250.0      # the dam: a ridge
    dem[:, 16:] = 50.0      # downstream valley, far lower than the pool

    nodata = np.zeros(dem.shape, dtype=bool)
    catchment = np.zeros(dem.shape, dtype=bool)
    catchment[:, :15] = True

    leaky = reservoir.delineate_pool(dem, nodata, 10, 14, 200.0, None)
    confined = reservoir.delineate_pool(dem, nodata, 10, 14, 200.0, catchment)

    assert leaky[:, 20].any(), "without a catchment mask the fill should leak (the bug)"
    assert not confined[:, 20].any(), "with a catchment mask it must not leak"
    assert confined[:, :15].all()


def test_eac_curve_is_monotonic_and_invertible():
    dem = np.full((30, 30), 200.0)
    yy, xx = np.mgrid[0:30, 0:30]
    dem = 100.0 + 0.5 * np.hypot(yy - 15, xx - 15)   # a cone-shaped basin
    nodata = np.zeros(dem.shape, dtype=bool)

    curve = reservoir.elevation_area_capacity(
        dem, nodata, 15, 15, cell_area_m2=100.0, max_level_m=105.0, n_levels=20
    )
    assert (np.diff(curve.volumes_m3) >= -1e-9).all(), "volume must increase with level"
    assert (np.diff(curve.areas_m2) >= -1e-9).all(), "area must increase with level"

    mid = curve.volumes_m3[len(curve.volumes_m3) // 2]
    assert curve.volume_at(curve.level_at_volume(mid)) == pytest.approx(mid, rel=1e-6)


def test_bathymetry_reconstruction_hits_the_registered_storage():
    """The reconstruction exists to make V(FRL) equal the registered value."""
    levels = np.linspace(100.0, 110.0, 11)
    areas = np.full(11, 1.0e6)
    volumes = (levels - 100.0) * 1.0e6
    curve = reservoir.ElevationAreaCapacity(levels, areas, volumes, 900.0, 100.0)

    target = 50.0e6
    new_curve, meta = reservoir.reconstruct_bathymetry(curve, 100.0, target)

    assert meta["applied"] is True
    assert meta["is_reconstruction"] is True
    assert new_curve.volumes_m3[-1] == pytest.approx(target, rel=0.02)
    assert meta["bed_elevation_m"] < 100.0


def test_bathymetry_is_skipped_when_nothing_is_hidden():
    levels = np.linspace(100.0, 110.0, 11)
    curve = reservoir.ElevationAreaCapacity(
        levels, np.full(11, 1.0e6), (levels - 100.0) * 1.0e6, 900.0, 100.0
    )
    _, meta = reservoir.reconstruct_bathymetry(curve, 100.0, 1.0e6)
    assert meta["applied"] is False
    assert "reason" in meta


def test_water_surface_detected_only_when_a_plateau_exists():
    dem = np.random.default_rng(0).uniform(100, 200, (50, 50))
    pool = np.ones(dem.shape, dtype=bool)
    assert reservoir.detect_water_surface(dem, pool) is None

    dem[:40, :] = 814.5   # a flat lake over 80% of the pool
    assert reservoir.detect_water_surface(dem, pool) == pytest.approx(814.5, abs=1.0)


def test_manning_override_rejects_an_implausible_value():
    with pytest.raises(ValueError, match="outside the physically plausible range"):
        roughness.resolve_class_values({"channel": 5.0})


def test_uniform_roughness_declares_itself_as_an_assumption():
    field = roughness.uniform((10, 10), 0.035)
    assert field.coverage_fraction == 0.0
    assert "uniform" in field.source
    assert any("uniform" in note for note in field.notes)
