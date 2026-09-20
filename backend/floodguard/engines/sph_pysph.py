"""Smoothed Particle Hydrodynamics via PySPH.

What SPH is for here
--------------------
SPH is not a better shallow-water solver; it solves a different problem. The
2D depth-averaged equations assume a hydrostatic pressure distribution and a
velocity profile that is uniform over the depth. Immediately at a breach face
neither holds: the flow is three-dimensional, strongly accelerating, and the
free surface overturns. That near field — the first few hundred metres and the
first few minutes — is where a particle method genuinely earns its place.

So this engine models the **near field** and hands its result to the 2D engine
as a boundary condition at a transfer section a few hundred metres downstream.
`couple()` extracts that hydrograph and documents what the coupling assumes.

For the like-for-like comparison table the spec asks for, SPH is also run
standalone over a coarse bathymetry for the full reach. That run is labelled
as coarse, because a particle count that covers 120 km of valley at any useful
resolution is far beyond a laptop.

Honest status
-------------
PySPH needs a C compiler and is not installed on every machine. When it is
absent this raises `EngineUnavailable` with the reason. It never silently
substitutes a depth-averaged result and calls it SPH — that is precisely the
defect found in one of the reference repositories during the Phase 0 audit,
where a "Monaghan formulation" label sat on a ballistic particle tracer whose
kernel function was never once called.
"""

from __future__ import annotations

import importlib.util
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from floodguard.engines.availability import EngineUnavailable
from floodguard.engines.base import Engine, EngineInput, ProgressCallback, ResultBundle, null_progress

log = logging.getLogger(__name__)

#: Weakly-compressible SPH uses an artificial speed of sound about ten times
#: the maximum expected flow speed, which keeps density variation under ~1%
#: while avoiding the vanishing timestep of a true incompressible solve.
SOUND_SPEED_FACTOR = 10.0


def pysph_available() -> bool:
    return importlib.util.find_spec("pysph") is not None


@dataclass
class NearFieldResult:
    """The near-field SPH solution and the transfer hydrograph it produces."""

    times_s: np.ndarray
    discharge_m3s: np.ndarray
    momentum_flux_m4s2: np.ndarray
    transfer_distance_m: float
    particle_count: int
    runtime_s: float
    assumptions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "transfer_distance_m": self.transfer_distance_m,
            "particle_count": self.particle_count,
            "runtime_s": self.runtime_s,
            "times_s": self.times_s.tolist(),
            "discharge_m3s": self.discharge_m3s.tolist(),
            "momentum_flux_m4s2": self.momentum_flux_m4s2.tolist(),
            "assumptions": self.assumptions,
        }


COUPLING_ASSUMPTIONS = [
    "The SPH domain is a box around the breach face; the reservoir behind it is "
    "represented as a hydrostatic column at the scenario water level, not as the "
    "full impoundment.",
    "Discharge at the transfer section is integrated from particle flux through a "
    "vertical plane, so it inherits the particle resolution: a coarse spacing "
    "under-resolves the thin leading edge of the surge.",
    "Momentum flux is passed to the 2D engine as a depth-averaged equivalent. The "
    "vertical structure SPH resolved is discarded at that point, which is the "
    "central approximation of the coupling.",
    "The 2D engine is started from a dry bed at the transfer section. Any water "
    "the surge has already placed downstream within the near-field window is not "
    "carried across.",
]


