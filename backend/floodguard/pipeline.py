"""End-to-end orchestration: scenario in, inundation maps and exports out.

    floodguard simulate --scenario data/scenarios/tehri_bhagirathi.yaml

This is the headless path. The API drives exactly the same function, so
anything the dashboard shows can be reproduced from the command line — which
is what makes a result auditable.

Stages: preprocess (Phase 2) -> breach + routing (Phase 3) -> engines
(Phase 4) -> derived rasters and exports (Phase 5) -> impact (Phase 6).

Engine substitution is decided here, in one visible place, and recorded in the
result so the API, the UI badge and the PDF all read the same fact.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from floodguard.breach import parameters as breach_params
from floodguard.breach import routing
from floodguard.engines.availability import EngineUnavailable, resolve
from floodguard.engines.base import EngineInput, ResultBundle
from floodguard.engines.swe_fv import ShallowWaterFV
from floodguard.postprocess import exports, hazard
from floodguard.preprocess.pipeline import PreprocessResult, run_preprocess
from floodguard.scenario import Scenario

log = logging.getLogger(__name__)

ProgressFn = Callable[..., None]


def _noop(**_kwargs: Any) -> None:
    pass


@dataclass
class EngineRun:
    """One engine's result plus how it came to be chosen."""

    requested_id: str
    actual_id: str
    display_name: str
    is_real_solver: bool
    substituted: bool
    substitution_reason: str
    bundle: ResultBundle | None = None
    error: str | None = None

    def honesty_note(self) -> str:
        """The sentence that must appear in the UI and the PDF for this run."""
        if self.error:
            return f"{self.requested_id} failed: {self.error}"
        if not self.substituted:
            return f"Computed with {self.display_name}."
        return (
            f"{self.requested_id} was requested but is not available on this machine. "
            f"{self.substitution_reason} The result shown was computed with "
            f"{self.display_name}."
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "requested_engine": self.requested_id,
            "actual_engine": self.actual_id,
            "display_name": self.display_name,
            "is_real_solver": self.is_real_solver,
            "substituted": self.substituted,
            "substitution_reason": self.substitution_reason,
            "honesty_note": self.honesty_note(),
            "error": self.error,
        }
        if self.bundle:
            out["summary"] = self.bundle.summary()
        return out


