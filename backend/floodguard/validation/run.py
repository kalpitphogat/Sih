"""`make validate` — verify the solver against analytical solutions and benchmarks.

This is the Phase 4.5 gate: no dashboard work is justified until these pass,
because a beautiful map of a wrong answer is worse than no map.

Tests run
---------
1. **Ritter dry-bed dam break** — L1/L2/Linf error vs the exact solution,
   plus the parameter-free h(x0) = 4/9 h0 check and the front position.
2. **Stoker wet-bed dam break** — error norms plus shock position.
3. **Lake at rest over irregular bed** — well-balancedness. Spurious velocity
   must be at round-off, not merely "small".
4. **Mass conservation** — a closed basin must not gain or lose water.
5. **Grid convergence** — error must fall with resolution at close to the
   design order, otherwise the scheme is not doing what it claims.
6. **Frictional dam break** — the front must lag Ritter's frictionless tip.

Every result is written to `docs/validation/` as a PNG plus a JSON error table,
and `summary.md` collects them for the About page.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from floodguard.engines.swe_fv import solve_1d_dambreak
from floodguard.validation import analytical as exact

log = logging.getLogger(__name__)

#: Pass thresholds. Chosen from the finite-volume literature for a
#: second-order MUSCL-HLLC scheme on a shock problem, not tuned to our output.
THRESHOLDS = {
    "ritter_relative_l2": 0.05,        # 5% relative L2 on depth
    "ritter_dam_depth_pct": 5.0,       # 5% on the exact 4/9 h0 result
    "ritter_front_lag_pct": 25.0,      # numerical fronts lag; 25% is the documented band
    "stoker_relative_l2": 0.05,
    "stoker_shock_cells": 12.0,        # shock located within 12 cells
    "lake_at_rest_velocity": 1e-10,    # must be round-off, not "small"
    "mass_error": 1e-9,
    "convergence_order": 0.8,          # observed order on a shock problem
}


@dataclass
class Check:
    """One verification result."""

    name: str
    passed: bool
    metrics: dict[str, Any]
    threshold: str
    note: str = ""
    plot: str | None = None

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return f"  [{mark}] {self.name}: {self.threshold}"


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)
    runtime_s: float = 0.0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "runtime_s": self.runtime_s,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "threshold": c.threshold,
                    "metrics": c.metrics,
                    "note": c.note,
                    "plot": c.plot,
                }
                for c in self.checks
            ],
        }


def _plot(out_dir: Path, name: str, draw) -> str | None:
    """Render a matplotlib figure, returning its filename, or None on failure."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(9, 4.5), dpi=130)
        draw(fig)
        fig.tight_layout()
        path = out_dir / f"{name}.png"
        fig.savefig(path)
        plt.close(fig)
        return path.name
    except Exception as exc:  # noqa: BLE001 - a missing plot must not fail the gate
        log.warning("could not render plot %s: %s", name, exc)
        return None


# --- individual checks ------------------------------------------------------------


