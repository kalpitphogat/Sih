"""Reservoir geometry derived from the DEM.

The elevation-area-capacity curve is the single most load-bearing derived
product in the whole pipeline: it converts the breach outflow into a falling
reservoir level, which is what makes the outflow hydrograph recede. Get it
wrong and the flood either never stops or stops instantly.

It is also the best available check on whether our DEM and our dam catalog are
describing the same reservoir. `sanity_check()` compares the DEM-integrated
gross storage against the CWC NRLD value and reports the percentage difference.
A large disagreement means something upstream is wrong — the dam point, the FRL,
the DEM epoch — and it is far better to see that number than to discover it in
the inundation map.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class ElevationAreaCapacity:
    """The E-A-C curve, integrated from the DEM."""

    levels_m: np.ndarray        # water surface elevations, m MSL
    areas_m2: np.ndarray        # inundated pool area at each level
    volumes_m3: np.ndarray      # cumulative storage below each level
    cell_area_m2: float
    dam_elevation_m: float
    method: str = "DEM integration over the connected pool upstream of the dam"

    def area_at(self, level_m: float) -> float:
        """Pool surface area at a water level, m2. Linear between samples."""
        return float(np.interp(level_m, self.levels_m, self.areas_m2))

    def volume_at(self, level_m: float) -> float:
        """Storage below a water level, m3."""
        return float(np.interp(level_m, self.levels_m, self.volumes_m3))

    def level_at_volume(self, volume_m3: float) -> float:
        """Invert the curve: water level holding a given storage, m MSL.

        This is the operation reservoir routing performs every timestep, so it
        is worth stating that it is exact only to the curve's sampling
        interval. Outside the curve's range it clamps rather than extrapolating,
        because extrapolating a hypsometric curve invents bathymetry.
        """
        volume_m3 = float(np.clip(volume_m3, self.volumes_m3[0], self.volumes_m3[-1]))
        return float(np.interp(volume_m3, self.volumes_m3, self.levels_m))

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "cell_area_m2": self.cell_area_m2,
            "dam_elevation_m": self.dam_elevation_m,
            "levels_m": self.levels_m.tolist(),
            "areas_km2": (self.areas_m2 / 1e6).tolist(),
            "volumes_mcm": (self.volumes_m3 / 1e6).tolist(),
        }


@dataclass
class ReservoirGeometry:
    """Delineated pool plus its derived curve and the catalog cross-check."""

    pool_mask: np.ndarray                 # cells in the reservoir at FRL
    curve: ElevationAreaCapacity
    frl_m: float
    derived_gross_storage_mcm: float
    catalog_gross_storage_mcm: float | None
    storage_difference_pct: float | None
    derived_area_km2: float
    #: Metadata from the bathymetry reconstruction, if one was applied.
    bathymetry: dict[str, Any] = field(default_factory=dict)
    #: Storage the DEM could actually see, before any reconstruction.
    visible_storage_mcm: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Reservoir at FRL {self.frl_m:.2f} m MSL",
            f"  DEM-derived area      : {self.derived_area_km2:.2f} km2",
            f"  DEM-visible storage   : {self.visible_storage_mcm:.1f} MCM",
            f"  Storage used by solver: {self.derived_gross_storage_mcm:.1f} MCM"
            + (" (bathymetry reconstructed)" if self.bathymetry.get("applied") else ""),
        ]
        if self.catalog_gross_storage_mcm is not None:
            lines.append(
                f"  CWC NRLD gross storage: {self.catalog_gross_storage_mcm:.1f} MCM"
            )
            lines.append(
                f"  Difference            : {self.storage_difference_pct:+.1f} %"
            )
        else:
            lines.append("  CWC NRLD gross storage: not available — no cross-check possible")
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frl_m": self.frl_m,
            "derived_gross_storage_mcm": self.derived_gross_storage_mcm,
            "catalog_gross_storage_mcm": self.catalog_gross_storage_mcm,
            "storage_difference_pct": self.storage_difference_pct,
            "derived_area_km2": self.derived_area_km2,
            "visible_storage_mcm": self.visible_storage_mcm,
            "bathymetry": self.bathymetry,
            "warnings": self.warnings,
            "curve": self.curve.to_dict(),
        }


def delineate_pool(
    dem: np.ndarray,
    nodata_mask: np.ndarray,
    dam_row: int,
    dam_col: int,
    level_m: float,
    catchment_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Cells impounded to `level_m` behind the dam.

    Two traps, both of which we hit on the real Tehri DEM before this function
    was written the way it is now:

    1. **A plain `dem <= level` threshold leaks downstream.** On a steep reach
       the river bed 60 km below the dam sits hundreds of metres beneath the
       reservoir surface, so it satisfies the threshold too. Restricting to the
       dam's upstream catchment (`catchment_mask`) makes that impossible.

    2. **The dam cell is not in the pool.** The routing release point is
       snapped to the highest-accumulation cell near the published coordinate,
       which lands on the channel *below* the dam — at Tehri, 70 m lower than
       the reservoir surface, with the dam embankment between the two. Seeding
       a flood fill there finds a puddle in the tailrace, not the reservoir.

    So the pool is defined as the **largest connected component of
    (dem <= level) within the dam's catchment**. Behind a dam that component is
    the impoundment by construction: it is the contiguous region upstream that
    water at `level_m` occupies. The result is checked for adjacency to the dam
    and the caller is warned if it is implausibly far away.
    """
    from scipy import ndimage

    below = (dem <= level_m) & ~nodata_mask
    if catchment_mask is not None:
        below &= catchment_mask
    if not below.any():
        log.warning("no cell at or below %.2f m in the dam catchment; pool is empty", level_m)
        return np.zeros_like(below)

    labels, n_components = ndimage.label(below)
    if n_components == 0:
        return np.zeros_like(below)

    # Component sizes, ignoring label 0 (background).
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    pool_label = int(np.argmax(sizes))
    pool = labels == pool_label

    # Sanity: the impoundment should start near the dam, not in a different
    # part of the catchment. Report the distance so the caller can judge.
    rr, cc = np.nonzero(pool)
    nearest_cells = float(np.sqrt(((rr - dam_row) ** 2 + (cc - dam_col) ** 2).min()))
    if nearest_cells > 100:
        log.warning(
            "the largest sub-%.1f m component in the catchment starts %.0f cells from the "
            "dam; it may not be the impoundment",
            level_m,
            nearest_cells,
        )

    return pool


