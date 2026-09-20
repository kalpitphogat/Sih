"""Manning's n field.

Friction is the parameter dam-break results are most sensitive to after grid
resolution, and it is the one most often buried as a single hidden constant.
Here it is a raster, derived from land cover where land cover exists, and
overridable per class from the scenario YAML.

Default values follow Chow (1959), *Open-Channel Hydraulics*, Table 5-6, and
the USGS roughness guide (Arcement & Schneider 1989, WSP 2339). The ranges are
wide in the literature; the values chosen here are mid-range, and the scenario
file is where a user narrows them for a specific reach.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

#: Mid-range Manning's n by land-cover class, after Chow (1959) Table 5-6.
DEFAULT_MANNING_N: dict[str, float] = {
    "channel": 0.030,       # natural stream, clean, winding, some pools
    "water": 0.030,
    "bare": 0.025,          # bare earth / sand
    "grassland": 0.035,
    "agriculture": 0.050,   # cultivated, mature field crops
    "shrub": 0.070,
    "urban": 0.080,         # built-up, obstruction-dominated
    "forest": 0.100,        # dense trees with undergrowth
    "wetland": 0.070,
    "snow_ice": 0.025,
}

#: ESA WorldCover 10 m class codes -> our class names.
#: https://esa-worldcover.org/en/data-access  (v200 legend)
ESA_WORLDCOVER_MAP: dict[int, str] = {
    10: "forest",        # Tree cover
    20: "shrub",         # Shrubland
    30: "grassland",     # Grassland
    40: "agriculture",   # Cropland
    50: "urban",         # Built-up
    60: "bare",          # Bare / sparse vegetation
    70: "snow_ice",      # Snow and ice
    80: "water",         # Permanent water bodies
    90: "wetland",       # Herbaceous wetland
    95: "wetland",       # Mangroves
    100: "grassland",    # Moss and lichen
}


@dataclass
class RoughnessField:
    """A Manning's n raster plus the provenance of how it was built."""

    n: np.ndarray
    source: str
    class_values: dict[str, float]
    #: Fraction of cells assigned from real land cover, as opposed to default.
    coverage_fraction: float
    notes: list[str]

    def stats(self) -> dict[str, Any]:
        finite = self.n[np.isfinite(self.n)]
        return {
            "source": self.source,
            "coverage_fraction": round(self.coverage_fraction, 3),
            "min": float(finite.min()) if finite.size else None,
            "max": float(finite.max()) if finite.size else None,
            "mean": float(finite.mean()) if finite.size else None,
            "class_values": self.class_values,
            "notes": self.notes,
        }


def resolve_class_values(overrides: dict[str, float] | None) -> dict[str, float]:
    """Merge scenario overrides over the literature defaults."""
    values = dict(DEFAULT_MANNING_N)
    for key, value in (overrides or {}).items():
        if key not in values:
            log.warning(
                "scenario overrides Manning's n for unknown land-cover class %r; "
                "it will be available but is not produced by any mapper",
                key,
            )
        if not 0.005 <= value <= 0.5:
            raise ValueError(
                f"Manning's n override for {key!r} is {value}, outside the physically "
                f"plausible range 0.005-0.5. Check the units: n is dimensionless, "
                f"typically 0.02-0.15 for natural surfaces."
            )
        values[key] = value
    return values


def uniform(
    shape: tuple[int, int],
    default_n: float,
    overrides: dict[str, float] | None = None,
) -> RoughnessField:
    """A constant-n field, used when no land cover is available.

    Explicitly labelled so nothing downstream can mistake a uniform assumption
    for a mapped one.
    """
    values = resolve_class_values(overrides)
    return RoughnessField(
        n=np.full(shape, default_n, dtype=np.float32),
        source="uniform (no land cover available)",
        class_values=values,
        coverage_fraction=0.0,
        notes=[
            f"No land-cover raster was supplied, so Manning's n is uniform at "
            f"{default_n}. Friction is the second most sensitive parameter in a "
            f"dam-break simulation after grid resolution; a uniform value over a "
            f"mixed forest/urban/channel valley is a real source of error, and "
            f"results should be quoted with that caveat."
        ],
    )


def from_landcover(
    landcover: np.ndarray,
    class_map: dict[int, str],
    default_n: float,
    overrides: dict[str, float] | None = None,
    *,
    source: str = "ESA WorldCover 10 m v200",
) -> RoughnessField:
    """Map a land-cover code raster to Manning's n."""
    values = resolve_class_values(overrides)
    n = np.full(landcover.shape, default_n, dtype=np.float32)

    assigned = np.zeros(landcover.shape, dtype=bool)
    unmapped_codes: set[int] = set()

    for code in np.unique(landcover):
        code = int(code)
        name = class_map.get(code)
        if name is None:
            unmapped_codes.add(code)
            continue
        mask = landcover == code
        n[mask] = values[name]
        assigned |= mask

    notes = [f"Manning's n mapped from {source} using Chow (1959) Table 5-6 mid-range values."]
    if unmapped_codes:
        notes.append(
            f"Land-cover codes {sorted(unmapped_codes)} are not in the class map and "
            f"received the default n={default_n}."
        )

    return RoughnessField(
        n=n,
        source=source,
        class_values=values,
        coverage_fraction=float(assigned.mean()),
        notes=notes,
    )


def burn_channel(
    field: RoughnessField,
    channel_mask: np.ndarray,
    channel_n: float | None = None,
) -> RoughnessField:
    """Force channel cells to the channel roughness.

    Land-cover products classify a 30 m river cell as "water", which already
    maps to a channel-like n. This exists for the case where the channel is
    narrower than the land-cover pixel and got classified as its bank instead —
    common on Himalayan reaches, where it would otherwise apply forest
    roughness to the fastest-moving water in the domain.
    """
    n = field.n.copy()
    value = channel_n if channel_n is not None else field.class_values["channel"]
    n[channel_mask] = value
    return RoughnessField(
        n=n,
        source=field.source + " + channel burn",
        class_values=field.class_values,
        coverage_fraction=field.coverage_fraction,
        notes=field.notes
        + [f"{int(channel_mask.sum())} channel cells forced to n={value} from the flow network."],
    )
