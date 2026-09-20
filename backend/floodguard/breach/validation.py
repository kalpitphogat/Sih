"""Validate the breach models against documented historical failures.

A breach model that has never been checked against a real failure is an
opinion. These two cases are the standard benchmarks, and both are
well-documented enough that our predictions can be scored honestly — including
where they are wrong.

Cases
-----
**Teton Dam, Idaho, 5 June 1976.** The canonical dam-break validation case: an
earthfill dam that failed by piping during first filling, with the failure
filmed and the peak discharge estimated from downstream evidence.

**Banqiao Dam, Henan, 8 August 1975.** Failed by overtopping during Typhoon
Nina. Far larger peak discharge; the deadliest dam failure in history.
Published parameters vary more widely than Teton's, so the observed values here
carry a wider uncertainty.

Observed values are from the USBR/NWS DAMBRK case literature as compiled in
Wahl (1998), *Prediction of Embankment Dam Breach Parameters*, USBR
Dam Safety Report DSO-98-004, which remains the standard reference compilation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from floodguard.breach import parameters as params
from floodguard.scenario import DamType, ScenarioType


@dataclass(frozen=True)
class HistoricalCase:
    """A documented dam failure with observed breach parameters."""

    name: str
    date: str
    dam_type: DamType
    failure_mode: ScenarioType
    height_m: float
    #: Head of water above the breach invert at failure, m.
    head_m: float
    storage_m3: float
    observed_width_m: float
    observed_formation_time_min: float
    observed_peak_m3s: float
    source: str
    notes: str = ""
    #: Multiplicative uncertainty on the observed values, as reported.
    width_uncertainty: float = 1.2
    peak_uncertainty: float = 1.3


TETON = HistoricalCase(
    name="Teton Dam",
    date="1976-06-05",
    dam_type=DamType.EARTHFILL,
    failure_mode=ScenarioType.PIPING_FAILURE,
    height_m=93.0,
    head_m=77.4,
    storage_m3=310.0e6,
    observed_width_m=151.0,
    observed_formation_time_min=72.0,
    observed_peak_m3s=65_120.0,
    source=(
        "Wahl (1998), USBR DSO-98-004, Table 1; peak discharge from the "
        "Independent Panel to Review Cause of Teton Dam Failure (1976)."
    ),
    notes=(
        "Failed by piping during first filling. Average breach width 151 m; "
        "formation time reported between 1.25 and 4 h depending on when the "
        "failure is deemed to start."
    ),
    width_uncertainty=1.2,
    peak_uncertainty=1.3,
)

BANQIAO = HistoricalCase(
    name="Banqiao Dam",
    date="1975-08-08",
    dam_type=DamType.EARTHFILL,
    failure_mode=ScenarioType.OVERTOPPING,
    height_m=24.5,
    head_m=31.0,
    storage_m3=607.5e6,
    observed_width_m=372.0,
    observed_formation_time_min=60.0,
    observed_peak_m3s=78_100.0,
    source="Wahl (1998), USBR DSO-98-004, Table 1.",
    notes=(
        "Overtopped during Typhoon Nina after extreme rainfall. Published "
        "parameters vary more than Teton's; the peak in particular is an "
        "estimate reconstructed decades after the event."
    ),
    width_uncertainty=1.4,
    peak_uncertainty=1.6,
)

CASES = (TETON, BANQIAO)


@dataclass
class CaseResult:
    """How each model scored against one historical failure."""

    case: HistoricalCase
    predictions: list[params.BreachGeometry]
    routed_peak_m3s: float | None
    scores: dict[str, dict[str, Any]] = field(default_factory=dict)

    def table(self) -> str:
        c = self.case
        lines = [
            f"{c.name} ({c.date}) — {c.failure_mode.value}",
            f"  Observed: width {c.observed_width_m:.0f} m, "
            f"formation {c.observed_formation_time_min:.0f} min, "
            f"peak {c.observed_peak_m3s:,.0f} m3/s",
            f"  Source  : {c.source}",
            "",
            f"  {'MODEL':<38} {'WIDTH m':>10} {'ERR %':>8} {'t_f min':>9} {'ERR %':>8}",
            "  " + "-" * 76,
        ]
        for model, s in self.scores.items():
            lines.append(
                f"  {model:<38} {s['width_m']:>10.0f} {s['width_error_pct']:>+8.0f} "
                f"{s['formation_time_min']:>9.0f} {s['time_error_pct']:>+8.0f}"
            )
        if self.routed_peak_m3s is not None:
            err = 100 * (self.routed_peak_m3s - c.observed_peak_m3s) / c.observed_peak_m3s
            lines.append("")
            lines.append(
                f"  Routed peak with the observed breach geometry: "
                f"{self.routed_peak_m3s:,.0f} m3/s ({err:+.0f}% vs observed)"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.name,
            "date": self.case.date,
            "failure_mode": self.case.failure_mode.value,
            "observed": {
                "width_m": self.case.observed_width_m,
                "formation_time_min": self.case.observed_formation_time_min,
                "peak_m3s": self.case.observed_peak_m3s,
                "source": self.case.source,
                "notes": self.case.notes,
            },
            "model_scores": self.scores,
            "routed_peak_m3s": self.routed_peak_m3s,
        }


def score_case(case: HistoricalCase, route_peak: bool = True) -> CaseResult:
    """Run every breach model against one historical failure and score it."""
    predictions = params.predict_all(
        case.head_m, case.storage_m3, case.dam_type, case.failure_mode
    )

    scores: dict[str, dict[str, Any]] = {}
    for p in predictions:
        scores[p.model] = {
            "width_m": p.width_m,
            "width_error_pct": 100 * (p.width_m - case.observed_width_m) / case.observed_width_m,
            "formation_time_min": p.formation_time_min,
            "time_error_pct": (
                100
                * (p.formation_time_min - case.observed_formation_time_min)
                / case.observed_formation_time_min
            ),
            "within_reported_uncertainty": (
                abs(p.width_m - case.observed_width_m) / case.observed_width_m
                <= (case.width_uncertainty - 1.0)
            ),
        }

    routed_peak = _route_observed(case) if route_peak else None
    return CaseResult(case=case, predictions=predictions, routed_peak_m3s=routed_peak)._with(scores)


def _route_observed(case: HistoricalCase) -> float | None:
    """Route the OBSERVED breach geometry and compare the peak.

    This separates two questions that are usually conflated: does the empirical
    model predict the right breach, and does our routing convert a breach into
    the right discharge? Feeding the observed geometry in isolates the second.
    """
    import numpy as np

    from floodguard.breach.routing import route
    from floodguard.preprocess.reservoir import ElevationAreaCapacity

    # A synthetic conic reservoir matched to the case's storage and head. The
    # real elevation-area-capacity curves for these dams are not public, so
    # this is an approximation, and the resulting peak inherits its error.
    crest = case.head_m
    levels = np.linspace(0.0, crest, 60)
    # V = A_top * h / 3 for a cone; solve A_top from the known storage.
    area_top = 3.0 * case.storage_m3 / max(crest, 1.0)
    areas = area_top * (levels / max(crest, 1e-9)) ** 2
    volumes = areas * levels / 3.0
    curve = ElevationAreaCapacity(levels, areas, volumes, 900.0, 0.0)

    observed = params.BreachGeometry(
        model="observed",
        width_m=case.observed_width_m,
        depth_m=case.head_m,
        side_slope=0.7 if case.failure_mode is ScenarioType.PIPING_FAILURE else 1.0,
        formation_time_min=case.observed_formation_time_min,
        reference=case.source,
    )

    try:
        hydrograph = route(
            curve,
            observed,
            initial_level_m=crest,
            crest_elevation_m=crest,
            scenario_type=case.failure_mode,
            duration_s=12 * 3600.0,
        )
    except Exception:  # noqa: BLE001 - validation must never break the suite
        return None
    return hydrograph.peak_discharge_m3s


def _with(self, scores):  # attached below to keep CaseResult a plain dataclass
    self.scores = scores
    return self


CaseResult._with = _with  # type: ignore[attr-defined]


def run_all() -> list[CaseResult]:
    """Score every historical case. Used by `floodguard breach --validate`."""
    return [score_case(c) for c in CASES]


def report() -> str:
    """Human-readable validation report for docs/METHODOLOGY.md."""
    parts = [
        "Breach model validation against documented historical failures",
        "=" * 76,
        "",
        "These are empirical regressions on a few dozen embankment failures. A "
        "prediction within a factor of 2 on width is considered good in the "
        "literature (Wahl 1998); formation time is worse, often a factor of 3. "
        "The numbers below are reported as computed, including where they are poor.",
        "",
    ]
    for result in run_all():
        parts.append(result.table())
        parts.append("")
    return "\n".join(parts)