def check_ritter(out_dir: Path, n_cells: int = 800) -> Check:
    """Dry-bed dam break against Ritter (1892)."""
    h0, length, duration = 10.0, 2000.0, 20.0
    res = solve_1d_dambreak(n_cells, length, h0, 0.0, duration)

    x0 = length / 2.0
    h_exact, u_exact = exact.ritter(res["x"], res["t"], h0, x0)
    norms = exact.error_norms(res["h"], h_exact, res["dx"])

    # Parameter-free check: depth at the dam site is exactly 4/9 h0.
    i0 = int(np.argmin(np.abs(res["x"] - x0)))
    dam_depth = float(res["h"][i0])
    dam_expected = exact.ritter_depth_at_dam(h0)
    dam_error_pct = 100.0 * abs(dam_depth - dam_expected) / dam_expected

    # Front position. A monotone numerical front always lags the analytical
    # tip, where depth goes to zero with infinite gradient; it must never lead.
    # The front is located at a stated fraction of the initial head (0.1% of
    # h0 = 1 cm here) rather than at the dry tolerance, so the comparison does
    # not depend on a numerical parameter.
    front_threshold = 0.001 * h0
    wet = res["h"] > front_threshold
    front_num = float(res["x"][wet].max()) if wet.any() else x0
    front_exact = exact.ritter_front_position(res["t"], h0, x0)
    lag = (front_exact - front_num) / (front_exact - x0)

    passed = (
        norms["relative_L2"] < THRESHOLDS["ritter_relative_l2"]
        and dam_error_pct < THRESHOLDS["ritter_dam_depth_pct"]
        and 0.0 <= lag < THRESHOLDS["ritter_front_lag_pct"] / 100.0
    )

    def draw(fig):
        ax = fig.add_subplot(111)
        ax.plot(res["x"], h_exact, "k-", lw=2, label="Ritter (1892) analytical")
        ax.plot(res["x"], res["h"], "C0--", lw=1.6, label="FloodGuard-SWE")
        ax.axvline(front_exact, color="C3", ls=":", lw=1, label="analytical front")
        ax.set_xlabel("x (m)")
        ax.set_ylabel("depth (m)")
        ax.set_title(
            f"Ritter dry-bed dam break, t = {res['t']:.1f} s, "
            f"{n_cells} cells (dx = {res['dx']:.2f} m)\n"
            f"relative L2 = {norms['relative_L2']:.3%}, "
            f"h(dam) error = {dam_error_pct:.2f}%"
        )
        ax.legend()
        ax.grid(alpha=0.3)

    return Check(
        name="Ritter dry-bed dam break",
        passed=passed,
        metrics={
            **norms,
            "dam_depth_m": dam_depth,
            "dam_depth_expected_m": dam_expected,
            "dam_depth_error_pct": dam_error_pct,
            "front_numerical_m": front_num,
            "front_analytical_m": front_exact,
            "front_lag_fraction": lag,
            "front_threshold_m": front_threshold,
            "t_s": res["t"],
            "dx_m": res["dx"],
        },
        threshold=(
            f"relative L2 {norms['relative_L2']:.3%} < 5%, "
            f"h(dam) error {dam_error_pct:.2f}% < 5%, "
            f"front lag {lag:.1%} in [0%, 25%)"
        ),
        note=(
            "The front lag is expected and is documented, not a defect: a monotone "
            "second-order scheme cannot resolve the infinite-gradient tip where depth "
            "reaches zero. It must never LEAD the analytical front, which would mean "
            "the wave is arriving too early."
        ),
        plot=_plot(out_dir, "ritter_dam_break", draw),
    )


def check_stoker(out_dir: Path, n_cells: int = 800) -> Check:
    """Wet-bed dam break against Stoker (1957)."""
    h0, h1, length, duration = 10.0, 2.0, 2000.0, 20.0
    res = solve_1d_dambreak(n_cells, length, h0, h1, duration)

    x0 = length / 2.0
    h_exact, _ = exact.stoker(res["x"], res["t"], h0, h1, x0)
    norms = exact.error_norms(res["h"], h_exact, res["dx"])

    # Shock position: the steepest depth gradient downstream of the dam.
    downstream = res["x"] > x0
    grad = np.abs(np.gradient(res["h"][downstream], res["dx"]))
    shock_num = float(res["x"][downstream][int(np.argmax(grad))])
    shock_exact = x0 + exact.stoker_shock_speed(h0, h1) * res["t"]
    shock_error_cells = abs(shock_num - shock_exact) / res["dx"]

    passed = (
        norms["relative_L2"] < THRESHOLDS["stoker_relative_l2"]
        and shock_error_cells < THRESHOLDS["stoker_shock_cells"]
    )

    def draw(fig):
        ax = fig.add_subplot(111)
        ax.plot(res["x"], h_exact, "k-", lw=2, label="Stoker (1957) analytical")
        ax.plot(res["x"], res["h"], "C0--", lw=1.6, label="FloodGuard-SWE")
        ax.axvline(shock_exact, color="C3", ls=":", lw=1, label="analytical shock")
        ax.set_xlabel("x (m)")
        ax.set_ylabel("depth (m)")
        ax.set_title(
            f"Stoker wet-bed dam break (h0 = {h0} m, h1 = {h1} m), t = {res['t']:.1f} s\n"
            f"relative L2 = {norms['relative_L2']:.3%}, "
            f"shock located within {shock_error_cells:.1f} cells"
        )
        ax.legend()
        ax.grid(alpha=0.3)

    return Check(
        name="Stoker wet-bed dam break",
        passed=passed,
        metrics={
            **norms,
            "shock_numerical_m": shock_num,
            "shock_analytical_m": shock_exact,
            "shock_error_cells": shock_error_cells,
            "shock_speed_ms": exact.stoker_shock_speed(h0, h1),
            "t_s": res["t"],
        },
        threshold=(
            f"relative L2 {norms['relative_L2']:.3%} < 5%, "
            f"shock within {shock_error_cells:.1f} < 12 cells"
        ),
        note=(
            "The wet-bed case is the one that matters for a real reach, where the "
            "flood runs onto an existing river rather than a dry bed. It produces a "
            "genuine shock, so it tests the HLLC solver rather than only the "
            "rarefaction."
        ),
        plot=_plot(out_dir, "stoker_dam_break", draw),
    )


