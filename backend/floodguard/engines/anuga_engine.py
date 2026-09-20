"""ANUGA as an independent cross-check of FloodGuard-SWE.

ANUGA is a published, peer-reviewed, extensively validated 2D shallow-water
solver from Geoscience Australia (Apache-2.0). Its value here is precisely that
it is *not ours*: agreement between an independent published solver and our own
on the same scenario is third-party evidence that our answer is right, and it is
far stronger evidence than any amount of our own testing.

It solves the same equations as `swe_fv` — depth-averaged shallow water with a
Manning friction term — using a finite-volume scheme on an unstructured
triangular mesh rather than a Cartesian grid. The two therefore differ in mesh,
in reconstruction and in Riemann solver, which is what makes the comparison
meaningful instead of circular.

ANUGA ships on conda-forge only. Where it is absent this raises
`EngineUnavailable`; no Cartesian result is ever labelled as ANUGA.
"""

from __future__ import annotations

import importlib.util
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from floodguard.engines.availability import EngineUnavailable
from floodguard.engines.base import Engine, EngineInput, ProgressCallback, ResultBundle, null_progress

log = logging.getLogger(__name__)


def anuga_available() -> bool:
    return importlib.util.find_spec("anuga") is not None


class AnugaEngine(Engine):
    """Geoscience Australia's ANUGA, wrapped behind the Engine interface."""

    id = "anuga"
    display_name = "ANUGA (2D shallow-water, Geoscience Australia)"
    is_real_solver = True

    def __init__(self, work_dir: Path | None = None, mesh_area_m2: float | None = None) -> None:
        self.work_dir = work_dir
        self.mesh_area_m2 = mesh_area_m2

    def run(
        self, spec: EngineInput, progress: ProgressCallback = null_progress
    ) -> ResultBundle:
        if not anuga_available():
            raise EngineUnavailable(
                self.id,
                (
                    "ANUGA is not installed. It ships on conda-forge only: "
                    "`conda install -c conda-forge anuga`. No independent cross-check is "
                    "available on this machine, and no FloodGuard-SWE result may be "
                    "labelled as ANUGA."
                ),
            )

        import anuga  # noqa: F401  (imported for its side effects and version)

        work = Path(self.work_dir or "anuga_case")
        work.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()

        progress(fraction=0.05, phase="anuga", message="building the triangular mesh")
        domain = self._build_domain(spec, work)

        progress(fraction=0.2, phase="anuga", message="evolving")
        duration = spec.duration_s
        interval = spec.output_interval_s

        max_depth = np.zeros(spec.shape)
        max_speed = np.zeros(spec.shape)
        arrival = np.full(spec.shape, -1.0)

        for t in domain.evolve(yieldstep=interval, finaltime=duration):
            progress(
                fraction=0.2 + 0.7 * (t / duration),
                phase="anuga",
                message=f"t = {t / 3600:.2f} h",
                t_seconds=float(t),
            )
            depth, speed = self._sample_to_grid(domain, spec)
            np.maximum(max_depth, depth, out=max_depth)
            np.maximum(max_speed, speed, out=max_speed)
            newly_wet = (depth >= spec.wet_threshold_m) & (arrival < 0)
            arrival[newly_wet] = float(t)

        runtime = time.perf_counter() - started
        return ResultBundle(
            engine_id=self.id,
            display_name=self.display_name,
            is_real_solver=True,
            max_depth=max_depth,
            max_velocity=max_speed,
            max_hazard=max_depth * max_speed,
            arrival_time_s=arrival,
            transform=spec.transform,
            crs=spec.crs,
            cell_size_m=spec.cell_size_m,
            runtime_s=runtime,
            provenance={
                "engine": self.display_name,
                "engine_id": self.id,
                "is_real_solver": True,
                "scheme": (
                    "ANUGA finite volume on an unstructured triangular mesh. Chosen as an "
                    "INDEPENDENT check: different mesh, different reconstruction, "
                    "different Riemann solver from FloodGuard-SWE, so agreement is "
                    "evidence rather than a tautology."
                ),
                "licence": "Apache-2.0, Geoscience Australia",
                "reference": (
                    "Roberts, Nielsen, Gray & Sexton, ANUGA User Manual, "
                    "Geoscience Australia"
                ),
                "mesh_max_area_m2": self.mesh_area_m2 or spec.cell_size_m**2,
                **spec.scenario_provenance,
            },
        )

    # --- mesh and sampling ---------------------------------------------------------

    def _build_domain(self, spec: EngineInput, work: Path):
        """Build an ANUGA domain over the active corridor.

        The mesh is refined to roughly one triangle per raster cell, which keeps
        the two engines at comparable resolution. Comparing a fine ANUGA mesh
        against a coarse Cartesian grid would measure the resolution difference
        rather than the solver difference.
        """
        import anuga
        from rasterio.transform import xy

        rows, cols = spec.shape
        r0, c0 = 0, 0
        x0, y0 = xy(spec.transform, rows - 1, c0, offset="ll")
        x1, y1 = xy(spec.transform, r0, cols - 1, offset="ur")

        max_area = self.mesh_area_m2 or (spec.cell_size_m**2)
        points, vertices, boundary = anuga.rectangular_cross(
            int((x1 - x0) / spec.cell_size_m),
            int((y1 - y0) / spec.cell_size_m),
            len1=float(x1 - x0),
            len2=float(y1 - y0),
            origin=(float(x0), float(y0)),
        )

        domain = anuga.Domain(points, vertices, boundary)
        domain.set_name("floodguard")
        domain.set_datadir(str(work))
        domain.set_flow_algorithm("DE1")

        # Bed elevation and roughness sampled from our rasters.
        domain.set_quantity("elevation", lambda x, y: self._sample(spec, spec.bed_elevation, x, y))
        domain.set_quantity("friction", lambda x, y: self._sample(spec, spec.manning_n, x, y))
        domain.set_quantity("stage", expression="elevation")

        # Transmissive downstream, reflective elsewhere — the same choice the
        # native solver makes, so the boundary treatment is not a difference.
        domain.set_boundary(
            {
                "left": anuga.Reflective_boundary(domain),
                "right": anuga.Transmissive_boundary(domain),
                "top": anuga.Reflective_boundary(domain),
                "bottom": anuga.Reflective_boundary(domain),
            }
        )

        # The breach as an inlet operator over the breach face.
        face = spec.source_cells or [(*spec.source_rc, 1.0)]
        centre = xy(spec.transform, *face[len(face) // 2][:2])
        radius = max(np.sqrt(len(face)) * spec.cell_size_m / 2.0, spec.cell_size_m)
        anuga.Inlet_operator(
            domain,
            anuga.Region(domain, center=centre, radius=float(radius)),
            Q=spec.inflow_q,
        )
        return domain

    @staticmethod
    def _sample(spec: EngineInput, raster: np.ndarray, x, y) -> np.ndarray:
        from rasterio.transform import rowcol

        rows, cols = raster.shape
        out = np.zeros(np.shape(x), dtype=float)
        rr, cc = rowcol(spec.transform, np.asarray(x), np.asarray(y))
        rr = np.clip(np.asarray(rr), 0, rows - 1)
        cc = np.clip(np.asarray(cc), 0, cols - 1)
        values = raster[rr, cc]
        out[:] = np.where(np.isfinite(values), values, 0.0)
        return out

    @staticmethod
    def _sample_to_grid(domain, spec: EngineInput) -> tuple[np.ndarray, np.ndarray]:
        """Project ANUGA's triangle-centred quantities back onto our raster."""
        from rasterio.transform import rowcol

        centroids = domain.get_centroid_coordinates(absolute=True)
        depth = domain.quantities["height"].centroid_values
        xmom = domain.quantities["xmomentum"].centroid_values
        ymom = domain.quantities["ymomentum"].centroid_values

        safe = np.maximum(depth, 1e-6)
        speed = np.hypot(xmom, ymom) / safe

        rows, cols = spec.shape
        grid_depth = np.zeros((rows, cols))
        grid_speed = np.zeros((rows, cols))

        rr, cc = rowcol(spec.transform, centroids[:, 0], centroids[:, 1])
        rr = np.asarray(rr)
        cc = np.asarray(cc)
        inside = (rr >= 0) & (rr < rows) & (cc >= 0) & (cc < cols)

        np.maximum.at(grid_depth, (rr[inside], cc[inside]), depth[inside])
        np.maximum.at(grid_speed, (rr[inside], cc[inside]), speed[inside])
        return grid_depth, grid_speed