@dataclass
class SimulationResult:
    """Everything one `simulate` produced."""

    scenario_id: str
    run_id: str
    out_dir: Path
    preprocess: PreprocessResult
    hydrograph: routing.Hydrograph
    breach_used: breach_params.BreachGeometry
    breach_predictions: list[breach_params.BreachGeometry]
    breach_spread: dict[str, Any]
    engine_runs: list[EngineRun] = field(default_factory=list)
    exported: list[exports.ExportResult] = field(default_factory=list)
    hazard_stats: dict[str, Any] = field(default_factory=dict)
    town_results: list[dict[str, Any]] = field(default_factory=list)
    runtime_s: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def primary(self) -> EngineRun | None:
        """The engine run the KPI cards default to."""
        for run in self.engine_runs:
            if run.bundle is not None:
                return run
        return None

    def summary(self) -> str:
        lines = [
            f"Simulation {self.run_id} for {self.scenario_id} in {self.runtime_s:.1f}s",
            "",
            self.hydrograph.summary(),
            "",
        ]
        for run in self.engine_runs:
            lines.append(run.honesty_note())
            if run.bundle:
                s = run.bundle.summary()
                lines.append(
                    f"  Flooded area  : {s['flooded_area_km2']:.2f} km2"
                    if s["flooded_area_km2"] is not None
                    else "  Flooded area  : —"
                )
                lines.append(
                    f"  Max depth     : {s['max_depth_m']:.2f} m"
                    if s["max_depth_m"] is not None
                    else "  Max depth     : — (not computed)"
                )
                lines.append(
                    f"  Max velocity  : {s['max_velocity_ms']:.2f} m/s"
                    if s["max_velocity_ms"] is not None
                    else "  Max velocity  : — (not computed)"
                )
                lines.append(
                    f"  First arrival : {s['earliest_arrival_min']:.1f} min"
                    if s["earliest_arrival_min"] is not None
                    else "  First arrival : — (nothing flooded)"
                )
                lines.append(
                    f"  Runtime       : {s['runtime_s']:.1f}s over {s['steps']:,} steps"
                )
            lines.append("")

        if self.town_results:
            lines.append("Arrival at named locations (sorted by lead time):")
            lines.append(f"  {'LOCATION':<20} {'ARRIVAL':>10} {'DEPTH m':>9} {'VEL m/s':>9}")
            for t in self.town_results:
                arrival = (
                    f"{t['arrival_min']:.0f} min" if t["arrival_min"] is not None else "—"
                )
                depth = f"{t['max_depth_m']:.2f}" if t["max_depth_m"] else "—"
                vel = f"{t['max_velocity_ms']:.2f}" if t["max_velocity_ms"] else "—"
                lines.append(f"  {t['name']:<20} {arrival:>10} {depth:>9} {vel:>9}")
            lines.append("")

        if self.exported:
            lines.append("Exports:")
            for e in self.exported:
                lines.append(f"  {e.format:<8} {e.path.name} ({e.size_bytes / 1024:.0f} KB)")
            lines.append("")

        if self.warnings:
            lines.append("WARNINGS — read before quoting any number from this run:")
            for w in self.warnings:
                lines.append(f"  - {w}")
            lines.append("")

        lines.append(f"Outputs: {self.out_dir}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "run_id": self.run_id,
            "out_dir": str(self.out_dir),
            "runtime_s": self.runtime_s,
            "breach": {
                "used": self.breach_used.to_dict(),
                "predictions": [p.to_dict() for p in self.breach_predictions],
                "spread": self.breach_spread,
            },
            "hydrograph": {
                "peak_discharge_m3s": self.hydrograph.peak_discharge_m3s,
                "time_to_peak_s": self.hydrograph.time_to_peak_s,
                "total_volume_m3": self.hydrograph.total_volume_m3,
                "mass_error": self.hydrograph.mass_error,
                "provenance": self.hydrograph.provenance,
            },
            "engines": [r.to_dict() for r in self.engine_runs],
            "hazard": self.hazard_stats,
            "towns": self.town_results,
            "exports": [e.to_dict() for e in self.exported],
            "warnings": self.warnings,
        }


def _engine_classes() -> dict[str, type]:
    """Engine registry, imported lazily.

    Adapters import netCDF4/xarray at module scope in places, so importing them
    eagerly would make an optional dependency a hard one.
    """
    from floodguard.engines.anuga_engine import AnugaEngine
    from floodguard.engines.delft3d_adapter import Delft3DAdapter
    from floodguard.engines.dualsphysics_adapter import DualSPHysicsAdapter
    from floodguard.engines.sph_pysph import PySPHEngine

    return {
        "swe_fv": ShallowWaterFV,
        "anuga": AnugaEngine,
        "delft3d": Delft3DAdapter,
        "sph_pysph": PySPHEngine,
        "dualsphysics": DualSPHysicsAdapter,
    }


#: Engines whose input deck is a deliverable in its own right, generated
#: whether or not the solver exists on this machine.
DECK_WRITERS = ("delft3d", "dualsphysics")