def elevation_area_capacity(
    dem: np.ndarray,
    nodata_mask: np.ndarray,
    dam_row: int,
    dam_col: int,
    cell_area_m2: float,
    *,
    max_level_m: float,
    min_level_m: float | None = None,
    n_levels: int = 60,
    catchment_mask: np.ndarray | None = None,
) -> ElevationAreaCapacity:
    """Integrate the DEM to build the elevation-area-capacity curve.

    At each sampled level the connected pool is re-delineated and its storage
    computed as `sum((level - bed) * cell_area)` over submerged cells. Summing
    the prism depth rather than multiplying area by depth is what makes this a
    real hypsometric integration rather than a bathtub approximation.
    """
    if min_level_m is None:
        # Start just above the deepest point of the pool at full level.
        full_pool = delineate_pool(
            dem, nodata_mask, dam_row, dam_col, max_level_m, catchment_mask
        )
        min_level_m = float(dem[full_pool].min()) if full_pool.any() else float(
            dem[~nodata_mask].min()
        )

    levels = np.linspace(min_level_m, max_level_m, n_levels)
    areas = np.zeros(n_levels)
    volumes = np.zeros(n_levels)

    for i, level in enumerate(levels):
        pool = delineate_pool(
            dem, nodata_mask, dam_row, dam_col, float(level), catchment_mask
        )
        if not pool.any():
            continue
        depths = level - dem[pool]
        areas[i] = pool.sum() * cell_area_m2
        volumes[i] = float(np.clip(depths, 0.0, None).sum()) * cell_area_m2

    # The curve must be monotonic to be invertible during routing. Numerical
    # non-monotonicity here would mean the delineation jumped basins, which is
    # a bug worth surfacing rather than smoothing away silently.
    if np.any(np.diff(volumes) < -1e-6):
        log.warning(
            "elevation-area-capacity curve is not monotonic; "
            "the pool delineation may be jumping between basins"
        )
        volumes = np.maximum.accumulate(volumes)
        areas = np.maximum.accumulate(areas)

    return ElevationAreaCapacity(
        levels_m=levels,
        areas_m2=areas,
        volumes_m3=volumes,
        cell_area_m2=cell_area_m2,
        dam_elevation_m=float(dem[dam_row, dam_col]),
    )