def check_lake_at_rest(out_dir: Path, n: int = 120) -> Check:
    """Well-balancedness: still water over irregular terrain must stay still.

    This is the test that separates a well-balanced scheme from one that merely
    looks stable. Without hydrostatic reconstruction, the bed-slope source term
    and the pressure flux fail to cancel, and spurious velocities of order
    sqrt(g*dz) appear on every slope — metres per second of fictional flow on a
    Himalayan DEM, which would then be reported as flood velocity.
    """
    from floodguard.engines import _swe_kernels as k
    from floodguard.engines.swe_fv import ShallowWaterFV

    rng = np.random.default_rng(42)
    yy, xx = np.mgrid[0:n, 0:n]
    z = (
        20.0 * np.sin(xx / 9.0)
        + 15.0 * np.cos(yy / 7.0)
        + 8.0 * rng.standard_normal((n, n))
    )
    z -= z.min()

    water_level = z.max() + 5.0
    h = np.maximum(water_level - z, 0.0)
    h0 = h.copy()
    hu = np.zeros_like(h)
    hv = np.zeros_like(h)
    manning = np.zeros_like(h)
    active = np.ones(h.shape, dtype=np.bool_)

    from floodguard.engines.swe_fv import Work

    work = Work(z, active)
    dx = 30.0
    dt = 0.05
    for _ in range(200):
        # Walls, so the lake genuinely cannot drain: any motion here is
        # spurious rather than legitimate outflow.
        ShallowWaterFV._step(
            h, hu, hv, z, manning, active, work, dx, dx, dt, 1e-6, True,
            open_edges=False,
        )

    wse = h + z
    drift = float(np.abs(wse - water_level).max())
    max_discharge = float(max(np.abs(hu).max(), np.abs(hv).max()))
    depth_change = float(np.abs(h - h0).max())

    passed = (
        max_discharge < THRESHOLDS["lake_at_rest_velocity"]
        and drift < THRESHOLDS["lake_at_rest_velocity"]
    )

    def draw(fig):
        ax1 = fig.add_subplot(121)
        im = ax1.imshow(z, cmap="terrain")
        ax1.set_title("irregular bed")
        fig.colorbar(im, ax=ax1, label="z (m)")
        ax2 = fig.add_subplot(122)
        im2 = ax2.imshow(np.abs(wse - water_level), cmap="magma")
        ax2.set_title(
            f"|water surface - initial| after 200 steps\nmax drift = {drift:.2e} m"
        )
        fig.colorbar(im2, ax=ax2, label="m")

    return Check(
        name="Lake at rest over irregular bed (well-balancedness)",
        passed=passed,
        metrics={
            "max_water_surface_drift_m": drift,
            "max_spurious_discharge_m2s": max_discharge,
            "max_depth_change_m": depth_change,
            "steps": 200,
            "bed_relief_m": float(z.max() - z.min()),
        },
        threshold=(
            f"spurious discharge {max_discharge:.2e} < 1e-10, "
            f"surface drift {drift:.2e} m < 1e-10"
        ),
        note=(
            "Over a bed with "
            f"{float(z.max() - z.min()):.0f} m of relief. A non-well-balanced scheme "
            "produces velocities of order sqrt(g*dz) here, i.e. several m/s of flow "
            "that does not exist."
        ),
        plot=_plot(out_dir, "lake_at_rest", draw),
    )


