"""Breach outflow and level-pool reservoir routing.

This module produces the breach hydrograph Q(t) that is the upstream boundary
condition for both hydrodynamic engines. It is where reservoir storage becomes
discharge, and it is the reason the flood eventually stops: as water leaves,
the level falls, the head over the breach invert drops, and outflow decays.

The mass balance solved is

    dS/dt = I(t) - Q_breach(t) - Q_spillway(t)

with S obtained from the elevation-area-capacity curve, integrated with an
adaptive RK-style step that shrinks when the level is changing fast — which is
exactly during breach formation, when a fixed step would overshoot and produce
a peak discharge that depends on the timestep rather than on the physics.

Outflow through the trapezoidal breach uses the standard broad-crested weir
plus side-slope terms, with a submergence correction for tailwater.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from floodguard.breach.parameters import BreachGeometry
from floodguard.scenario import BreachGrowth, BreachShape, ScenarioType

log = logging.getLogger(__name__)

G = 9.81

#: Broad-crested weir coefficient for the rectangular part of the breach.
#: 1.7 (SI) is the standard value used in DAMBRK/HEC-RAS for a breach weir.
C_WEIR_RECT = 1.7
#: Coefficient for the triangular side-slope contribution.
C_WEIR_SIDE = 1.35
#: Orifice discharge coefficient for a piping failure before roof collapse.
C_ORIFICE = 0.6


def breach_fraction(t_s: float, formation_time_s: float, growth: BreachGrowth) -> float:
    """Fraction of final breach size developed at time t, in [0, 1].

    `linear` is the DAMBRK default. `sine` gives a slow start and slow finish,
    which better matches observed headcut erosion. `parabolic` accelerates,
    matching failures where erosion runs away once flow concentrates.
    """
    if formation_time_s <= 0:
        return 1.0
    tau = min(max(t_s / formation_time_s, 0.0), 1.0)

    if growth is BreachGrowth.LINEAR:
        return tau
    if growth is BreachGrowth.SINE:
        return 0.5 * (1.0 - math.cos(math.pi * tau))
    if growth is BreachGrowth.PARABOLIC:
        return tau * tau
    raise ValueError(f"unknown breach growth law: {growth}")


@dataclass
class BreachState:
    """Breach opening geometry at one instant."""

    bottom_width_m: float
    invert_m: float
    side_slope: float

    def top_width_at(self, water_level_m: float) -> float:
        head = max(0.0, water_level_m - self.invert_m)
        return self.bottom_width_m + 2.0 * self.side_slope * head


def breach_state_at(
    t_s: float,
    geometry: BreachGeometry,
    crest_elevation_m: float,
    formation_time_s: float,
    growth: BreachGrowth,
    shape: BreachShape,
) -> BreachState:
    """Breach opening at time t, growing downward and outward from the crest.

    Both the width and the depth develop over the formation time. A breach that
    reached full depth instantly while widening slowly would release the full
    head from t=0 and overstate the peak.
    """
    f = breach_fraction(t_s, formation_time_s, growth)

    depth = geometry.depth_m * f
    invert = crest_elevation_m - depth

    if shape is BreachShape.TRIANGULAR:
        bottom_width = 0.0
        side_slope = geometry.side_slope if geometry.side_slope > 0 else 1.0
    elif shape is BreachShape.RECTANGULAR:
        bottom_width = geometry.width_m * f
        side_slope = 0.0
    else:  # trapezoidal
        # Froehlich's width is the AVERAGE width at mid-height. Converting to a
        # bottom width for a trapezoid: B_avg = B_bottom + z * h.
        bottom = max(geometry.width_m - geometry.side_slope * geometry.depth_m, 0.0)
        bottom_width = bottom * f
        side_slope = geometry.side_slope

    return BreachState(bottom_width_m=bottom_width, invert_m=invert, side_slope=side_slope)


def weir_outflow(
    water_level_m: float,
    breach: BreachState,
    tailwater_m: float = 0.0,
) -> float:
    """Discharge through a trapezoidal breach acting as a broad-crested weir.

        Q = C_r * B * H^(3/2)  +  C_s * z * H^(5/2)

    The first term is the rectangular section, the second the two triangular
    side-slope wedges. A submergence correction after Villemonte (1947) is
    applied when tailwater rises above the invert:

        f = (1 - (H_t / H_u)^1.5)^0.385
    """
    head = water_level_m - breach.invert_m
    if head <= 0.0:
        return 0.0

    q = C_WEIR_RECT * breach.bottom_width_m * head**1.5
    if breach.side_slope > 0:
        q += C_WEIR_SIDE * breach.side_slope * head**2.5

    tail_head = tailwater_m - breach.invert_m
    if tail_head > 0.0:
        ratio = min(tail_head / head, 0.999)
        q *= (1.0 - ratio**1.5) ** 0.385

    return q


def orifice_outflow(water_level_m: float, breach: BreachState, pipe_area_m2: float) -> float:
    """Discharge through a piping void before the roof collapses.

        Q = C * A * sqrt(2 * g * H)

    H is measured from the water surface to the centre of the orifice.
    """
    centre = breach.invert_m + 0.5 * math.sqrt(max(pipe_area_m2, 0.0))
    head = water_level_m - centre
    if head <= 0.0 or pipe_area_m2 <= 0.0:
        return 0.0
    return C_ORIFICE * pipe_area_m2 * math.sqrt(2.0 * G * head)


@dataclass
class Hydrograph:
    """The breach outflow hydrograph — the boundary condition for both engines."""

    time_s: np.ndarray
    discharge_m3s: np.ndarray
    water_level_m: np.ndarray
    storage_m3: np.ndarray
    breach_width_m: np.ndarray

    peak_discharge_m3s: float
    time_to_peak_s: float
    total_volume_m3: float
    final_level_m: float
    #: Mass-balance closure error as a fraction of released volume.
    mass_error: float
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def q_at(self, t_s: float) -> float:
        """Discharge at an arbitrary time, for the solver's boundary condition."""
        return float(np.interp(t_s, self.time_s, self.discharge_m3s, left=0.0, right=0.0))

    def summary(self) -> str:
        lines = [
            f"Breach hydrograph ({self.provenance.get('breach_model', 'unknown model')})",
            f"  Peak discharge : {self.peak_discharge_m3s:,.0f} m3/s "
            f"at t = {self.time_to_peak_s / 60:.1f} min",
            f"  Volume released: {self.total_volume_m3 / 1e6:,.0f} MCM",
            f"  Level          : {self.water_level_m[0]:.2f} -> {self.final_level_m:.2f} m MSL",
            f"  Mass closure   : {self.mass_error * 100:.4f} % error",
        ]
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_s": self.time_s.tolist(),
            "discharge_m3s": self.discharge_m3s.tolist(),
            "water_level_m": self.water_level_m.tolist(),
            "breach_width_m": self.breach_width_m.tolist(),
            "peak_discharge_m3s": self.peak_discharge_m3s,
            "time_to_peak_s": self.time_to_peak_s,
            "total_volume_m3": self.total_volume_m3,
            "final_level_m": self.final_level_m,
            "mass_error": self.mass_error,
            "provenance": self.provenance,
            "warnings": self.warnings,
        }


