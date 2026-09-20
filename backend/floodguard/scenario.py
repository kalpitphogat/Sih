"""Scenario definition — the single configuration object the whole pipeline reads.

Engineering rule 5: everything is a config, not a constant. Adding a new dam
must require zero code changes, only a new YAML file in `data/scenarios/`.

A Scenario is validated on load, so a physically impossible request (breach
deeper than the dam, reservoir level above the crest) fails immediately with a
message naming the field, rather than producing a confident wrong map.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class ScenarioType(str, Enum):
    """Failure modes the framework supports.

    LANDSLIDE_DAM_BREACH is the Rishi Ganga / Chamoli 2021 case named in the
    problem statement: a natural blockage failing, not an engineered structure.
    """

    COMPLETE_DAM_BREAK = "complete_dam_break"
    PARTIAL_BREACH = "partial_breach"
    PIPING_FAILURE = "piping_failure"
    OVERTOPPING = "overtopping"
    CONTROLLED_RELEASE = "controlled_release"
    LANDSLIDE_DAM_BREACH = "landslide_dam_breach"


class DamType(str, Enum):
    EARTHFILL = "earthfill"
    ROCKFILL = "rockfill"
    CONCRETE_GRAVITY = "concrete_gravity"
    CONCRETE_ARCH = "concrete_arch"
    MASONRY = "masonry"
    NATURAL_BLOCKAGE = "natural_blockage"


class BreachShape(str, Enum):
    TRAPEZOIDAL = "trapezoidal"
    RECTANGULAR = "rectangular"
    TRIANGULAR = "triangular"


class BreachGrowth(str, Enum):
    LINEAR = "linear"
    SINE = "sine"
    PARABOLIC = "parabolic"


class DamSpec(BaseModel):
    """Physical description of the dam. Sourced from the catalog, overridable."""

    id: str
    name: str
    river: str
    state: str
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    dam_type: DamType

    structural_height_m: float = Field(gt=0, description="Crest to lowest foundation")
    crest_length_m: float | None = Field(default=None, gt=0)
    crest_elevation_m: float | None = Field(default=None, description="m above MSL")

    frl_m: float | None = Field(default=None, description="Full reservoir level, m MSL")
    mddl_m: float | None = Field(default=None, description="Min drawdown level, m MSL")

    gross_storage_mcm: float | None = Field(default=None, gt=0)
    live_storage_mcm: float | None = Field(default=None, gt=0)
    spillway_capacity_m3s: float | None = Field(default=None, gt=0)
    commissioned_year: int | None = None

    #: Per-field provenance: {"frl_m": "CWC NRLD 2019, p.142", ...}
    sources: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _levels_consistent(self):
        if self.frl_m is not None and self.mddl_m is not None and self.mddl_m >= self.frl_m:
            raise ValueError(f"mddl_m ({self.mddl_m}) must be below frl_m ({self.frl_m})")
        if (
            self.crest_elevation_m is not None
            and self.frl_m is not None
            and self.frl_m > self.crest_elevation_m
        ):
            raise ValueError(
                f"frl_m ({self.frl_m}) exceeds crest_elevation_m ({self.crest_elevation_m})"
            )
        return self


class BreachSpec(BaseModel):
    """Breach geometry and timing.

    Leave the geometry fields as None to let the empirical models (Froehlich,
    Von Thun & Gillette, MacDonald) predict them. When a user supplies values,
    the prediction is still computed and shown alongside, so the user can see
    whether their number is physically plausible.
    """

    shape: BreachShape = BreachShape.TRAPEZOIDAL
    growth: BreachGrowth = BreachGrowth.LINEAR

    #: Average breach width at mid-height, m. None -> predicted.
    width_m: float | None = Field(default=None, gt=0)
    #: Vertical extent of the breach below the crest, m. None -> full height.
    depth_m: float | None = Field(default=None, gt=0)
    #: Horizontal:vertical side slope of the trapezoid.
    side_slope: float = Field(default=1.0, ge=0)
    #: Breach formation time, minutes. None -> predicted.
    formation_time_min: float | None = Field(default=None, gt=0)

    #: Which empirical model supplies unset fields.
    parameter_model: str = "froehlich_2008"


class ReservoirState(BaseModel):
    """Initial reservoir condition at t=0."""

    #: Water surface elevation, m MSL. None -> use the dam's FRL.
    initial_level_m: float | None = None
    #: Steady inflow to the reservoir during the event, m3/s.
    inflow_m3s: float = Field(default=0.0, ge=0)
    #: Whether the spillway is discharging alongside the breach.
    spillway_active: bool = False


class DomainSpec(BaseModel):
    """The compute domain: how far downstream, at what resolution."""

    #: Distance traced downstream from the dam along the flow path, km.
    reach_length_km: float = Field(default=120.0, gt=0)
    #: Lateral buffer either side of the valley centreline, km.
    corridor_buffer_km: float = Field(default=5.0, gt=0)
    #: Buffer around the dam that must contain the reservoir pool, km.
    #: The reservoir lies UPSTREAM of the dam, so an AOI derived only from the
    #: dam and its downstream towns excludes it entirely — and a reservoir that
    #: is not in the DEM cannot supply the water that drives the breach.
    #: Tehri's pool reaches ~45 km up the Bhagirathi; 40 km is a safe default.
    reservoir_buffer_km: float = Field(default=40.0, gt=0)
    #: Compute grid resolution, m. Dam-break peaks are resolution-sensitive:
    #: this is exposed deliberately rather than hidden.
    resolution_m: float = Field(default=30.0, gt=0)
    #: Optional refined resolution near the dam, m.
    near_field_resolution_m: float | None = Field(default=None, gt=0)
    #: Target CRS (metric). None -> auto-select the UTM zone from the dam lon.
    crs: str | None = None
    #: Explicit bounding box (west, south, east, north) in EPSG:4326.
    #: None -> derived by tracing the flow path.
    bbox: tuple[float, float, float, float] | None = None


class SolverSpec(BaseModel):
    """Numerical settings. All exposed, none hidden."""

    duration_hours: float = Field(default=6.0, gt=0)
    #: Courant number. 0.45 is a safe default for 2nd-order MUSCL-HLLC.
    cfl: float = Field(default=0.45, gt=0, le=1.0)
    #: Cells with depth below this are treated as dry, m.
    dry_tolerance_m: float = Field(default=1e-3, gt=0)
    #: Depth above which a cell counts as flooded for extent/arrival, m.
    wet_threshold_m: float = Field(default=0.3, gt=0)
    #: How often a result frame is written, seconds.
    output_interval_s: float = Field(default=300.0, gt=0)
    #: Default Manning's n where land cover is unavailable.
    default_manning_n: float = Field(default=0.035, gt=0)
    #: Per-landcover-class overrides, e.g. {"channel": 0.03, "urban": 0.08}.
    manning_n_overrides: dict[str, float] = Field(default_factory=dict)
    #: Second-order MUSCL reconstruction. Off = 1st order, more diffusive.
    second_order: bool = True
    max_steps: int = Field(default=2_000_000, gt=0)


class DemSpec(BaseModel):
    """Which DEM to use and where it came from."""

    #: Preference order; the first that succeeds is used and recorded.
    sources: list[str] = Field(
        default_factory=lambda: ["copernicus_s3", "planetary_computer", "opentopography"]
    )
    #: Explicit local file, bypassing all fetchers (e.g. Bhuvan CartoDEM).
    local_path: str | None = None
    #: Nominal resolution to request, m.
    resolution_m: float = 30.0


class TownSpec(BaseModel):
    """A downstream settlement we report arrival time and depth for."""

    name: str
    lon: float
    lat: float
    #: Approximate distance downstream of the dam, km. Informational only;
    #: the actual routed distance is computed from the flow path.
    approx_km_downstream: float | None = None
    population: int | None = None
    population_source: str | None = None


class Scenario(BaseModel):
    """A complete, runnable scenario. This is what `make simulate` consumes."""

    id: str
    name: str
    description: str = ""

    scenario_type: ScenarioType
    dam: DamSpec
    breach: BreachSpec = Field(default_factory=BreachSpec)
    reservoir: ReservoirState = Field(default_factory=ReservoirState)
    domain: DomainSpec = Field(default_factory=DomainSpec)
    solver: SolverSpec = Field(default_factory=SolverSpec)
    dem: DemSpec = Field(default_factory=DemSpec)

    #: Downstream towns for the arrival-time table and cross-section panel.
    towns: list[TownSpec] = Field(default_factory=list)

    #: Engines to run, by id. See floodguard.engines.availability.
    engines: list[str] = Field(default_factory=lambda: ["swe_fv"])

    #: Free-text notes that appear in the PDF report's assumptions section.
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _breach_within_dam(self):
        if self.breach.depth_m is not None and self.breach.depth_m > self.dam.structural_height_m:
            raise ValueError(
                f"breach.depth_m ({self.breach.depth_m}) exceeds the dam's "
                f"structural_height_m ({self.dam.structural_height_m})"
            )
        level = self.reservoir.initial_level_m
        if level is not None and self.dam.crest_elevation_m is not None:
            if level > self.dam.crest_elevation_m:
                raise ValueError(
                    f"reservoir.initial_level_m ({level}) is above the dam crest "
                    f"({self.dam.crest_elevation_m}); this is overtopping, not a "
                    f"valid initial condition"
                )
        return self

    # --- initial condition helpers -------------------------------------------------

    @property
    def initial_level_m(self) -> float:
        """Reservoir water surface elevation at t=0, m MSL.

        Falls back to FRL, then to crest elevation. Raises if the scenario
        supplies none of them, rather than inventing a level.
        """
        for value in (self.reservoir.initial_level_m, self.dam.frl_m, self.dam.crest_elevation_m):
            if value is not None:
                return value
        raise ValueError(
            f"scenario {self.id!r}: cannot determine the initial reservoir level. "
            f"Set reservoir.initial_level_m, dam.frl_m or dam.crest_elevation_m."
        )

    @property
    def water_head_m(self) -> float:
        """Head of water above the breach invert, m.

        This is the quantity that actually drives the outflow, so it is derived
        once here rather than recomputed differently in each module.
        """
        breach_depth = self.breach.depth_m or self.dam.structural_height_m
        if self.dam.crest_elevation_m is not None:
            invert = self.dam.crest_elevation_m - breach_depth
            return max(0.0, self.initial_level_m - invert)
        # No absolute crest elevation known: fall back to the structural height,
        # which is the standard assumption for a full-height breach.
        return min(breach_depth, self.dam.structural_height_m)

    @property
    def utm_crs(self) -> str:
        """Metric CRS for the compute grid, auto-selected if not set.

        India spans UTM 42N-47N. Northern hemisphere throughout, so EPSG
        326xx. Tehri (78.5E) lands in 44N; Hirakud (83.9E) in 45N.
        """
        if self.domain.crs:
            return self.domain.crs
        zone = int((self.dam.lon + 180) / 6) + 1
        epsg = 32600 + zone if self.dam.lat >= 0 else 32700 + zone
        return f"EPSG:{epsg}"

    # --- io ------------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> Scenario:
        """Load and validate a scenario file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"scenario file not found: {path}")
        with path.open(encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            raise ValueError(f"{path} does not contain a YAML mapping")
        return cls.model_validate(raw)

    def to_yaml(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self.model_dump(mode="json", exclude_none=True), fh, sort_keys=False)
        return path

    def provenance_inputs(self) -> dict[str, Any]:
        """The subset of the scenario that belongs in every output's metadata."""
        return {
            "scenario_id": self.id,
            "scenario_type": self.scenario_type.value,
            "dam": {
                "id": self.dam.id,
                "name": self.dam.name,
                "river": self.dam.river,
                "lon": self.dam.lon,
                "lat": self.dam.lat,
                "structural_height_m": self.dam.structural_height_m,
                "frl_m": self.dam.frl_m,
                "gross_storage_mcm": self.dam.gross_storage_mcm,
                "sources": self.dam.sources,
            },
            "initial_level_m": self.reservoir.initial_level_m,
            "resolution_m": self.domain.resolution_m,
            "crs": self.utm_crs,
            "cfl": self.solver.cfl,
            "duration_hours": self.solver.duration_hours,
        }
