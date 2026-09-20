"""The Engine interface every hydrodynamic backend implements.

One interface, four backends: our own finite-volume solver, ANUGA, Delft3D
D-Flow FM and SPH. The orchestrator talks only to this interface, so
substituting an unavailable engine is a single, visible decision made in one
place rather than a silent fallback buried in an adapter.

`is_real_solver` is the honesty flag. It is False for anything approximate, and
the API, the UI badge and the PDF all read it.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np


class ProgressCallback(Protocol):
    """Called periodically so the WebSocket can stream job progress."""

    def __call__(
        self, *, fraction: float, phase: str, message: str, **extra: Any
    ) -> None: ...


def null_progress(*, fraction: float, phase: str, message: str, **extra: Any) -> None:
    """Default no-op progress callback."""


@dataclass
class ResultBundle:
    """Everything one engine run produced.

    The derived rasters are what the dashboard reads; the frames are what the
    time slider animates. Both carry the provenance needed to reproduce them.
    """

    engine_id: str
    #: The exact string the UI badge, API and PDF must display.
    display_name: str
    is_real_solver: bool

    #: Derived rasters over the compute grid.
    max_depth: np.ndarray
    max_velocity: np.ndarray
    max_hazard: np.ndarray          # max of depth * velocity, m2/s
    arrival_time_s: np.ndarray      # -1 where never wetted above the threshold

    transform: Any
    crs: str
    cell_size_m: float

    #: Time-series frames for the animation: list of (t_seconds, depth array).
    frames: list[tuple[float, np.ndarray]] = field(default_factory=list)

    runtime_s: float = 0.0
    steps: int = 0
    #: Mass balance closure as a fraction of the volume introduced.
    mass_error: float = 0.0
    #: Fraction of updates where positivity clipping had to intervene.
    clipped_fraction: float = 0.0

    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: Input files the engine generated, e.g. a Delft3D deck, for download.
    artifacts: dict[str, Path] = field(default_factory=dict)

    # --- summary metrics the KPI cards read ---------------------------------------

    def flooded_area_km2(self, threshold_m: float = 0.3) -> float:
        wet = self.max_depth >= threshold_m
        return float(wet.sum() * self.cell_size_m**2 / 1e6)

    def summary(self, threshold_m: float = 0.3) -> dict[str, Any]:
        """The four KPI cards, computed — never hardcoded."""
        wet = self.max_depth >= threshold_m
        arrived = self.arrival_time_s[self.arrival_time_s >= 0]
        return {
            "engine_id": self.engine_id,
            "engine_display_name": self.display_name,
            "is_real_solver": self.is_real_solver,
            "flooded_area_km2": self.flooded_area_km2(threshold_m),
            "max_depth_m": float(self.max_depth.max()) if wet.any() else None,
            "max_velocity_ms": float(self.max_velocity.max()) if wet.any() else None,
            "earliest_arrival_min": float(arrived.min() / 60.0) if arrived.size else None,
            "latest_arrival_min": float(arrived.max() / 60.0) if arrived.size else None,
            "max_hazard_m2s": float(self.max_hazard.max()) if wet.any() else None,
            "wet_threshold_m": threshold_m,
            "runtime_s": self.runtime_s,
            "steps": self.steps,
            "mass_error": self.mass_error,
            "warnings": self.warnings,
        }

    def sample_at(self, row: int, col: int) -> dict[str, float | None]:
        """Depth, velocity and arrival time at one cell, for town reporting."""
        if not (0 <= row < self.max_depth.shape[0] and 0 <= col < self.max_depth.shape[1]):
            return {"max_depth_m": None, "max_velocity_ms": None, "arrival_min": None}
        arrival = self.arrival_time_s[row, col]
        return {
            "max_depth_m": float(self.max_depth[row, col]),
            "max_velocity_ms": float(self.max_velocity[row, col]),
            "arrival_min": float(arrival / 60.0) if arrival >= 0 else None,
        }


@dataclass
class EngineInput:
    """Everything an engine needs to run, assembled by the orchestrator.

    Deliberately concrete arrays rather than a Scenario object: an adapter that
    shells out to a foreign binary needs the numbers, not our schema, and
    keeping the boundary here means adding an engine never touches Phase 2.
    """

    bed_elevation: np.ndarray       # m MSL
    manning_n: np.ndarray
    active: np.ndarray              # bool; the corridor mask
    cell_size_m: float
    transform: Any
    crs: str

    #: Where the breach releases into the domain, as grid (row, col).
    source_rc: tuple[int, int]
    #: Breach outflow hydrograph: callable t_seconds -> m3/s.
    inflow_q: Callable[[float], float]
    #: Total volume the hydrograph will release, for the mass check.
    inflow_volume_m3: float

    duration_s: float
    cfl: float = 0.45
    dry_tolerance_m: float = 1e-3
    wet_threshold_m: float = 0.3
    output_interval_s: float = 300.0
    second_order: bool = True
    max_steps: int = 2_000_000

    #: Initial depth field. None means a dry bed everywhere.
    initial_depth: np.ndarray | None = None

    scenario_provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.bed_elevation.shape

    @property
    def cell_area_m2(self) -> float:
        return self.cell_size_m**2


class Engine(ABC):
    """A hydrodynamic backend."""

    #: Stable identifier, matching floodguard.engines.availability.
    id: str = "abstract"
    #: The exact string that must be displayed for this engine.
    display_name: str = "abstract engine"
    #: False for any approximation or fallback.
    is_real_solver: bool = False

    @abstractmethod
    def run(
        self, spec: EngineInput, progress: ProgressCallback = null_progress
    ) -> ResultBundle:
        """Run the simulation. Must raise rather than return a fabricated result."""

    def available(self) -> tuple[bool, str]:
        """Whether this engine can run here, and why not if it cannot."""
        from floodguard.engines.availability import resolve

        status = resolve(self.id)
        return status.available, status.detail


class Timer:
    """Small helper so every engine reports runtime the same way."""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.perf_counter() - self._start
        return False