def detect_water_surface(dem: np.ndarray, pool_mask: np.ndarray) -> float | None:
    """Elevation of the flat water surface inside the delineated pool, m MSL.

    A DSM over an impounded reservoir returns the water surface at acquisition
    time, which shows up as a conspicuously flat plateau. We find it as the
    modal elevation of the pool, binned at 1 m: a real hillside has no single
    elevation that thousands of cells share, a lake does.

    Returns None when no elevation dominates, which means the pool is not
    impounded in this DEM and its bathymetry is already real.
    """
    if not pool_mask.any():
        return None
    values = dem[pool_mask]
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < 1.0:
        return float(np.median(values))
    bins = np.arange(np.floor(lo), np.ceil(hi) + 1.0, 1.0)
    counts, edges = np.histogram(values, bins=bins)
    peak = int(np.argmax(counts))
    # A water surface holds a large share of the pool's cells. Below ~15% we
    # are looking at ordinary terrain, not a lake.
    if counts[peak] / counts.sum() < 0.15:
        return None
    return float((edges[peak] + edges[peak + 1]) / 2.0)


def reconstruct_bathymetry(
    curve: ElevationAreaCapacity,
    water_surface_m: float,
    target_storage_m3: float,
    *,
    shape_exponent: float = 2.0,
    n_levels: int = 25,
) -> tuple[ElevationAreaCapacity, dict[str, Any]]:
    """Extend the E-A-C curve below an observed water surface.

    A 30 m DSM cannot see through water, so the storage it yields for an
    impounded reservoir is only the prism between the water surface and FRL.
    At Tehri that is 639 MCM against a registered 3540 MCM: an 82% shortfall,
    and a dam break driven by 639 MCM would be dramatically too small.

    The submerged volume is reconstructed with the conic reservoir
    approximation standard in USBR/USACE practice and in the ICOLD reservoir
    sedimentation literature, in which area varies as a power of depth above
    the deepest bed point:

        A(z) = A_ws * ((z - z_bed) / (z_ws - z_bed)) ** m
        V(z) = A(z) * (z - z_bed) / (m + 1)

    m = 2 corresponds to a V-shaped valley and is the usual default for a gorge
    impoundment; m = 1 is a prismatic channel. The bed elevation is solved so
    total storage at FRL equals the registered value:

        z_bed = z_ws - V_missing * (m + 1) / A_ws

    This is a calibration, not a measurement. It is labelled as such in the
    returned metadata, and every result depending on reservoir volume carries
    that label. A real study would use a bathymetric survey.
    """
    area_ws = curve.area_at(water_surface_m)
    storage_visible = float(curve.volumes_m3[-1])
    missing = target_storage_m3 - storage_visible

    meta: dict[str, Any] = {
        "method": "conic reservoir approximation calibrated to registered gross storage",
        "water_surface_m": water_surface_m,
        "water_surface_area_km2": area_ws / 1e6,
        "shape_exponent_m": shape_exponent,
        "visible_storage_mcm": storage_visible / 1e6,
        "target_storage_mcm": target_storage_m3 / 1e6,
        "reconstructed_storage_mcm": missing / 1e6,
        "is_reconstruction": True,
    }

    if missing <= 0 or area_ws <= 0:
        meta["applied"] = False
        meta["reason"] = (
            "the DEM already yields at least the registered storage, so nothing is "
            "hidden beneath a water surface and no reconstruction is applied"
        )
        return curve, meta

    depth_below = missing * (shape_exponent + 1.0) / area_ws
    z_bed = water_surface_m - depth_below
    meta["applied"] = True
    meta["bed_elevation_m"] = z_bed
    meta["reconstructed_depth_m"] = depth_below

    # Sample the reconstructed prism from bed to water surface, then splice the
    # DEM-derived part above it back on unchanged.
    sub_levels = np.linspace(z_bed, water_surface_m, n_levels, endpoint=False)
    rel = (sub_levels - z_bed) / max(depth_below, 1e-9)
    sub_areas = area_ws * rel ** shape_exponent
    sub_volumes = sub_areas * (sub_levels - z_bed) / (shape_exponent + 1.0)

    above = curve.levels_m >= water_surface_m
    top_levels = curve.levels_m[above]
    top_areas = curve.areas_m2[above]
    # Shift the visible prism up by the reconstructed volume so the curve is
    # continuous and V(FRL) lands on the registered storage.
    top_volumes = curve.volumes_m3[above] - curve.volume_at(water_surface_m) + missing

    levels = np.concatenate([sub_levels, top_levels])
    areas = np.concatenate([sub_areas, np.maximum(top_areas, area_ws)])
    volumes = np.maximum.accumulate(np.concatenate([sub_volumes, top_volumes]))

    meta["final_storage_mcm"] = float(volumes[-1]) / 1e6

    return (
        ElevationAreaCapacity(
            levels_m=levels,
            areas_m2=areas,
            volumes_m3=volumes,
            cell_area_m2=curve.cell_area_m2,
            dam_elevation_m=curve.dam_elevation_m,
            method=(
                "DEM integration above the observed water surface, plus a conic "
                "reconstruction below it calibrated to the registered gross storage"
            ),
        ),
        meta,
    )


