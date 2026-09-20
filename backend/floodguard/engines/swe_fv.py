"""FloodGuard-SWE: second-order finite-volume 2D shallow-water solver.

This is the workhorse that actually powers the demo. When Delft3D binaries are
absent, this is what runs, and it is labelled
`FloodGuard-SWE (Delft3D-class FV solver)` everywhere — never "Delft3D".

Scheme
------
Godunov finite volume on a uniform Cartesian grid:

* MUSCL reconstruction with a minmod limiter (second order, TVD)
* HLLC approximate Riemann solver
* Hydrostatic reconstruction (Audusse et al. 2004) for well-balancing
* Semi-implicit Manning friction
* Wetting/drying with a depth tolerance and desingularised velocity
* Adaptive timestep from the CFL condition
* SSP-RK2 (Heun) time integration when second order is enabled

Governing equations, in conservative form:

    d(h)/dt   + d(hu)/dx + d(hv)/dy = S_mass
    d(hu)/dt  + d(hu^2 + gh^2/2)/dx + d(huv)/dy = -gh dz/dx - g n^2 u|u| / h^(1/3)
    d(hv)/dt  + d(huv)/dx + d(hv^2 + gh^2/2)/dy = -gh dz/dy - g n^2 v|u| / h^(1/3)

Verification against Ritter, Stoker, lake-at-rest and mass conservation lives
in `floodguard.validation` and runs under `make validate`. Nothing here should
be trusted before those plots exist.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from floodguard.engines import _swe_kernels as k
from floodguard.engines.base import Engine, EngineInput, ProgressCallback, ResultBundle, null_progress

log = logging.getLogger(__name__)


class ShallowWaterFV(Engine):
    """Our own 2D SWE solver."""

    id = "swe_fv"
    display_name = "FloodGuard-SWE (Delft3D-class FV solver)"
    is_real_solver = True
    version = "0.1.0"

    def run(
        self, spec: EngineInput, progress: ProgressCallback = null_progress
    ) -> ResultBundle:
        started = time.perf_counter()
        rows, cols = spec.shape
        dx = dy = float(spec.cell_size_m)
        dry_tol = float(spec.dry_tolerance_m)

        z = np.ascontiguousarray(spec.bed_elevation, dtype=np.float64)
        manning = np.ascontiguousarray(spec.manning_n, dtype=np.float64)
        active = np.ascontiguousarray(spec.active, dtype=np.bool_)

        # A nodata cell must never be active: its bed elevation is meaningless
        # and would generate an enormous fictional pressure gradient.
        active &= np.isfinite(z)

        h = (
            np.ascontiguousarray(spec.initial_depth, dtype=np.float64)
            if spec.initial_depth is not None
            else np.zeros((rows, cols), dtype=np.float64)
        )
        h[~active] = 0.0
        hu = np.zeros((rows, cols), dtype=np.float64)
        hv = np.zeros((rows, cols), dtype=np.float64)

        # Scratch arrays, allocated once. Reallocating per step on a multi-
        # million-cell grid dominates the runtime.
        work = Work(z, active)

        max_depth = np.zeros_like(h)
        max_speed = np.zeros_like(h)
        max_hazard = np.zeros_like(h)
        arrival = np.full((rows, cols), -1.0, dtype=np.float64)

        src_r, src_c = spec.source_rc
        if not active[src_r, src_c]:
            raise ValueError(
                f"the breach release cell ({src_r}, {src_c}) is outside the active "
                f"domain. The corridor mask or the snapped dam point is wrong."
            )
        cell_area = spec.cell_area_m2

        frames: list[tuple[float, np.ndarray]] = []
        warnings: list[str] = []

        t = 0.0
        step = 0
        next_output = 0.0
        volume_in = 0.0
        initial_volume = k.total_volume(h, active, cell_area)

        second_order = bool(spec.second_order)
        progress(fraction=0.0, phase="solve", message="starting")

        while t < spec.duration_s and step < spec.max_steps:
            # --- timestep from the CFL condition ---
            fastest = k.max_wave_speed(h, hu, hv, active, dry_tol)
            if fastest < 1e-9:
                # Nothing is moving yet. Step forward far enough to let the
                # inflow do something, but not past the next output frame.
                dt = min(spec.output_interval_s, spec.duration_s - t)
            else:
                dt = spec.cfl * min(dx, dy) / fastest
                dt = min(dt, spec.duration_s - t)
            if dt <= 0:
                break

            # --- inflow: the breach hydrograph, as a mass source ---
            q = spec.inflow_q(t + 0.5 * dt)
            if q > 0.0:
                added = q * dt / cell_area
                h[src_r, src_c] += added
                volume_in += q * dt

            dt = self._step(
                h, hu, hv, z, manning, active, work, dx, dy, dt, dry_tol,
                second_order,
            )

            k.apply_friction(h, hu, hv, manning, active, dt, dry_tol)

            t += dt
            step += 1

            k.update_maxima(
                h, hu, hv, z, max_depth, max_speed, max_hazard, arrival, active,
                t, spec.wet_threshold_m, dry_tol,
            )

            if t >= next_output:
                frames.append((t, max_depth.copy() if False else h.copy()))
                next_output += spec.output_interval_s
                wet_cells = int((h > spec.wet_threshold_m).sum())
                progress(
                    fraction=min(t / spec.duration_s, 1.0),
                    phase="solve",
                    message=(
                        f"t={t / 3600:.2f} h  step={step}  dt={dt:.3f} s  "
                        f"wet={wet_cells:,} cells  peak={h.max():.2f} m"
                    ),
                    t_seconds=t,
                    step=step,
                    dt=dt,
                    wet_cells=wet_cells,
                )

        runtime = time.perf_counter() - started

        if step >= spec.max_steps:
            warnings.append(
                f"The solver hit its {spec.max_steps:,} step cap at t={t / 3600:.2f} h of "
                f"the requested {spec.duration_s / 3600:.2f} h. Results are truncated in "
                f"time, so downstream arrival times may be missing entirely."
            )

        cells_dried = int(work.counters[0])
        cells_capped = int(work.counters[1])
        cell_updates = max(step * int(active.sum()), 1)
        clipped_fraction = cells_capped / cell_updates

        if cells_capped > 0:
            warnings.append(
                f"The velocity cap of 120 m/s engaged on {cells_capped:,} cell-updates "
                f"({clipped_fraction:.2e} of the total). Capping is a numerical backstop "
                f"at the wet/dry front, not physics; if this fraction is not tiny, treat "
                f"the velocity field as unreliable."
            )

        final_volume = k.total_volume(h, active, cell_area)
        expected = initial_volume + volume_in
        mass_error = abs(final_volume - expected) / max(expected, 1.0)

        # Water genuinely leaves through the open downstream boundary, so a
        # nonzero deficit here is expected and is NOT an error. We report it
        # rather than hiding it, and only warn when it exceeds what boundary
        # outflow can plausibly explain.
        if mass_error > 0.5:
            warnings.append(
                f"Mass balance closes to only {mass_error * 100:.1f}%. Some of this is "
                f"legitimate outflow through the open downstream boundary, but a deficit "
                f"this large may also indicate positivity clipping at the wet/dry front."
            )

        if (h[0, :].sum() + h[-1, :].sum() + h[:, 0].sum() + h[:, -1].sum()) > 0:
            warnings.append(
                "Water reached the edge of the compute domain. The flood extent is "
                "clipped by the raster boundary, not by terrain, so flooded area is a "
                "lower bound. Increase domain.reach_length_km or corridor_buffer_km."
            )

        if not (max_depth >= spec.wet_threshold_m).any():
            warnings.append(
                f"No cell ever exceeded the {spec.wet_threshold_m} m wet threshold. "
                f"Either the release point is wrong, the hydrograph is empty, or the "
                f"domain is inactive at the source."
            )

        progress(fraction=1.0, phase="solve", message=f"done in {runtime:.1f}s")

        return ResultBundle(
            engine_id=self.id,
            display_name=self.display_name,
            is_real_solver=True,
            max_depth=max_depth,
            max_velocity=max_speed,
            max_hazard=max_hazard,
            arrival_time_s=arrival,
            transform=spec.transform,
            crs=spec.crs,
            cell_size_m=spec.cell_size_m,
            frames=frames,
            runtime_s=runtime,
            steps=step,
            mass_error=float(mass_error),
            clipped_fraction=float(clipped_fraction),
            provenance={
                "engine": self.display_name,
                "engine_id": self.id,
                "engine_version": self.version,
                "is_real_solver": True,
                "scheme": (
                    "Godunov finite volume, MUSCL-minmod reconstruction, HLLC Riemann "
                    "solver, hydrostatic reconstruction (Audusse et al. 2004) for "
                    "well-balancing, semi-implicit Manning friction, SSP-RK2 in time"
                ),
                "spatial_order": 2 if second_order else 1,
                "temporal_order": 2 if second_order else 1,
                "references": [
                    "Toro (2009), Riemann Solvers and Numerical Methods for Fluid Dynamics, 3rd ed.",
                    "Audusse et al. (2004), SIAM J. Sci. Comput. 25(6), 2050-2065",
                    "Kurganov & Petrova (2007), Commun. Math. Sci. 5(1), 133-160",
                ],
                "jit": k.HAVE_NUMBA,
                "grid": {"rows": rows, "cols": cols, "cell_size_m": spec.cell_size_m},
                "crs": spec.crs,
                "cfl": spec.cfl,
                "dry_tolerance_m": dry_tol,
                "wet_threshold_m": spec.wet_threshold_m,
                "duration_s": spec.duration_s,
                "simulated_to_s": t,
                "steps": step,
                "volume_introduced_m3": volume_in,
                "volume_remaining_m3": final_volume,
                "mass_error": float(mass_error),
                "cells_dried_by_positivity": cells_dried,
                "cell_updates_speed_capped": cells_capped,
                "clipped_fraction": float(clipped_fraction),
                "max_speed_cap_ms": 120.0,
                "runtime_s": runtime,
                **spec.scenario_provenance,
            },
            warnings=warnings,
        )

    @staticmethod
    def _tendencies(
        h, hu, hv, z, manning, active, work, dx, dy, dry_tol, second_order,
        open_edges=True,
    ):
        """Fill work.dh / dhu / dhv with the spatial operator at the current state.

        Split out from the update so the driver can size the timestep against
        the tendencies it is about to apply — see _swe_kernels.positivity_dt.
        """
        w = work
        k.primitives(h, hu, hv, z, w.eta, w.u, w.v, active, dry_tol)

        if second_order:
            k.compute_slopes(w.eta, w.s_eta_x, w.s_eta_y, active)
            k.compute_slopes(w.u, w.s_u_x, w.s_u_y, active)
            k.compute_slopes(w.v, w.s_v_x, w.s_v_y, active)
            np.copyto(w.s_z_x, w.s_z_x_base)
            np.copyto(w.s_z_y, w.s_z_y_base)

            # First order at the wet/dry front, or the reconstruction pushes a
            # film ahead of the physical wave. See the kernel docstring.
            #
            # The BED slopes must be limited on exactly the same cells. The
            # interface depth is h_int = eta_int - z_int, and over dry ground
            # eta == z, so the two reconstructions cancel only while they use
            # the same slope. Zeroing the eta slope alone leaves
            # h_int = -0.5 * s_z, which is positive wherever the bed falls —
            # water conjured out of the terrain gradient. That defect filled a
            # test domain to 75 m depth with the inflow switched off.
            k.zero_slopes_at_wet_dry(w.s_eta_x, w.s_eta_y, h, active, dry_tol)
            k.zero_slopes_at_wet_dry(w.s_u_x, w.s_u_y, h, active, dry_tol)
            k.zero_slopes_at_wet_dry(w.s_v_x, w.s_v_y, h, active, dry_tol)
            k.zero_slopes_at_wet_dry(w.s_z_x, w.s_z_y, h, active, dry_tol)

        k.flux_sweep(
            h, hu, hv, z,
            w.eta, w.u, w.v,
            w.s_eta_x, w.s_u_x, w.s_v_x, w.s_z_x,
            w.s_eta_y, w.s_u_y, w.s_v_y, w.s_z_y,
            w.dh, w.dhu, w.dhv,
            active, dx, dy, dry_tol, second_order,
        )
        # Faces with no active neighbour carry no flux from the sweep above, so
        # the boundary contribution must be supplied explicitly. See
        # _swe_kernels.boundary_fluxes for why omitting it is fatal.
        k.boundary_fluxes(
            h, hu, hv, z, w.eta, w.u, w.v,
            w.s_eta_x, w.s_u_x, w.s_v_x, w.s_z_x,
            w.s_eta_y, w.s_u_y, w.s_v_y, w.s_z_y,
            w.dh, w.dhu, w.dhv,
            active, dx, dy, dry_tol, second_order, open_edges,
        )

    @staticmethod
    def _apply(h, hu, hv, work, active, dt, dry_tol, max_speed=120.0):
        k.apply_update(
            h, hu, hv, work.dh, work.dhu, work.dhv, active, dt, dry_tol,
            max_speed, work.counters,
        )

    @staticmethod
    def _stage(
        h, hu, hv, z, manning, active, work, dx, dy, dt, dry_tol, second_order,
        open_edges=True, max_speed=120.0,
    ):
        """Tendencies plus update in one call, for tests and simple drivers."""
        ShallowWaterFV._tendencies(
            h, hu, hv, z, manning, active, work, dx, dy, dry_tol, second_order,
            open_edges,
        )
        ShallowWaterFV._apply(h, hu, hv, work, active, dt, dry_tol, max_speed)

    @staticmethod
    def _step(
        h, hu, hv, z, manning, active, work, dx, dy, dt_max, dry_tol, second_order,
        open_edges=True, max_speed=120.0, positivity_safety=0.5,
    ):
        """One SSP-RK2 step with a positivity-limited timestep. Returns dt used.

        The timestep is the smaller of the CFL limit the caller supplies and
        the positivity limit derived from the first stage's tendencies. The
        safety factor accounts for the second stage, whose tendencies are not
        known when dt is chosen and can be slightly more draining.
        """
        ShallowWaterFV._tendencies(
            h, hu, hv, z, manning, active, work, dx, dy, dry_tol, second_order,
            open_edges,
        )
        dt = min(
            dt_max,
            positivity_safety * k.positivity_dt(h, work.dh, active, dry_tol),
        )
        if not second_order:
            ShallowWaterFV._apply(h, hu, hv, work, active, dt, dry_tol, max_speed)
            return dt

        h0, hu0, hv0 = h.copy(), hu.copy(), hv.copy()
        ShallowWaterFV._apply(h, hu, hv, work, active, dt, dry_tol, max_speed)

        # Stage 2, then average. This is what makes the scheme second-order in
        # time as well as space; a single Euler step with second-order fluxes
        # is only first-order overall and loses most of the MUSCL benefit.
        ShallowWaterFV._tendencies(
            h, hu, hv, z, manning, active, work, dx, dy, dry_tol, second_order,
            open_edges,
        )
        ShallowWaterFV._apply(h, hu, hv, work, active, dt, dry_tol, max_speed)

        h *= 0.5
        hu *= 0.5
        hv *= 0.5
        h += 0.5 * h0
        hu += 0.5 * hu0
        hv += 0.5 * hv0
        h[h < dry_tol] = 0.0
        hu[h <= 0.0] = 0.0
        hv[h <= 0.0] = 0.0
        return dt


class Work:
    """Pre-allocated scratch arrays for one solver run.

    The bed slopes are limited once here rather than per stage: the bed does
    not move, and recomputing them every stage was measurable on a large grid.
    """

    __slots__ = (
        "eta", "u", "v",
        "s_eta_x", "s_eta_y", "s_u_x", "s_u_y", "s_v_x", "s_v_y",
        "s_z_x", "s_z_y",
        # The bed never moves, so its limited slopes are computed once. But the
        # wet/dry mask DOES move, and the slopes actually used each stage must
        # be zeroed on the same cells as the water-surface slopes — hence a
        # static pair plus a per-stage working pair.
        "s_z_x_base", "s_z_y_base",
        "dh", "dhu", "dhv",
        "counters",
    )

    def __init__(self, z, active):
        import numpy as _np

        shape = z.shape
        for name in self.__slots__:
            if name == "counters":
                continue
            setattr(self, name, _np.zeros(shape, dtype=_np.float64))
        # [cells dried by positivity, cells whose speed was capped]
        self.counters = _np.zeros(2, dtype=_np.int64)
        k.compute_slopes(z, self.s_z_x_base, self.s_z_y_base, active)


def solve_1d_dambreak(
    n_cells: int,
    length_m: float,
    h_left: float,
    h_right: float,
    duration_s: float,
    *,
    cfl: float = 0.45,
    manning_n: float = 0.0,
    second_order: bool = True,
    dry_tolerance_m: float = 1e-3,
) -> dict[str, Any]:
    """1-D dam break on a flat frictionless bed, for analytical verification.

    Runs the full 2-D solver on a single-row domain so the verification tests
    exercise the same kernels the production runs use. A separate 1-D code path
    would verify code that never runs in anger, which is worth nothing.

    The dry tolerance defaults to 1 mm, matching production runs. Setting it
    far smaller does not make the answer better: a 0.3 mm film is numerically
    meaningless, but the solver will still transport it, and with no friction
    and nothing to resist it that film accelerates away downstream and lands
    200-600 m ahead of the physical front. Tolerances of 1e-3 to 1e-2 m are
    standard in the shallow-water literature for exactly this reason.

    Returns cell centres, depth, velocity and the timestep history.
    """
    dx = length_m / n_cells
    rows = 3  # one interior row plus a guard row each side
    z = np.zeros((rows, n_cells), dtype=np.float64)
    manning = np.full((rows, n_cells), manning_n, dtype=np.float64)
    active = np.ones((rows, n_cells), dtype=np.bool_)

    x = (np.arange(n_cells) + 0.5) * dx
    h = np.zeros((rows, n_cells), dtype=np.float64)
    h[:, x < length_m / 2.0] = h_left
    h[:, x >= length_m / 2.0] = h_right

    hu = np.zeros_like(h)
    hv = np.zeros_like(h)

    work = Work(z, active)

    t = 0.0
    steps = 0
    dts: list[float] = []

    while t < duration_s and steps < 200_000:
        fastest = k.max_wave_speed(h, hu, hv, active, dry_tolerance_m)
        if fastest < 1e-12:
            break
        dt = min(cfl * dx / fastest, duration_s - t)
        if dt <= 0:
            break

        dt = ShallowWaterFV._step(
            h, hu, hv, z, manning, active, work, dx, dx, dt, dry_tolerance_m,
            second_order,
        )

        if manning_n > 0:
            k.apply_friction(h, hu, hv, manning, active, dt, dry_tolerance_m)

        t += dt
        steps += 1
        dts.append(dt)

    mid = rows // 2
    depth = h[mid].copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        velocity = np.where(depth > dry_tolerance_m, hu[mid] / np.maximum(depth, 1e-30), 0.0)

    return {
        "x": x,
        "dx": dx,
        "h": depth,
        "u": velocity,
        "t": t,
        "steps": steps,
        "dt_min": float(min(dts)) if dts else 0.0,
        "dt_max": float(max(dts)) if dts else 0.0,
    }
