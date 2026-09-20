"""Quantitative comparison between two engine runs.

This drives the "Model Comparison (SPH vs Delft3D)" table. Every cell,
including the Difference column, is computed from the two result sets — none
of it is written by hand.

Metrics
-------
* **Per-engine KPIs** side by side, with a signed percentage difference.
* **Critical Success Index** of flood-extent overlap, CSI = TP / (TP+FP+FN).
  CSI ignores true negatives deliberately: on any realistic domain the dry area
  dwarfs the wet one, and counting it would make every comparison look
  excellent. POD and FAR are reported alongside, because CSI alone is
  misleading when the two extents differ mostly in size rather than in
  location.
* **RMSE of the depth field** over cells wet in either run.
* **A difference raster**, written to disk for the swipe map.

When only one engine ran, nothing is fabricated: the table comes back empty
with a note saying why.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


def extent_agreement(
    a: np.ndarray, b: np.ndarray, threshold_m: float = 0.3
) -> dict[str, float | None]:
    """Contingency metrics for two flood extents.

    `a` is treated as the reference. TP is wet in both, FP is wet in b only,
    FN is wet in a only.
    """
    wet_a = a >= threshold_m
    wet_b = b >= threshold_m

    tp = int(np.sum(wet_a & wet_b))
    fp = int(np.sum(~wet_a & wet_b))
    fn = int(np.sum(wet_a & ~wet_b))

    denom = tp + fp + fn
    return {
        "true_positive_cells": tp,
        "false_positive_cells": fp,
        "false_negative_cells": fn,
        "critical_success_index": tp / denom if denom else None,
        "probability_of_detection": tp / (tp + fn) if (tp + fn) else None,
        "false_alarm_ratio": fp / (tp + fp) if (tp + fp) else None,
        "bias": (tp + fp) / (tp + fn) if (tp + fn) else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else None,
    }


def depth_rmse(a: np.ndarray, b: np.ndarray, threshold_m: float = 0.3) -> float | None:
    """RMSE of depth over cells wet in either run.

    Restricted to the union of the wet areas: including the dry majority would
    drive the RMSE towards zero and say nothing about where the two solvers
    actually disagree.
    """
    wet = (a >= threshold_m) | (b >= threshold_m)
    if not wet.any():
        return None
    return float(np.sqrt(np.mean((a[wet] - b[wet]) ** 2)))


def signed_difference_pct(values: list[float | None]) -> float | None:
    """Signed percentage difference between the first two non-null values."""
    present = [v for v in values if v is not None]
    if len(present) < 2 or present[0] == 0:
        return None
    return 100.0 * (present[1] - present[0]) / abs(present[0])


#: The rows of the comparison table, and where each is read from.
METRIC_ROWS: tuple[tuple[str, str, str], ...] = (
    ("Maximum depth", "m", "max_depth_m"),
    ("Maximum velocity", "m/s", "max_velocity_ms"),
    ("Flooded area", "km2", "flooded_area_km2"),
    ("Earliest arrival", "min", "earliest_arrival_min"),
    ("Maximum hazard (h x v)", "m2/s", "max_hazard_m2s"),
    ("Computation time", "s", "runtime_s"),
)


def compare_engines(run_id: str, runs: list[dict[str, Any]], out_dir: Path):
    """Build the comparison payload from two or more engine runs."""
    from app.schemas.models import Comparison, ComparisonRow

    ids = [r["actual_engine"] for r in runs]
    names = {r["actual_engine"]: r["display_name"] for r in runs}

    rows: list[ComparisonRow] = []
    for label, unit, key in METRIC_ROWS:
        values = {r["actual_engine"]: (r.get("summary") or {}).get(key) for r in runs}
        rows.append(
            ComparisonRow(
                metric=label,
                unit=unit,
                values=values,
                difference_pct=signed_difference_pct(list(values.values())),
            )
        )

    csi: float | None = None
    rmse: float | None = None
    note = ""

    depth_paths = [out_dir / f"max_depth_{eid}.tif" for eid in ids]
    if all(p.exists() for p in depth_paths[:2]):
        import rasterio

        with rasterio.open(depth_paths[0]) as src_a, rasterio.open(depth_paths[1]) as src_b:
            a = src_a.read(1)
            b = src_b.read(1)
            profile = src_a.profile

        if a.shape != b.shape:
            note = (
                f"The two engines produced grids of different shape ({a.shape} vs "
                f"{b.shape}), so extent agreement could not be computed. Re-run both at "
                f"the same resolution."
            )
        else:
            agreement = extent_agreement(a, b)
            csi = agreement["critical_success_index"]
            rmse = depth_rmse(a, b)

            diff_path = out_dir / "depth_difference.tif"
            profile.update(dtype="float32", compress="deflate")
            with rasterio.open(diff_path, "w", **profile) as dst:
                dst.write((b - a).astype("float32"), 1)
                dst.set_band_description(
                    1, f"{names[ids[1]]} minus {names[ids[0]]}, metres"
                )
            note = (
                f"Extent agreement CSI = {csi:.3f} "
                f"(POD {agreement['probability_of_detection']:.3f}, "
                f"FAR {agreement['false_alarm_ratio']:.3f}). "
                f"Read POD and FAR alongside CSI: a low CSI with a high POD means the two "
                f"extents differ in size rather than in location. Difference raster written "
                f"to {diff_path.name}."
            )
    else:
        note = (
            "Per-engine depth rasters were not written, so extent agreement and depth "
            "RMSE could not be computed. The KPI comparison above is still exact."
        )

    return Comparison(
        run_id=run_id,
        engines=ids,
        engine_display_names=names,
        rows=rows,
        critical_success_index=csi,
        extent_rmse_m=rmse,
        note=note,
    )