def build(
    dem: np.ndarray,
    nodata_mask: np.ndarray,
    dam_row: int,
    dam_col: int,
    cell_area_m2: float,
    frl_m: float,
    catalog_gross_storage_mcm: float | None,
    *,
    n_levels: int = 60,
    catchment_mask: np.ndarray | None = None,
    reconstruct: bool = True,
) -> ReservoirGeometry:
    """Delineate the pool, derive the curve, and cross-check against the catalog.

    `catchment_mask` should be the dam's upstream watershed (see
    `hydro.upstream_watershed`). Passing None is supported for tests on
    synthetic terrain but is not correct on a real reach.
    """
    curve = elevation_area_capacity(
        dem,
        nodata_mask,
        dam_row,
        dam_col,
        cell_area_m2,
        max_level_m=frl_m,
        n_levels=n_levels,
        catchment_mask=catchment_mask,
    )
    pool = delineate_pool(dem, nodata_mask, dam_row, dam_col, frl_m, catchment_mask)

    bathymetry: dict[str, Any] = {"applied": False}
    visible_storage_mcm = float(curve.volumes_m3[-1]) / 1e6 if len(curve.volumes_m3) else 0.0

    if reconstruct and catalog_gross_storage_mcm:
        water_surface = detect_water_surface(dem, pool)
        if water_surface is None:
            bathymetry = {
                "applied": False,
                "reason": "no flat water surface detected in the pool; the DEM appears to "
                          "predate impoundment, so its bathymetry is already real",
            }
        else:
            curve, bathymetry = reconstruct_bathymetry(
                curve, water_surface, catalog_gross_storage_mcm * 1e6
            )

    derived_m3 = curve.volume_at(frl_m)
    derived_mcm = derived_m3 / 1e6
    derived_area_km2 = pool.sum() * cell_area_m2 / 1e6

    diff_pct: float | None = None
    warnings: list[str] = []

    if bathymetry.get("applied"):
        warnings.append(
            f"Reservoir bathymetry is RECONSTRUCTED, not measured. The DEM is a surface "
            f"model that sees the water surface at {bathymetry['water_surface_m']:.1f} m "
            f"and nothing beneath it, yielding only {visible_storage_mcm:.0f} MCM of "
            f"visible storage. The submerged prism was fitted with a conic approximation "
            f"(exponent m={bathymetry['shape_exponent_m']:.0f}) calibrated so total "
            f"storage matches the registered {catalog_gross_storage_mcm:.0f} MCM, implying "
            f"a bed at {bathymetry['bed_elevation_m']:.0f} m MSL. Every volume-dependent "
            f"result inherits this assumption; a bathymetric survey would replace it."
        )

    if catalog_gross_storage_mcm:
        diff_pct = 100.0 * (derived_mcm - catalog_gross_storage_mcm) / catalog_gross_storage_mcm
        if abs(diff_pct) > 40.0:
            warnings.append(
                f"DEM-derived storage differs from the CWC NRLD value by {diff_pct:+.0f}%. "
                f"Likely causes, in order of probability: (1) the DEM is a surface model "
                f"that samples the reservoir water surface at acquisition time rather than "
                f"the pre-impoundment valley floor, so storage below that surface is "
                f"invisible and the derived figure is an UNDER-estimate; (2) the FRL in the "
                f"scenario is wrong; (3) the dam coordinate snapped into the wrong valley. "
                f"Do not quote depths from this run before resolving it."
            )
        elif abs(diff_pct) > 15.0:
            warnings.append(
                f"DEM-derived storage differs from the CWC NRLD value by {diff_pct:+.0f}%. "
                f"That is within the range expected from a 30 m DSM over an impounded "
                f"reservoir, but it should be quoted alongside any storage-dependent result."
            )
    else:
        warnings.append(
            "No catalog gross storage available, so the DEM-derived curve is unchecked. "
            "Treat storage-dependent outputs as unvalidated."
        )

    if derived_area_km2 <= 0:
        warnings.append(
            "The delineated pool is empty. The dam coordinate is probably not in the "
            "reservoir, or the FRL is below the local terrain."
        )

    return ReservoirGeometry(
        bathymetry=bathymetry,
        visible_storage_mcm=visible_storage_mcm,
        pool_mask=pool,
        curve=curve,
        frl_m=frl_m,
        derived_gross_storage_mcm=derived_mcm,
        catalog_gross_storage_mcm=catalog_gross_storage_mcm,
        storage_difference_pct=diff_pct,
        derived_area_km2=derived_area_km2,
        warnings=warnings,
    )