def write_engine_decks(
    requested: list[str], spec: EngineInput, out_dir: Path
) -> dict[str, dict[str, Path]]:
    """Write the input decks for any requested external engine.

    "Here is the Delft3D input deck our tool generated from a DEM and a dam
    record" is a legitimate deliverable on its own — it is what a hydraulics
    team would otherwise assemble by hand over days. So the deck is produced
    even when the solver is absent, and offered for download.
    """
    written: dict[str, dict[str, Path]] = {}

    for engine_id in requested:
        if engine_id not in DECK_WRITERS:
            continue
        try:
            if engine_id == "delft3d":
                from floodguard.engines.delft3d_adapter import write_case

                written[engine_id] = write_case(spec, out_dir / "delft3d_case")
            else:
                from floodguard.engines.dualsphysics_adapter import write_case as write_dsph

                written[engine_id] = write_dsph(spec, out_dir / "dualsphysics_case", 5.0)
        except Exception as exc:  # noqa: BLE001 - a deck failure must not stop the solve
            log.warning("could not write the %s deck: %s", engine_id, exc)

    return written


ENGINE_CLASSES: dict[str, type] = {}


def _select_engine(engine_id: str) -> EngineRun:
    """Resolve a requested engine to one that can actually run.

    This is the single place substitution is decided. Every downstream consumer
    reads the EngineRun, so the API response, the UI badge and the PDF cannot
    disagree about what was executed.
    """
    status = resolve(engine_id)

    classes = _engine_classes()
    if status.available and engine_id in classes:
        return EngineRun(
            requested_id=engine_id,
            actual_id=engine_id,
            display_name=status.display_name,
            is_real_solver=status.is_real_solver,
            substituted=False,
            substitution_reason="",
        )

    substitute_id = status.substitute_id
    if not substitute_id or substitute_id not in classes:
        raise EngineUnavailable(
            engine_id,
            f"{status.detail} No implemented substitute is available either.",
        )

    sub_status = resolve(substitute_id)
    return EngineRun(
        requested_id=engine_id,
        actual_id=substitute_id,
        display_name=sub_status.display_name,
        is_real_solver=sub_status.is_real_solver,
        substituted=True,
        substitution_reason=status.detail,
    )


def crop_to_active(
    active: np.ndarray, transform, margin: int = 2
) -> tuple[slice, slice, Any]:
    """Bounding box of the active mask, plus the transform of that window.

    The routing corridor typically occupies a small fraction of the fetched
    raster — 12.7% for Tehri — but a dense solver still walks every cell and,
    more importantly, still moves every cell through the memory bus. Cropping
    the Tehri domain from 8.67 to 2.52 million cells removes 71% of that
    traffic for no loss of accuracy, because the discarded cells were never
    active.

    Results stay in the cropped frame rather than being mapped back. The
    returned transform carries the georeferencing, so every export, town lookup
    and tile is correct without a second coordinate system to keep in step.
    """
    from rasterio.transform import Affine

    rows, cols = np.nonzero(active)
    if rows.size == 0:
        return slice(0, active.shape[0]), slice(0, active.shape[1]), transform

    r0 = max(int(rows.min()) - margin, 0)
    r1 = min(int(rows.max()) + margin + 1, active.shape[0])
    c0 = max(int(cols.min()) - margin, 0)
    c1 = min(int(cols.max()) + margin + 1, active.shape[1])

    window_transform = transform * Affine.translation(c0, r0)
    return slice(r0, r1), slice(c0, c1), window_transform


def breach_face_cells(
    active: np.ndarray,
    release_rc: tuple[int, int],
    breach_width_m: float,
    cell_size_m: float,
) -> list[tuple[int, int, float]]:
    """Cells the breach discharges through, with weights summing to 1.

    The footprint is a disc of radius half the breach width, centred on the
    release point and clipped to the active domain. Weights fall off linearly
    from the centre, which approximates a breach discharging most strongly at
    its middle.

    A breach narrower than one cell still gets its own cell, so the boundary
    condition never disappears; it is simply resolved as a point, and the
    provenance records how many cells carried it.
    """
    r0, c0 = release_rc
    radius_cells = max(breach_width_m / (2.0 * cell_size_m), 0.5)
    reach = int(np.ceil(radius_cells))

    cells: list[tuple[int, int, float]] = []
    for dr in range(-reach, reach + 1):
        for dc in range(-reach, reach + 1):
            r, c = r0 + dr, c0 + dc
            if not (0 <= r < active.shape[0] and 0 <= c < active.shape[1]):
                continue
            if not active[r, c]:
                continue
            distance = float(np.hypot(dr, dc))
            if distance > radius_cells:
                continue
            cells.append((r, c, max(1.0 - distance / (radius_cells + 1e-9), 0.1)))

    return cells or [(r0, c0, 1.0)]