def check_mass_conservation(out_dir: Path, n: int = 100) -> Check:
    """Exact conservation with no wet/dry front.

    Deliberately fully submerged. Wetting and drying is a separate, unavoidable
    source of mass loss (a cell that falls below the dry tolerance is zeroed),
    and mixing the two would let a leaky flux scheme hide behind it. This test
    isolates the flux scheme: with every cell wet and walls on every edge,
    conservation must hold to round-off and nothing else is being measured.
    """
    from floodguard.engines import _swe_kernels as k
    from floodguard.engines.swe_fv import ShallowWaterFV, Work

    yy, xx = np.mgrid[0:n, 0:n]
    z = 0.02 * ((xx - n / 2) ** 2 + (yy - n / 2) ** 2)
    # Water surface well above the highest bed point: no cell can dry out.
    h = (z.max() + 20.0) - z
    hu = 0.5 * h
    hv = np.zeros_like(h)
    manning = np.zeros_like(h)
    active = np.ones(h.shape, dtype=np.bool_)

    dx = 20.0
    cell_area = dx * dx
    work = Work(z, active)
    v0 = k.total_volume(h, active, cell_area)

    for _ in range(300):
        fastest = k.max_wave_speed(h, hu, hv, active, 1e-3)
        dt = 0.4 * dx / max(fastest, 1e-9)
        ShallowWaterFV._step(
            h, hu, hv, z, manning, active, work, dx, dx, dt, 1e-3, True,
            open_edges=False,
        )

    v1 = k.total_volume(h, active, cell_area)
    error = abs(v1 - v0) / v0
    passed = error < THRESHOLDS["mass_error"]

    def draw(fig):
        ax = fig.add_subplot(111)
        im = ax.imshow(h, cmap="Blues")
        fig.colorbar(im, ax=ax, label="depth (m)")
        ax.set_title(
            f"Closed basin, fully submerged, after 300 steps\n"
            f"volume {v0:,.0f} -> {v1:,.0f} m3, relative error {error:.2e}"
        )

    return Check(
        name="Mass conservation, no wet/dry front",
        passed=passed,
        metrics={
            "initial_volume_m3": v0,
            "final_volume_m3": v1,
            "relative_error": error,
            "steps": 300,
            "dried_cells": int(work.counters[0]),
        },
        threshold=f"relative volume error {error:.2e} < 1e-9",
        note=(
            "Water is given an initial momentum so it sloshes against the walls; a "
            "static test would pass trivially. Every cell stays wet throughout, so "
            "this measures the flux scheme alone."
        ),
        plot=_plot(out_dir, "mass_conservation", draw),
    )


def check_wetdry_mass_budget(out_dir: Path, n: int = 100) -> Check:
    """How much mass wetting and drying actually costs, measured not assumed.

    A cell whose depth falls below the dry tolerance is zeroed, which loses at
    most `dry_tol * cell_area` of water. That is a real, unavoidable cost of
    any wet/dry treatment, and the honest thing is to measure it and publish
    the number rather than claim conservation the scheme does not have.

    The bound asserted here is 1% over an aggressively sloshing partially-dry
    basin, which is far harsher than a routing run: there, the front advances
    into dry land once rather than oscillating across it hundreds of times.
    """
    from floodguard.engines import _swe_kernels as k
    from floodguard.engines.swe_fv import ShallowWaterFV, Work

    yy, xx = np.mgrid[0:n, 0:n]
    z = 0.02 * ((xx - n / 2) ** 2 + (yy - n / 2) ** 2)
    h = np.maximum(30.0 - z, 0.0)    # a pool with a genuine shoreline
    hu = 0.5 * h
    hv = np.zeros_like(h)
    manning = np.zeros_like(h)
    active = np.ones(h.shape, dtype=np.bool_)

    dx = 20.0
    cell_area = dx * dx
    dry_tol = 1e-3
    work = Work(z, active)
    v0 = k.total_volume(h, active, cell_area)
    shoreline_cells = int(((h > 0) & (h < 1.0)).sum())

    for _ in range(300):
        fastest = k.max_wave_speed(h, hu, hv, active, dry_tol)
        dt = 0.4 * dx / max(fastest, 1e-9)
        ShallowWaterFV._step(
            h, hu, hv, z, manning, active, work, dx, dx, dt, dry_tol, True,
            open_edges=False,
        )

    v1 = k.total_volume(h, active, cell_area)
    error = abs(v1 - v0) / v0
    dried = int(work.counters[0])
    theoretical = dried * dry_tol * cell_area / v0
    passed = error < 0.01 and np.isfinite(v1)

    def draw(fig):
        ax = fig.add_subplot(111)
        im = ax.imshow(np.where(h > dry_tol, h, np.nan), cmap="Blues")
        fig.colorbar(im, ax=ax, label="depth (m)")
        ax.set_title(
            f"Partially dry basin after 300 steps\n"
            f"mass budget: {error:.3%} lost over {dried:,} drying events"
        )

    return Check(
        name="Wet/dry mass budget (measured cost of drying)",
        passed=passed,
        metrics={
            "initial_volume_m3": v0,
            "final_volume_m3": v1,
            "relative_loss": error,
            "drying_events": dried,
            "theoretical_bound": theoretical,
            "dry_tolerance_m": dry_tol,
            "shoreline_cells_initial": shoreline_cells,
            "steps": 300,
        },
        threshold=f"relative mass loss {error:.3%} < 1%",
        note=(
            "This is not a conservation failure to be fixed; it is the price of a "
            "wet/dry treatment, quantified. The number is reported alongside every "
            "simulation so a reviewer can see what it cost on their own case."
        ),
        plot=_plot(out_dir, "wetdry_mass_budget", draw),
    )