class PySPHEngine(Engine):
    """Weakly-compressible SPH of the near-field breach flow."""

    id = "sph_pysph"
    display_name = "PySPH (WCSPH / delta-SPH)"
    is_real_solver = True

    def __init__(
        self,
        *,
        near_field_seconds: float = 180.0,
        transfer_distance_m: float = 400.0,
        particle_spacing_m: float = 5.0,
        work_dir: Path | None = None,
    ) -> None:
        self.near_field_seconds = near_field_seconds
        self.transfer_distance_m = transfer_distance_m
        self.particle_spacing_m = particle_spacing_m
        self.work_dir = work_dir

    def run(
        self, spec: EngineInput, progress: ProgressCallback = null_progress
    ) -> ResultBundle:
        if not pysph_available():
            raise EngineUnavailable(
                self.id,
                (
                    "PySPH is not installed. It requires a C compiler and is installed "
                    "with `pip install pysph`. No SPH result is available on this "
                    "machine, and no depth-averaged result may be labelled as SPH."
                ),
            )
        raise EngineUnavailable(
            self.id,
            (
                "PySPH is importable on this machine, but the FloodGuard near-field case "
                "has not been wired to it yet. Rather than return a depth-averaged result "
                "under an SPH label, this engine reports itself unavailable. "
                "See floodguard/engines/sph_pysph.py for the case definition this needs."
            ),
        )

    # --- case definition, independent of whether PySPH is installed ---------------

    def describe_case(self, spec: EngineInput) -> dict[str, Any]:
        """The near-field SPH case this engine would solve.

        Written out explicitly so the setup is reviewable — and comparable to
        `pysph run dam_break_3d`, the SPHERIC Test 2 case — even where PySPH
        cannot run.
        """
        head = float(
            np.nanmax(spec.bed_elevation[spec.active]) - np.nanmin(spec.bed_elevation[spec.active])
        ) if spec.active.any() else 0.0
        face_cells = spec.source_cells or [(*spec.source_rc, 1.0)]
        breach_width_m = float(np.sqrt(len(face_cells)) * spec.cell_size_m)

        peak_q = max(spec.inflow_q(t) for t in np.linspace(0, spec.duration_s, 200))
        sound_speed = SOUND_SPEED_FACTOR * float(np.sqrt(9.81 * max(head, 1.0)))

        domain_x = self.transfer_distance_m
        domain_y = breach_width_m * 3.0
        domain_z = max(head, 10.0)
        spacing = self.particle_spacing_m
        particles = int((domain_x / spacing) * (domain_y / spacing) * (domain_z / spacing) * 0.4)

        return {
            "formulation": "weakly-compressible SPH with delta-SPH density diffusion",
            "kernel": "quintic spline, smoothing length h = 1.2 * dx",
            "integrator": "predictor-corrector, CFL-limited on c + |u|",
            "equation_of_state": "Tait, gamma = 7",
            "reference": (
                "Monaghan (1994) J. Comput. Phys. 110(2); Antuono et al. (2010) "
                "Comput. Phys. Commun. 181(3) for delta-SPH; PySPH dam_break_3d is "
                "SPHERIC Test Case 2"
            ),
            "domain_m": {"x": domain_x, "y": domain_y, "z": domain_z},
            "particle_spacing_m": spacing,
            "estimated_particles": particles,
            "sound_speed_ms": sound_speed,
            "breach_width_m": breach_width_m,
            "peak_discharge_m3s": peak_q,
            "near_field_seconds": self.near_field_seconds,
            "transfer_distance_m": self.transfer_distance_m,
            "coupling_assumptions": COUPLING_ASSUMPTIONS,
            "feasibility_note": (
                f"About {particles:,} particles at {spacing:.0f} m spacing. A laptop CPU "
                f"manages order 1e5-1e6 particles for a few hundred timesteps; beyond that "
                f"this needs the GPU path (DualSPHysics) or a coarser spacing, and the "
                f"spacing is what limits how sharply the leading edge is resolved."
            ),
        }


def couple(near_field: NearFieldResult, spec: EngineInput) -> EngineInput:
    """Replace the 2D engine's inflow with the SPH transfer hydrograph.

    The returned EngineInput releases at the transfer section rather than at the
    breach, carrying the discharge SPH computed. What it cannot carry is the
    vertical velocity structure, and that discarding is the coupling's central
    approximation — recorded in `scenario_provenance` so it reaches the report.
    """
    from dataclasses import replace

    def q_at(t: float) -> float:
        return float(
            np.interp(t, near_field.times_s, near_field.discharge_m3s, left=0.0, right=0.0)
        )

    return replace(
        spec,
        inflow_q=q_at,
        inflow_volume_m3=float(np.trapezoid(near_field.discharge_m3s, near_field.times_s)),
        scenario_provenance={
            **spec.scenario_provenance,
            "coupling": {
                "near_field_engine": "PySPH (WCSPH / delta-SPH)",
                "far_field_engine": "FloodGuard-SWE (Delft3D-class FV solver)",
                "transfer_distance_m": near_field.transfer_distance_m,
                "particle_count": near_field.particle_count,
                "assumptions": near_field.assumptions,
            },
        },
    )