def route(
    curve,
    geometry: BreachGeometry,
    *,
    initial_level_m: float,
    crest_elevation_m: float,
    scenario_type: ScenarioType,
    shape: BreachShape = BreachShape.TRAPEZOIDAL,
    growth: BreachGrowth = BreachGrowth.LINEAR,
    duration_s: float = 6 * 3600.0,
    inflow_m3s: float | Callable[[float], float] = 0.0,
    spillway_m3s: float = 0.0,
    tailwater_m: float = 0.0,
    min_dt_s: float = 0.05,
    max_dt_s: float = 60.0,
    max_level_drop_per_step_m: float = 0.05,
) -> Hydrograph:
    """Level-pool routing of a breach outflow.

    Integrates `dS/dt = I - Q_breach - Q_spillway` with an adaptive step. The
    step is chosen so the water level falls by no more than
    `max_level_drop_per_step_m` per step, which is what keeps the computed peak
    independent of the timestep: during breach formation the level can drop
    metres per minute, and a fixed 60 s step would step straight over the peak.

    Mass closure is computed at the end and reported. A routing that does not
    close to better than ~0.1% has a bug in it, and saying so is cheaper than
    discovering it in the inundation map.
    """
    inflow_fn = inflow_m3s if callable(inflow_m3s) else (lambda _t: float(inflow_m3s))

    formation_s = geometry.formation_time_min * 60.0
    level = float(initial_level_m)
    storage = curve.volume_at(level)
    initial_storage = storage

    times: list[float] = [0.0]
    discharges: list[float] = [0.0]
    levels: list[float] = [level]
    storages: list[float] = [storage]
    widths: list[float] = [0.0]

    volume_out = 0.0
    volume_in = 0.0
    t = 0.0
    dt = min_dt_s
    warnings: list[str] = []
    steps = 0
    max_steps = 2_000_000

    # Piping void area grows with the breach until it reaches full depth, at
    # which point the roof collapses and the failure becomes a weir.
    piping = scenario_type is ScenarioType.PIPING_FAILURE

    while t < duration_s and steps < max_steps:
        steps += 1
        bstate = breach_state_at(t, geometry, crest_elevation_m, formation_s, growth, shape)

        if piping and t < formation_s:
            f = breach_fraction(t, formation_s, growth)
            area = max(geometry.width_m * geometry.depth_m * f * f, 0.0)
            q_breach = orifice_outflow(level, bstate, area)
        else:
            q_breach = weir_outflow(level, bstate, tailwater_m)

        q_in = inflow_fn(t)
        q_spill = spillway_m3s if scenario_type != ScenarioType.CONTROLLED_RELEASE else 0.0
        net = q_in - q_breach - q_spill

        # Choose dt so the level moves by at most max_level_drop_per_step_m.
        area_now = max(curve.area_at(level), 1.0)
        rate_m_per_s = abs(net) / area_now
        if rate_m_per_s > 1e-12:
            dt = max(min_dt_s, min(max_dt_s, max_level_drop_per_step_m / rate_m_per_s))
        else:
            dt = max_dt_s
        # Resolve the formation period finely regardless: it sets the peak.
        if t < formation_s:
            dt = min(dt, max(formation_s / 400.0, min_dt_s))
        dt = min(dt, duration_s - t)
        if dt <= 0:
            break

        # Midpoint (RK2) step: evaluate the breach at the half step so a
        # rapidly growing breach is not held at its start-of-step size.
        half_storage = storage + net * (dt / 2.0)
        half_level = curve.level_at_volume(max(half_storage, 0.0))
        half_state = breach_state_at(
            t + dt / 2.0, geometry, crest_elevation_m, formation_s, growth, shape
        )
        if piping and (t + dt / 2.0) < formation_s:
            f = breach_fraction(t + dt / 2.0, formation_s, growth)
            area = max(geometry.width_m * geometry.depth_m * f * f, 0.0)
            q_half = orifice_outflow(half_level, half_state, area)
        else:
            q_half = weir_outflow(half_level, half_state, tailwater_m)

        q_half_in = inflow_fn(t + dt / 2.0)
        net_half = q_half_in - q_half - q_spill

        storage = max(storage + net_half * dt, 0.0)
        level = curve.level_at_volume(storage)
        volume_out += (q_half + q_spill) * dt
        volume_in += q_half_in * dt
        t += dt

        times.append(t)
        discharges.append(q_half)
        levels.append(level)
        storages.append(storage)
        widths.append(half_state.top_width_at(half_level))

        # Stop early once the reservoir has drained to the breach invert and
        # outflow has effectively ceased.
        if t > formation_s and q_half < 1.0 and level <= bstate.invert_m + 0.01:
            log.info("reservoir drained to the breach invert at t=%.0f s", t)
            break

    if steps >= max_steps:
        warnings.append(
            f"Routing hit the {max_steps:,} step cap before the requested duration. "
            f"The hydrograph is truncated at t={t / 3600:.2f} h."
        )

    time_s = np.array(times)
    q = np.array(discharges)
    peak_i = int(np.argmax(q))

    expected = initial_storage + volume_in - storage
    mass_error = abs(volume_out - expected) / max(expected, 1.0)
    if mass_error > 1e-3:
        warnings.append(
            f"Level-pool routing closed its mass balance to only {mass_error * 100:.3f}%. "
            f"Released volume and storage change disagree; treat the hydrograph as suspect."
        )

    if q[-1] > 0.05 * q[peak_i]:
        warnings.append(
            f"The hydrograph is still at {q[-1] / q[peak_i] * 100:.0f}% of peak when the "
            f"simulation window ends at {duration_s / 3600:.1f} h. The reservoir has not "
            f"finished draining, so downstream volumes are a lower bound. Increase "
            f"solver.duration_hours."
        )

    return Hydrograph(
        time_s=time_s,
        discharge_m3s=q,
        water_level_m=np.array(levels),
        storage_m3=np.array(storages),
        breach_width_m=np.array(widths),
        peak_discharge_m3s=float(q[peak_i]),
        time_to_peak_s=float(time_s[peak_i]),
        total_volume_m3=float(volume_out),
        final_level_m=float(level),
        mass_error=float(mass_error),
        provenance={
            "breach_model": geometry.model,
            "breach_reference": geometry.reference,
            "breach_width_m": geometry.width_m,
            "breach_depth_m": geometry.depth_m,
            "breach_side_slope": geometry.side_slope,
            "formation_time_min": geometry.formation_time_min,
            "growth_law": growth.value,
            "shape": shape.value,
            "scenario_type": scenario_type.value,
            "initial_level_m": initial_level_m,
            "crest_elevation_m": crest_elevation_m,
            "initial_storage_mcm": initial_storage / 1e6,
            "weir_coefficients": {"rectangular": C_WEIR_RECT, "side_slope": C_WEIR_SIDE},
            "orifice_coefficient": C_ORIFICE if piping else None,
            "integration": "adaptive midpoint (RK2) on dS/dt = I - Q_breach - Q_spillway",
            "steps": steps,
            "curve_method": getattr(curve, "method", "unknown"),
        },
        warnings=warnings,
    )