def _build_engine_input(
    scenario: Scenario,
    pre: PreprocessResult,
    hydrograph: routing.Hydrograph,
    breach_width_m: float = 0.0,
) -> tuple[EngineInput, tuple[slice, slice]]:
    """Assemble the solver's input, cropped to the active corridor."""
    bed_full = np.where(pre.dem < -1000, np.nan, pre.dem)
    active_full = pre.corridor & np.isfinite(bed_full)

    # The release point is the snapped dam cell — which sits just downstream of
    # the embankment, where the breach outflow actually enters the valley.
    src = pre.dam_snapped_rc
    if not active_full[src]:
        # Nudge onto the nearest active cell along the traced path.
        for rc in pre.path_rc[:50]:
            if active_full[rc[0], rc[1]]:
                src = (int(rc[0]), int(rc[1]))
                break

    rs, cs, transform = crop_to_active(active_full, pre.transform)
    bed = np.ascontiguousarray(bed_full[rs, cs])
    active = np.ascontiguousarray(active_full[rs, cs])
    manning = np.ascontiguousarray(pre.manning[rs, cs])
    src_cropped = (src[0] - rs.start, src[1] - cs.start)

    log.info(
        "compute domain cropped from %s to %s (%.0f%% fewer cells)",
        active_full.shape,
        active.shape,
        100 * (1 - active.size / active_full.size),
    )

    face = breach_face_cells(active, src_cropped, breach_width_m, pre.cell_size_m)

    spec = EngineInput(
        bed_elevation=bed,
        manning_n=manning,
        active=active,
        cell_size_m=pre.cell_size_m,
        transform=transform,
        crs=pre.crs,
        source_rc=src_cropped,
        source_cells=face,
        inflow_q=hydrograph.q_at,
        inflow_volume_m3=hydrograph.total_volume_m3,
        duration_s=scenario.solver.duration_hours * 3600.0,
        cfl=scenario.solver.cfl,
        dry_tolerance_m=scenario.solver.dry_tolerance_m,
        wet_threshold_m=scenario.solver.wet_threshold_m,
        output_interval_s=scenario.solver.output_interval_s,
        second_order=scenario.solver.second_order,
        max_steps=scenario.solver.max_steps,
        scenario_provenance={
            **scenario.provenance_inputs(),
            "compute_window": {
                "rows": [rs.start, rs.stop],
                "cols": [cs.start, cs.stop],
                "full_grid": list(active_full.shape),
                "cropped_grid": list(active.shape),
            },
        },
    )
    return spec, (rs, cs)


def _town_table(
    scenario: Scenario, crs: str, transform, shape, bundle: ResultBundle
) -> list[dict[str, Any]]:
    """Depth, velocity and arrival time per named town, sorted by lead time.

    Sorted by arrival because that is the order an evacuation is executed in.
    A table sorted alphabetically is a table nobody can act on.
    """
    from pyproj import Transformer
    from rasterio.transform import rowcol

    to_grid = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    rows = []
    for town in scenario.towns:
        x, y = to_grid.transform(town.lon, town.lat)
        r, c = rowcol(transform, x, y)
        sample = bundle.sample_at(int(r), int(c))
        rows.append(
            {
                "name": town.name,
                "lon": town.lon,
                "lat": town.lat,
                "population": town.population,
                "population_source": town.population_source,
                "approx_km_downstream": town.approx_km_downstream,
                **sample,
                "in_domain": 0 <= r < shape[0] and 0 <= c < shape[1],
            }
        )

    rows.sort(key=lambda t: (t["arrival_min"] is None, t["arrival_min"] or 0.0))
    return rows


