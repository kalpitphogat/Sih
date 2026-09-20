"""Flood hazard classification and depth banding.

Depth alone does not describe danger. A metre of standing water is an
inconvenience; a metre moving at 3 m/s sweeps away an adult and overturns a
car. The classification here uses the depth-velocity product, which is the
basis of every published hazard standard.

Standard used
-------------
**Australian Institute for Disaster Resilience, Handbook 7 (2017),
*Managing the Floodplain*, Table 6.1** — derived from Smith et al. (2014),
*Australian Rainfall and Runoff Project 10: Appropriate Safety Criteria for
People*, and adopted widely outside Australia.

The classes are stated in terms of the vulnerability they describe, not as
abstract labels, because a district disaster management officer needs to know
what a class means for their population.

We cite a standard rather than inventing thresholds. Where India has an
official equivalent through NDMA, substituting it is a matter of editing this
table — the codes and the legend are read from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

STANDARD = (
    "Australian Institute for Disaster Resilience, Handbook 7 (2017), "
    "Managing the Floodplain, Table 6.1, after Smith et al. (2014) ARR Project 10"
)


@dataclass(frozen=True)
class HazardClass:
    """One hazard vulnerability class."""

    code: int
    label: str
    #: Upper bound of depth * velocity for this class, m2/s.
    dv_max: float
    #: Upper bound of depth alone, m. A deep, still pool is still dangerous.
    depth_max: float
    #: Upper bound of velocity alone, m/s.
    velocity_max: float
    description: str
    colour: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "dv_max_m2s": self.dv_max,
            "depth_max_m": self.depth_max,
            "velocity_max_ms": self.velocity_max,
            "description": self.description,
            "colour": self.colour,
        }


HAZARD_CLASSES: tuple[HazardClass, ...] = (
    HazardClass(
        1, "H1", 0.3, 0.3, 2.0,
        "Generally safe for people, vehicles and buildings.",
        "#c8e6c9",
    ),
    HazardClass(
        2, "H2", 0.6, 0.5, 2.0,
        "Unsafe for small vehicles.",
        "#fff59d",
    ),
    HazardClass(
        3, "H3", 0.6, 1.2, 2.0,
        "Unsafe for vehicles, children and the elderly.",
        "#ffcc80",
    ),
    HazardClass(
        4, "H4", 1.0, 2.0, 2.0,
        "Unsafe for people and all vehicles.",
        "#ef9a9a",
    ),
    HazardClass(
        5, "H5", 4.0, 4.0, 4.0,
        "Unsafe for vehicles and people. All buildings vulnerable to structural damage. "
        "Some less robust buildings subject to failure.",
        "#e53935",
    ),
    HazardClass(
        6, "H6", float("inf"), float("inf"), float("inf"),
        "Unsafe for vehicles and people. All building types considered vulnerable to failure.",
        "#7b1fa2",
    ),
)

#: Legend bins for the inundation map, in metres. These are the exact bins the
#: frontend legend renders, defined once here so the two cannot drift apart.
DEPTH_BANDS: tuple[tuple[float, float, str, str], ...] = (
    (0.1, 0.5, "0.1 - 0.5 m", "#2b7bba"),
    (0.5, 2.0, "0.5 - 2 m", "#7fc4e8"),
    (2.0, 5.0, "2 - 5 m", "#f2d024"),
    (5.0, 10.0, "5 - 10 m", "#e8762c"),
    (10.0, float("inf"), "> 10 m", "#8b1a1a"),
)


def classify(depth: np.ndarray, velocity: np.ndarray) -> np.ndarray:
    """Hazard class code per cell, 0 where dry.

    A cell falls in the lowest class whose depth, velocity AND depth-velocity
    limits it all satisfies. Taking the most severe of the three criteria is
    what makes deep-but-slow and shallow-but-fast water both classify as
    dangerous, which a depth-only map misses entirely.
    """
    dv = depth * velocity
    out = np.zeros(depth.shape, dtype=np.uint8)
    wet = depth > 0

    # Walk from the most severe class downward so the final assignment is the
    # lowest class that fits.
    for hazard in reversed(HAZARD_CLASSES):
        fits = (
            wet
            & (dv <= hazard.dv_max)
            & (depth <= hazard.depth_max)
            & (velocity <= hazard.velocity_max)
        )
        out[fits] = hazard.code

    # Anything wet that fits no bounded class is the top class.
    out[wet & (out == 0)] = HAZARD_CLASSES[-1].code
    return out


def band_index(depth: np.ndarray) -> np.ndarray:
    """Depth-band index per cell (1-based), 0 where below the lowest band."""
    out = np.zeros(depth.shape, dtype=np.uint8)
    for i, (lo, hi, _label, _colour) in enumerate(DEPTH_BANDS, start=1):
        out[(depth >= lo) & (depth < hi)] = i
    return out


def statistics(
    depth: np.ndarray,
    velocity: np.ndarray,
    cell_area_m2: float,
    wet_threshold_m: float = 0.3,
) -> dict[str, Any]:
    """Area by hazard class and by depth band. Every number computed, none fixed."""
    classes = classify(depth, velocity)
    bands = band_index(depth)

    by_class = []
    for hazard in HAZARD_CLASSES:
        cells = int((classes == hazard.code).sum())
        by_class.append(
            {**hazard.to_dict(), "cells": cells, "area_km2": cells * cell_area_m2 / 1e6}
        )

    by_band = []
    for i, (lo, hi, label, colour) in enumerate(DEPTH_BANDS, start=1):
        cells = int((bands == i).sum())
        by_band.append(
            {
                "index": i,
                "min_m": lo,
                "max_m": None if hi == float("inf") else hi,
                "label": label,
                "colour": colour,
                "cells": cells,
                "area_km2": cells * cell_area_m2 / 1e6,
            }
        )

    wet = depth >= wet_threshold_m
    return {
        "standard": STANDARD,
        "wet_threshold_m": wet_threshold_m,
        "flooded_area_km2": float(wet.sum() * cell_area_m2 / 1e6),
        "by_hazard_class": by_class,
        "by_depth_band": by_band,
        "max_depth_m": float(depth.max()) if wet.any() else None,
        "max_velocity_ms": float(velocity.max()) if wet.any() else None,
        "max_dv_m2s": float((depth * velocity).max()) if wet.any() else None,
        "mean_depth_in_flooded_area_m": float(depth[wet].mean()) if wet.any() else None,
    }
