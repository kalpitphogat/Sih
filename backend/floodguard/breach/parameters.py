"""Empirical breach parameter models.

Predict breach width, side slope and formation time from dam height and
reservoir storage. Three published regressions are implemented, because they
disagree — often by a factor of two — and showing the user all three next to
their own input is more honest than picking one and calling it the answer.

References
----------
Froehlich, D.C. (2008). "Embankment Dam Breach Parameters and Their
    Uncertainties." *Journal of Hydraulic Engineering* 134(12), 1708-1721.
Von Thun, J.L. & Gillette, D.R. (1990). "Guidance on Breach Parameters."
    Unpublished internal document, U.S. Bureau of Reclamation.
MacDonald, T.C. & Langridge-Monopolis, J. (1984). "Breaching Characteristics
    of Dam Failures." *Journal of Hydraulic Engineering* 110(5), 567-586.

All three are regressions on historical embankment-dam failures. None of them
applies to a concrete gravity or arch dam, whose failure is a structural
collapse rather than progressive erosion; `predict_all` says so rather than
returning a number that looks authoritative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from floodguard.scenario import DamType, ScenarioType

#: Models valid only for embankment (earthfill/rockfill) dams.
EMBANKMENT_TYPES = {DamType.EARTHFILL, DamType.ROCKFILL}

#: Froehlich's k0: overtopping failures breach wider than piping failures.
FROEHLICH_K0_OVERTOPPING = 1.3
FROEHLICH_K0_PIPING = 1.0

#: Von Thun & Gillette erodibility categories.
ERODIBILITY = ("low", "medium", "high")


@dataclass
class BreachGeometry:
    """Predicted breach parameters from one model."""

    model: str
    #: Average breach width at mid-height, m.
    width_m: float
    #: Vertical extent of the breach, m.
    depth_m: float
    #: Horizontal:vertical breach side slope.
    side_slope: float
    #: Breach formation time, minutes.
    formation_time_min: float
    reference: str
    applicable: bool = True
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "width_m": round(self.width_m, 1),
            "depth_m": round(self.depth_m, 1),
            "side_slope": self.side_slope,
            "formation_time_min": round(self.formation_time_min, 1),
            "reference": self.reference,
            "applicable": self.applicable,
            "caveats": self.caveats,
        }


def _is_piping(scenario_type: ScenarioType) -> bool:
    return scenario_type == ScenarioType.PIPING_FAILURE


def froehlich_2008(
    height_m: float,
    storage_m3: float,
    scenario_type: ScenarioType,
) -> BreachGeometry:
    """Froehlich (2008), the current default for engineered embankments.

        B_avg = 0.27 * k0 * V^0.32 * h^0.04        [m]
        t_f   = 63.2 * sqrt(V / (g * h^2))         [s]

    where V is reservoir volume at failure (m3) and h is the height of water
    above the breach invert (m). Side slopes are 1.0H:1V for overtopping and
    0.7H:1V for piping — Froehlich reports these as fixed values, not
    regressions.

    Froehlich's own reported uncertainty is roughly a factor of 1.5 on width
    and 2 on formation time, which is carried in the caveats rather than
    dropped.
    """
    k0 = FROEHLICH_K0_PIPING if _is_piping(scenario_type) else FROEHLICH_K0_OVERTOPPING
    side_slope = 0.7 if _is_piping(scenario_type) else 1.0

    width = 0.27 * k0 * (storage_m3**0.32) * (height_m**0.04)
    formation_s = 63.2 * math.sqrt(storage_m3 / (9.81 * height_m**2))

    return BreachGeometry(
        model="froehlich_2008",
        width_m=width,
        depth_m=height_m,
        side_slope=side_slope,
        formation_time_min=formation_s / 60.0,
        reference="Froehlich (2008), J. Hydraulic Eng. 134(12), 1708-1721",
        caveats=[
            "Regression on 74 historical embankment failures. Froehlich reports "
            "roughly a factor of 1.5 uncertainty on width and 2 on formation time.",
            f"k0={k0} ({'piping' if _is_piping(scenario_type) else 'overtopping'}), "
            f"side slope {side_slope}H:1V.",
        ],
    )


def _vtg_offset_m(storage_m3: float) -> float:
    """Von Thun & Gillette's reservoir-size offset Cb, in metres.

    A step function of storage, given in the original guidance as a table.
    """
    v = storage_m3
    if v < 1.23e6:
        return 6.1
    if v < 6.17e6:
        return 18.3
    if v < 1.233e7:
        return 42.7
    return 54.9


def von_thun_gillette_1990(
    height_m: float,
    storage_m3: float,
    scenario_type: ScenarioType,
    erodibility: str = "medium",
) -> BreachGeometry:
    """Von Thun & Gillette (1990), USBR guidance.

        B_avg = 2.5 * h_w + Cb                     [m]

    Formation time has two forms; the erosion-resistant form is used for low
    erodibility and the easily-erodible form otherwise:

        t_f (erosion resistant) = 0.02 * h_w + 0.25        [h]
        t_f (easily erodible)   = 0.015 * h_w              [h]

    Simple enough to compute mentally, which is exactly why it remains a
    standard sanity check against Froehlich.
    """
    if erodibility not in ERODIBILITY:
        raise ValueError(f"erodibility must be one of {ERODIBILITY}, got {erodibility!r}")

    width = 2.5 * height_m + _vtg_offset_m(storage_m3)

    if erodibility == "low":
        formation_h = 0.02 * height_m + 0.25
        slope = 0.5
    else:
        formation_h = 0.015 * height_m
        slope = 1.0

    return BreachGeometry(
        model="von_thun_gillette_1990",
        width_m=width,
        depth_m=height_m,
        side_slope=slope,
        formation_time_min=formation_h * 60.0,
        reference="Von Thun & Gillette (1990), USBR guidance",
        caveats=[
            f"Erodibility assumed '{erodibility}'. This drives both the formation time "
            f"formula and the side slope, and it is a judgement about the embankment "
            f"material, not a measurement.",
            "Width depends only on water depth and a stepped storage offset, so it is "
            "insensitive to reservoir volume within each step.",
        ],
    )


def macdonald_langridge_monopolis_1984(
    height_m: float,
    storage_m3: float,
    scenario_type: ScenarioType,
) -> BreachGeometry:
    """MacDonald & Langridge-Monopolis (1984), earthfill embankments.

    Predicts the eroded *volume* of embankment material, then converts it to a
    breach geometry:

        V_er = 0.0261 * (V_out * h_w)^0.769        [m3]
        t_f  = 0.0179 * V_er^0.364                 [h]

    The eroded volume is the primary prediction; width is back-calculated
    assuming a trapezoid with 0.5H:1V side slopes through the full dam height.
    That conversion is an assumption of ours, not of the original paper, and is
    recorded as such.
    """
    outflow_volume = storage_m3
    eroded_m3 = 0.0261 * ((outflow_volume * height_m) ** 0.769)
    formation_h = 0.0179 * (eroded_m3**0.364)

    # Back out an average width from the eroded prism: V = L * h * (B + z*h),
    # where L is the embankment thickness at the base. Without a crest-width
    # and slope description we assume the eroded prism spans the dam height
    # with 0.5H:1V sides and an embankment base thickness of 3*h.
    side_slope = 0.5
    base_thickness = 3.0 * height_m
    area = eroded_m3 / max(base_thickness, 1.0)
    width = max(area / max(height_m, 1.0) - side_slope * height_m, 1.0)

    return BreachGeometry(
        model="macdonald_langridge_monopolis_1984",
        width_m=width,
        depth_m=height_m,
        side_slope=side_slope,
        formation_time_min=formation_h * 60.0,
        reference="MacDonald & Langridge-Monopolis (1984), J. Hydraulic Eng. 110(5), 567-586",
        caveats=[
            f"The paper predicts eroded embankment volume ({eroded_m3:,.0f} m3), not width "
            f"directly. Width here is back-calculated assuming a prism of base thickness "
            f"3x the dam height with 0.5H:1V sides — our assumption, not the authors'.",
            "Derived for earthfill dams; unreliable for rockfill.",
        ],
    )


def predict_all(
    height_m: float,
    storage_m3: float,
    dam_type: DamType,
    scenario_type: ScenarioType,
    erodibility: str = "medium",
) -> list[BreachGeometry]:
    """Run every applicable model. Returns them in a stable order.

    For a concrete or masonry dam all three are marked inapplicable and carry a
    caveat saying so. They still return numbers, because a user who overrides
    them deserves to see what the embankment regressions would have said, but
    nothing downstream may present those numbers as a prediction.
    """
    is_embankment = dam_type in EMBANKMENT_TYPES
    results = [
        froehlich_2008(height_m, storage_m3, scenario_type),
        von_thun_gillette_1990(height_m, storage_m3, scenario_type, erodibility),
        macdonald_langridge_monopolis_1984(height_m, storage_m3, scenario_type),
    ]

    if not is_embankment:
        note = (
            f"This dam is classified {dam_type.value}. All three breach-parameter models "
            f"are regressions on EMBANKMENT failures, where the breach grows by "
            f"progressive erosion. A concrete or masonry dam fails by structural "
            f"collapse — typically of one or more monoliths, essentially instantaneously. "
            f"These predictions do not apply; supply breach.width_m and "
            f"breach.formation_time_min explicitly, or use scenario_type "
            f"complete_dam_break with a formation time near zero."
        )
        for r in results:
            r.applicable = False
            r.caveats.insert(0, note)

    return results


def consensus(
    models: list[BreachGeometry],
) -> dict[str, Any]:
    """Spread across the models — the number that tells a user how uncertain this is.

    The mean is offered for convenience, but the ratio between the widest and
    narrowest prediction is the value worth showing: when it is 2 or more, the
    breach geometry is the dominant uncertainty in the whole simulation, and no
    amount of solver accuracy will fix that.
    """
    applicable = [m for m in models if m.applicable] or models
    widths = [m.width_m for m in applicable]
    times = [m.formation_time_min for m in applicable]

    return {
        "n_models": len(applicable),
        "width_m": {
            "min": round(min(widths), 1),
            "max": round(max(widths), 1),
            "mean": round(sum(widths) / len(widths), 1),
            "spread_ratio": round(max(widths) / max(min(widths), 1e-6), 2),
        },
        "formation_time_min": {
            "min": round(min(times), 1),
            "max": round(max(times), 1),
            "mean": round(sum(times) / len(times), 1),
            "spread_ratio": round(max(times) / max(min(times), 1e-6), 2),
        },
        "interpretation": (
            "A spread ratio above ~2 means the empirical models disagree more than the "
            "hydrodynamic solver's own error, so breach geometry — not numerics — is the "
            "dominant uncertainty in the resulting inundation map."
        ),
    }


def resolve(
    scenario,
) -> tuple[BreachGeometry, list[BreachGeometry], dict[str, Any]]:
    """Final breach geometry for a scenario, plus every prediction for comparison.

    User-supplied values always win; the predictions are still computed so the
    UI can show them as ghost hints and so the report can state how far the
    user's input sits from the empirical range.
    """
    height = scenario.water_head_m
    storage_m3 = (scenario.dam.gross_storage_mcm or 0.0) * 1e6

    predictions = predict_all(
        height,
        storage_m3,
        scenario.dam.dam_type,
        scenario.scenario_type,
    )
    by_name = {p.model: p for p in predictions}
    chosen_model = by_name.get(scenario.breach.parameter_model, predictions[0])

    used = BreachGeometry(
        model=(
            "user-specified"
            if scenario.breach.width_m and scenario.breach.formation_time_min
            else f"{chosen_model.model} (with user overrides where supplied)"
        ),
        width_m=scenario.breach.width_m or chosen_model.width_m,
        depth_m=scenario.breach.depth_m or scenario.dam.structural_height_m,
        side_slope=(
            scenario.breach.side_slope
            if scenario.breach.side_slope is not None
            else chosen_model.side_slope
        ),
        formation_time_min=(
            scenario.breach.formation_time_min or chosen_model.formation_time_min
        ),
        reference=chosen_model.reference,
        applicable=chosen_model.applicable,
        caveats=list(chosen_model.caveats),
    )

    stats = consensus(predictions)

    # Flag a user value that sits outside what any model predicts. Not an
    # error: the user may know something the regressions do not. But it must
    # be visible.
    if scenario.breach.width_m:
        lo, hi = stats["width_m"]["min"], stats["width_m"]["max"]
        if not lo * 0.5 <= scenario.breach.width_m <= hi * 2.0:
            used.caveats.append(
                f"The specified breach width of {scenario.breach.width_m:.0f} m sits well "
                f"outside the empirical range ({lo:.0f}-{hi:.0f} m) for a dam of this "
                f"height and storage. It will be used as given."
            )

    return used, predictions, stats