def check_convergence(out_dir: Path, resolutions=(100, 200, 400, 800)) -> Check:
    """Grid convergence on the Ritter problem.

    The observed order is fitted from log(error) vs log(dx). For a shock or a
    wet/dry front, formal second order is unattainable — the solution is not
    smooth — and the literature expects roughly first order in L1 there. We
    require better than 0.8 and report what we actually get rather than
    claiming the design order.
    """
    h0, length, duration = 10.0, 2000.0, 20.0
    x0 = length / 2.0

    dxs: list[float] = []
    errors: list[float] = []
    for n in resolutions:
        res = solve_1d_dambreak(n, length, h0, 0.0, duration)
        h_exact, _ = exact.ritter(res["x"], res["t"], h0, x0)
        norms = exact.error_norms(res["h"], h_exact, res["dx"])
        dxs.append(res["dx"])
        errors.append(norms["L1"])

    slope = float(np.polyfit(np.log(dxs), np.log(errors), 1)[0])
    passed = slope > THRESHOLDS["convergence_order"] and errors[-1] < errors[0]

    def draw(fig):
        ax = fig.add_subplot(111)
        ax.loglog(dxs, errors, "o-", label=f"L1 error (observed order {slope:.2f})")
        ref = np.array(errors[0]) * (np.array(dxs) / dxs[0]) ** 1.0
        ax.loglog(dxs, ref, "k--", alpha=0.5, label="first order reference")
        ax.set_xlabel("dx (m)")
        ax.set_ylabel("L1 error in depth")
        ax.set_title("Grid convergence, Ritter dam break")
        ax.legend()
        ax.grid(alpha=0.3, which="both")

    return Check(
        name="Grid convergence (Ritter)",
        passed=passed,
        metrics={
            "resolutions": list(resolutions),
            "dx_m": dxs,
            "L1_errors": errors,
            "observed_order": slope,
        },
        threshold=f"observed order {slope:.2f} > 0.8 and error decreases with dx",
        note=(
            "Second order is not attainable on a discontinuous solution; roughly "
            "first order in L1 is the expected and correct result at a shock."
        ),
        plot=_plot(out_dir, "grid_convergence", draw),
    )


def check_friction(out_dir: Path, n_cells: int = 600) -> Check:
    """Frictional dam break: the front must lag the frictionless Ritter tip."""
    h0, length, duration = 10.0, 2000.0, 20.0
    x0 = length / 2.0

    frictionless = solve_1d_dambreak(n_cells, length, h0, 0.0, duration, manning_n=0.0)
    rough = solve_1d_dambreak(n_cells, length, h0, 0.0, duration, manning_n=0.05)

    def front(res):
        wet = res["h"] > 0.001 * h0
        return float(res["x"][wet].max()) if wet.any() else x0

    f_free = front(frictionless)
    f_rough = front(rough)
    ritter_front = exact.ritter_front_position(duration, h0, x0)
    dressler = exact.dressler_front_celerity(h0, 0.05, f_rough - x0) * duration + x0

    passed = f_rough < f_free <= ritter_front + 5.0

    def draw(fig):
        ax = fig.add_subplot(111)
        h_exact, _ = exact.ritter(frictionless["x"], duration, h0, x0)
        ax.plot(frictionless["x"], h_exact, "k-", lw=2, label="Ritter (frictionless, exact)")
        ax.plot(frictionless["x"], frictionless["h"], "C0--", label="FloodGuard-SWE, n = 0")
        ax.plot(rough["x"], rough["h"], "C1-", label="FloodGuard-SWE, n = 0.05")
        ax.axvline(dressler, color="C2", ls=":", label="Dressler/Whitham front estimate")
        ax.set_xlabel("x (m)")
        ax.set_ylabel("depth (m)")
        ax.set_title(
            f"Effect of friction on the dam-break front, t = {duration:.0f} s\n"
            f"front: frictionless {f_free:.0f} m, n=0.05 {f_rough:.0f} m"
        )
        ax.legend()
        ax.grid(alpha=0.3)

    return Check(
        name="Frictional dam break (front retardation)",
        passed=passed,
        metrics={
            "front_frictionless_m": f_free,
            "front_rough_m": f_rough,
            "ritter_front_m": ritter_front,
            "dressler_estimate_m": dressler,
            "retardation_m": f_free - f_rough,
            "manning_n": 0.05,
        },
        threshold=(
            f"rough front {f_rough:.0f} m < frictionless {f_free:.0f} m "
            f"<= Ritter {ritter_front:.0f} m"
        ),
        note=(
            "The Dressler/Whitham line is a first-order asymptotic estimate shown for "
            "orientation, not a precision benchmark. The assertion being tested is the "
            "ordering: friction must retard the front, and the frictionless front must "
            "never outrun Ritter."
        ),
        plot=_plot(out_dir, "friction_dam_break", draw),
    )