def simulate(
    scenario: Scenario,
    data_dir: Path,
    *,
    progress: ProgressFn = _noop,
    run_id: str | None = None,
    engines: list[str] | None = None,
    reuse_preprocess: bool = True,
    export: bool = True,
) -> SimulationResult:
    """Run the full pipeline for one scenario."""
    started = time.perf_counter()
    run_id = run_id or f"{scenario.id}_{int(time.time())}"
    out_dir = data_dir / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []

    # --- Phase 2 ---
    progress(fraction=0.02, phase="preprocess", message="conditioning the DEM")
    pre = run_preprocess(scenario, data_dir, write_rasters=not reuse_preprocess)
    warnings.extend(pre.warnings)

    if pre.reservoir is None:
        raise RuntimeError(
            "reservoir geometry could not be derived, so there is no storage to "
            "release. Check the preprocess warnings."
        )

    # --- Phase 3 ---
    progress(fraction=0.15, phase="breach", message="solving the breach hydrograph")
    used, predictions, spread = breach_params.resolve(scenario)
    crest = scenario.dam.crest_elevation_m or scenario.initial_level_m
    hydrograph = routing.route(
        pre.reservoir.curve,
        used,
        initial_level_m=scenario.initial_level_m,
        crest_elevation_m=crest,
        scenario_type=scenario.scenario_type,
        shape=scenario.breach.shape,
        growth=scenario.breach.growth,
        duration_s=scenario.solver.duration_hours * 3600.0,
        inflow_m3s=scenario.reservoir.inflow_m3s,
    )
    warnings.extend(hydrograph.warnings)
    if spread["width_m"]["spread_ratio"] > 2.0:
        warnings.append(
            f"The breach-parameter models disagree by a factor of "
            f"{spread['width_m']['spread_ratio']:.1f} on width. Breach geometry, not the "
            f"solver, is the dominant uncertainty in this result."
        )

    result = SimulationResult(
        scenario_id=scenario.id,
        run_id=run_id,
        out_dir=out_dir,
        preprocess=pre,
        hydrograph=hydrograph,
        breach_used=used,
        breach_predictions=predictions,
        breach_spread=spread,
        warnings=warnings,
    )

    # --- Phase 4 ---
    spec, window = _build_engine_input(
        scenario, pre, hydrograph, breach_width_m=used.width_m
    )
    requested = engines or scenario.engines

    decks = write_engine_decks(requested, spec, out_dir)
    for engine_id, files in decks.items():
        log.info("wrote a %s input deck: %s", engine_id, sorted(f.name for f in files.values()))
        warnings.append(
            f"A complete {engine_id} input deck was generated at "
            f"{out_dir / (engine_id + '_case')} and is downloadable, independently of "
            f"whether that solver ran here."
        )

    for i, engine_id in enumerate(requested):
        try:
            run = _select_engine(engine_id)
        except EngineUnavailable as exc:
            result.engine_runs.append(
                EngineRun(
                    requested_id=engine_id,
                    actual_id="",
                    display_name="unavailable",
                    is_real_solver=False,
                    substituted=False,
                    substitution_reason="",
                    error=str(exc),
                )
            )
            warnings.append(str(exc))
            continue

        if run.substituted:
            warnings.append(run.honesty_note())

        base = 0.2 + 0.6 * i / max(len(requested), 1)
        span = 0.6 / max(len(requested), 1)

        def engine_progress(*, fraction, phase, message, **extra):
            progress(
                fraction=base + span * fraction,
                phase=f"{run.actual_id}:{phase}",
                message=message,
                **extra,
            )

        engine = _engine_classes()[run.actual_id]()
        try:
            run.bundle = engine.run(spec, engine_progress)
            warnings.extend(run.bundle.warnings)
        except Exception as exc:  # noqa: BLE001 - one engine failing must not kill the run
            log.exception("engine %s failed", run.actual_id)
            run.error = f"{type(exc).__name__}: {exc}"
            warnings.append(f"Engine {run.actual_id} failed: {run.error}")

        result.engine_runs.append(run)

    primary = result.primary
    if primary is None or primary.bundle is None:
        result.runtime_s = time.perf_counter() - started
        warnings.append("No engine produced a result, so no outputs were written.")
        return result

    bundle = primary.bundle

    # --- Phase 5 ---
    progress(fraction=0.85, phase="postprocess", message="deriving rasters and exports")

    result.hazard_stats = hazard.statistics(
        bundle.max_depth,
        bundle.max_velocity,
        pre.cell_size_m**2,
        scenario.solver.wet_threshold_m,
    )
    result.town_results = _town_table(
        scenario, bundle.crs, bundle.transform, bundle.max_depth.shape, bundle
    )

    provenance = {
        **bundle.provenance,
        "run_id": run_id,
        "scenario": scenario.id,
        "engine_honesty_note": primary.honesty_note(),
        "reservoir": pre.reservoir.to_dict()["bathymetry"],
        "breach": used.to_dict(),
        "hydrograph_peak_m3s": hydrograph.peak_discharge_m3s,
        "preprocess_warnings": pre.warnings,
    }

    if export:
        result.exported = _write_exports(
            out_dir, bundle, pre, provenance, hydrograph, scenario, primary
        )

    (out_dir / "result.json").write_text(
        json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8"
    )

    result.runtime_s = time.perf_counter() - started
    progress(fraction=1.0, phase="done", message=f"complete in {result.runtime_s:.1f}s")
    return result


