"""Pydantic models — the API contract.

The frontend's TypeScript types are generated from the OpenAPI schema these
produce, so this file is the single definition of what the API returns. Any
field the UI renders must exist here with a documented meaning.

Note the pervasive `| None` on result fields. That is the "not computed" state
from engineering rule 1, and it is a different thing from zero. The UI renders
None as an em dash.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# --- catalog ----------------------------------------------------------------------


class RiverSummary(BaseModel):
    name: str
    dam_count: int
    states: list[str]


class DamSummary(BaseModel):
    """One row of the dam dropdown."""

    id: str
    name: str
    river: str
    state: str
    lon: float
    lat: float
    dam_type: str
    structural_height_m: float | None
    crest_length_m: float | None
    gross_storage_mcm: float | None
    live_storage_mcm: float | None
    frl_m: float | None = Field(
        default=None,
        description=(
            "Full reservoir level, m MSL. Null for every catalog record: the CWC NRLD "
            "tables transcribed do not carry it, and it is never guessed."
        ),
    )
    mddl_m: float | None = None
    commissioned_year: int | None
    nrld_id: str | None = None
    notes: list[str] = Field(default_factory=list)
    sources: dict[str, str] = Field(
        default_factory=dict,
        description="Per-field citation, so any number in the UI traces to a source.",
    )


class ReservoirCurvePoint(BaseModel):
    level_m: float
    area_km2: float
    volume_mcm: float


class ReservoirCurve(BaseModel):
    dam_id: str
    method: str
    points: list[ReservoirCurvePoint]
    derived_gross_storage_mcm: float | None
    catalog_gross_storage_mcm: float | None
    storage_difference_pct: float | None
    bathymetry_reconstructed: bool
    warnings: list[str] = Field(default_factory=list)


# --- engines ----------------------------------------------------------------------


class EngineStatusModel(BaseModel):
    id: str
    display_name: str = Field(
        description=(
            "The exact string the UI badge must display. Never substitute different "
            "wording: when Delft3D binaries are absent this reads "
            "'FloodGuard-SWE (Delft3D-class FV solver)', and that is deliberate."
        )
    )
    kind: Literal["native", "external", "unavailable"]
    available: bool
    is_real_solver: bool
    version: str | None = None
    detail: str = ""
    substitute_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class EngineHealth(BaseModel):
    engines: list[EngineStatusModel]
    honesty_statement: str


# --- scenario / simulate ----------------------------------------------------------


class BreachInput(BaseModel):
    shape: Literal["trapezoidal", "rectangular", "triangular"] = "trapezoidal"
    growth: Literal["linear", "sine", "parabolic"] = "linear"
    width_m: float | None = Field(default=None, gt=0)
    depth_m: float | None = Field(default=None, gt=0)
    side_slope: float = Field(default=1.0, ge=0)
    formation_time_min: float | None = Field(default=None, gt=0)
    parameter_model: str = "froehlich_2008"


class SimulationRequest(BaseModel):
    """POST /api/simulate."""

    scenario_id: str | None = Field(
        default=None, description="A bundled scenario id, e.g. tehri_bhagirathi."
    )
    dam_id: str | None = Field(
        default=None, description="Build a scenario from the catalog instead."
    )
    scenario_type: Literal[
        "complete_dam_break",
        "partial_breach",
        "piping_failure",
        "overtopping",
        "controlled_release",
        "landslide_dam_breach",
    ] = "complete_dam_break"

    reservoir_level_m: float | None = None
    breach: BreachInput = Field(default_factory=BreachInput)

    engines: list[str] = Field(default_factory=lambda: ["swe_fv"])
    resolution_m: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Compute grid resolution. Cost scales as roughly 1/res^3, and peak depths "
            "are genuinely resolution-sensitive, so this is exposed rather than hidden."
        ),
    )
    duration_hours: float | None = Field(default=None, gt=0)
    cfl: float | None = Field(default=None, gt=0, le=1.0)
    wet_threshold_m: float | None = Field(default=None, gt=0)
    manning_n_overrides: dict[str, float] = Field(default_factory=dict)


class BreachPrediction(BaseModel):
    model: str
    width_m: float
    depth_m: float
    side_slope: float
    formation_time_min: float
    reference: str
    applicable: bool
    caveats: list[str] = Field(default_factory=list)


class BreachComparison(BaseModel):
    """What the UI shows as ghost hints next to each breach field."""

    used: BreachPrediction
    predictions: list[BreachPrediction]
    spread: dict[str, Any]


class JobCreated(BaseModel):
    job_id: str
    status: str
    scenario_id: str
    websocket: str = Field(description="WebSocket path for live progress.")


class JobState(BaseModel):
    id: str
    scenario_id: str
    status: Literal[
        "queued", "running", "succeeded", "failed", "cancelled", "interrupted"
    ]
    created_utc: str
    fraction: float
    phase: str
    message: str
    log: list[str] = Field(default_factory=list)
    eta_seconds: float | None = None
    elapsed_seconds: float | None = None
    error: str | None = None


# --- results ----------------------------------------------------------------------


class EngineSummary(BaseModel):
    """The four KPI cards, per engine. None means not computed, not zero."""

    engine_id: str
    engine_display_name: str
    is_real_solver: bool
    substituted: bool = False
    honesty_note: str = ""
    flooded_area_km2: float | None
    max_depth_m: float | None
    max_velocity_ms: float | None
    earliest_arrival_min: float | None
    max_hazard_m2s: float | None = None
    runtime_s: float | None = None
    steps: int | None = None
    mass_error: float | None = None
    warnings: list[str] = Field(default_factory=list)


class ResultSummary(BaseModel):
    run_id: str
    scenario_id: str
    engines: list[EngineSummary]
    hazard: dict[str, Any]
    breach: BreachComparison
    peak_discharge_m3s: float | None
    time_to_peak_min: float | None
    total_volume_mcm: float | None
    resolution_m: float
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ComparisonRow(BaseModel):
    """One row of the Model Comparison table. Every cell computed."""

    metric: str
    unit: str
    values: dict[str, float | None]
    difference_pct: float | None = Field(
        default=None,
        description=(
            "Signed percentage difference between the two engines, or null when "
            "fewer than two engines produced a value."
        ),
    )


class Comparison(BaseModel):
    run_id: str
    engines: list[str]
    engine_display_names: dict[str, str]
    rows: list[ComparisonRow]
    #: Critical Success Index of flood-extent overlap between the two engines.
    critical_success_index: float | None = None
    extent_rmse_m: float | None = None
    note: str = ""


class TownResult(BaseModel):
    name: str
    lon: float
    lat: float
    population: int | None
    population_source: str | None
    max_depth_m: float | None
    max_velocity_ms: float | None
    arrival_min: float | None
    in_domain: bool


class ImpactMetric(BaseModel):
    label: str
    value: float | int | None
    unit: str
    computed: bool
    reason: str = ""
    assumption: str = ""
    display: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ImpactResponse(BaseModel):
    run_id: str
    metrics: dict[str, ImpactMetric]
    facilities: list[dict[str, Any]] = Field(default_factory=list)
    evacuation_priority: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class HydrographSeries(BaseModel):
    label: str
    unit: str
    times_hours: list[float]
    values: list[float | None]


class HydrographResponse(BaseModel):
    run_id: str
    series: list[HydrographSeries]
    note: str = ""


class CrossSectionResponse(BaseModel):
    run_id: str
    location: str
    chainage_m: float
    offset_from_path_m: float | None
    offsets_m: list[float]
    bed_m: list[float | None]
    water_surface_m: list[float | None]
    max_depth_m: float | None
    note: str = ""


class ExportListing(BaseModel):
    run_id: str
    exports: list[dict[str, Any]]