# --- runner -----------------------------------------------------------------------


def run_validation(out_dir: Path, quick: bool = False) -> int:
    """Run every check, write plots and tables, return a process exit code."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    report = ValidationReport()

    n = 400 if quick else 800
    resolutions = (100, 200, 400) if quick else (100, 200, 400, 800)

    print("FloodGuard-SWE verification")
    print("=" * 78)
    print("Warming up the JIT...\n")

    for fn, args in (
        (check_ritter, (out_dir, n)),
        (check_stoker, (out_dir, n)),
        (check_lake_at_rest, (out_dir,)),
        (check_mass_conservation, (out_dir,)),
        (check_wetdry_mass_budget, (out_dir,)),
        (check_convergence, (out_dir, resolutions)),
        (check_friction, (out_dir,)),
    ):
        check = fn(*args)
        report.checks.append(check)
        print(check.line())

    report.runtime_s = time.perf_counter() - started

    (out_dir / "results.json").write_text(
        json.dumps(report.to_dict(), indent=2, default=float), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(_markdown(report), encoding="utf-8")

    print()
    print(f"{sum(c.passed for c in report.checks)}/{len(report.checks)} checks passed "
          f"in {report.runtime_s:.1f}s")
    print(f"Plots and tables: {out_dir}")

    if not report.passed:
        print("\nVERIFICATION FAILED. The solver does not reproduce the analytical "
              "solutions, so no result it produces should be trusted.")
        return 1
    return 0


def _markdown(report: ValidationReport) -> str:
    """The summary the About page and the PDF report link to."""
    lines = [
        "# Solver verification",
        "",
        "Verification asks whether the code solves the equations it claims to solve.",
        "These are analytical solutions and closed-form invariants, so the answers are",
        "exact and the comparison is not a matter of opinion.",
        "",
        f"**{sum(c.passed for c in report.checks)} of {len(report.checks)} checks passed** "
        f"({report.runtime_s:.1f}s).",
        "",
        "| Check | Result | Criterion |",
        "| --- | --- | --- |",
    ]
    for c in report.checks:
        lines.append(f"| {c.name} | {'PASS' if c.passed else 'FAIL'} | {c.threshold} |")

    lines += ["", "## Detail", ""]
    for c in report.checks:
        lines.append(f"### {c.name}")
        lines.append("")
        lines.append(f"**{'PASS' if c.passed else 'FAIL'}** — {c.threshold}")
        lines.append("")
        if c.note:
            lines.append(c.note)
            lines.append("")
        if c.plot:
            lines.append(f"![{c.name}]({c.plot})")
            lines.append("")
        lines.append("```json")
        lines.append(json.dumps(c.metrics, indent=2, default=float))
        lines.append("```")
        lines.append("")

    lines += [
        "## What these do not establish",
        "",
        "Verification is not validation. Passing every check above means the numerics",
        "are right; it says nothing about whether the DEM, the breach parameters, the",
        "roughness field or the reservoir bathymetry describe the real river. Those are",
        "validation questions, and they are answered separately against observed events.",
    ]
    return "\n".join(lines)