def _write_exports(
    out_dir: Path,
    bundle: ResultBundle,
    pre: PreprocessResult,
    provenance: dict[str, Any],
    hydrograph: routing.Hydrograph,
    scenario: Scenario,
    run: EngineRun,
) -> list[exports.ExportResult]:
    """Write every output format the spec requires."""
    written: list[exports.ExportResult] = []

    for name, array, desc in (
        ("max_depth", bundle.max_depth, "maximum water depth, m"),
        ("max_velocity", bundle.max_velocity, "maximum depth-averaged velocity, m/s"),
        ("max_hazard", bundle.max_hazard, "maximum depth x velocity, m2/s"),
        ("arrival_time", bundle.arrival_time_s, "first time depth exceeded the threshold, s"),
    ):
        written.append(
            exports.write_cog(
                out_dir / f"{name}.tif",
                array,
                bundle.transform,
                bundle.crs,
                provenance=provenance,
                description=desc,
            )
        )

    gdf = exports.polygonize_depth_bands(
        bundle.max_depth,
        bundle.max_velocity,
        bundle.arrival_time_s,
        bundle.transform,
        bundle.crs,
        cell_area_m2=bundle.cell_size_m**2,
        engine_name=run.display_name,
    )

    if not gdf.empty:
        written.append(exports.write_geojson(gdf, out_dir / "inundation.geojson", provenance))
        written.append(
            exports.write_shapefile_zip(gdf, out_dir / "inundation_shp.zip", provenance)
        )
        kml = exports.write_kml(
            gdf, out_dir / "inundation.kml", provenance, name=f"{scenario.name} inundation"
        )
        written.append(kml)
        written.append(exports.write_kmz(kml.path, out_dir / "inundation.kmz"))

    written.append(
        exports.write_timeseries_csv(
            out_dir / "breach_hydrograph.csv",
            hydrograph.time_s,
            {
                "discharge_m3s": hydrograph.discharge_m3s,
                "reservoir_level_m": hydrograph.water_level_m,
                "breach_width_m": hydrograph.breach_width_m,
            },
            {**provenance, **hydrograph.provenance},
        )
    )

    return written
