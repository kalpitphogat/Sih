"""Cross-section extraction along the routed flow path.

These feed the "Cross-section View" panel: terrain profile with the maximum
water level drawn over it, at named towns and at regular chainages.

A cross-section is only meaningful if it is perpendicular to the flow. The
local flow direction is estimated by fitting a line through a window of path
points rather than using the single D8 step, because a D8 step is quantised to
45 degrees and would make every section either axis-aligned or diagonal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class CrossSection:
    """One perpendicular section through the valley."""

    name: str
    #: Index into the flow path at which this section is taken.
    path_index: int
    #: Distance downstream from the dam along the path, m.
    chainage_m: float
    #: Sample offsets from the centreline, m (negative = left bank).
    offsets_m: np.ndarray
    #: Bed elevation at each offset, m MSL.
    bed_m: np.ndarray
    #: World coordinates of each sample, in the DEM's CRS.
    xs: np.ndarray
    ys: np.ndarray
    #: Unit vector along the section, in world units.
    normal: tuple[float, float]
    #: For a named town, how far it sits from the traced flow path, m.
    #: Large values mean the section is near the river but the town is not.
    offset_from_path_m: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "chainage_m": self.chainage_m,
            "path_index": self.path_index,
            "offset_from_path_m": self.offset_from_path_m,
            "offsets_m": self.offsets_m.tolist(),
            "bed_m": np.where(np.isfinite(self.bed_m), self.bed_m, None).tolist(),
            "thalweg_m": float(np.nanmin(self.bed_m)) if np.isfinite(self.bed_m).any() else None,
        }

    def sample_raster(self, raster: np.ndarray, transform) -> np.ndarray:
        """Sample another raster (depth, velocity) along this exact section."""
        from rasterio.transform import rowcol

        out = np.full(len(self.xs), np.nan)
        rows, cols = raster.shape
        for i, (x, y) in enumerate(zip(self.xs, self.ys)):
            r, c = rowcol(transform, x, y)
            if 0 <= r < rows and 0 <= c < cols:
                out[i] = raster[r, c]
        return out


def _local_direction(path_xy: np.ndarray, index: int, window: int = 8) -> tuple[float, float]:
    """Unit flow direction at a path index, from a least-squares fit.

    Fitting over a window smooths out D8's 45-degree quantisation. Falls back to
    the chord across the window if the fit is degenerate (a perfectly straight
    north-south reach makes the slope infinite).
    """
    lo = max(0, index - window)
    hi = min(len(path_xy), index + window + 1)
    seg = path_xy[lo:hi]
    if len(seg) < 2:
        return (1.0, 0.0)

    chord = seg[-1] - seg[0]
    norm = float(np.hypot(chord[0], chord[1]))
    if norm < 1e-9:
        return (1.0, 0.0)
    return (float(chord[0] / norm), float(chord[1] / norm))


def extract(
    dem: np.ndarray,
    transform,
    path_rc: np.ndarray,
    path_index: int,
    name: str,
    chainage_m: float,
    *,
    half_width_m: float = 2000.0,
    spacing_m: float = 30.0,
) -> CrossSection:
    """Extract one section perpendicular to the flow at `path_index`."""
    from rasterio.transform import rowcol, xy

    xs_path, ys_path = xy(transform, path_rc[:, 0], path_rc[:, 1])
    path_xy = np.column_stack([np.asarray(xs_path), np.asarray(ys_path)])

    dx, dy = _local_direction(path_xy, path_index)
    # Perpendicular: rotate the flow direction by 90 degrees.
    nx, ny = -dy, dx

    cx, cy = path_xy[path_index]
    offsets = np.arange(-half_width_m, half_width_m + spacing_m, spacing_m)
    sx = cx + offsets * nx
    sy = cy + offsets * ny

    bed = np.full(len(offsets), np.nan)
    rows, cols = dem.shape
    for i, (x, y) in enumerate(zip(sx, sy)):
        r, c = rowcol(transform, x, y)
        if 0 <= r < rows and 0 <= c < cols:
            bed[i] = dem[r, c]

    return CrossSection(
        name=name,
        path_index=path_index,
        chainage_m=chainage_m,
        offsets_m=offsets,
        bed_m=bed,
        xs=sx,
        ys=sy,
        normal=(nx, ny),
    )


def nearest_path_index(
    path_rc: np.ndarray,
    transform,
    x: float,
    y: float,
) -> tuple[int, float]:
    """Index of the path point closest to a world coordinate, and that distance.

    Used to place a named town's section on the routed channel. The returned
    distance matters: a town 8 km from the traced path is not on this river, and
    the caller should say so rather than drawing a section through a hillside.
    """
    from rasterio.transform import xy

    xs, ys = xy(transform, path_rc[:, 0], path_rc[:, 1])
    d2 = (np.asarray(xs) - x) ** 2 + (np.asarray(ys) - y) ** 2
    i = int(np.argmin(d2))
    return i, float(np.sqrt(d2[i]))


def chainages(path_rc: np.ndarray, cell_size_m: float) -> np.ndarray:
    """Cumulative distance along the path, m, accounting for diagonal steps."""
    if len(path_rc) < 2:
        return np.zeros(len(path_rc))
    steps = np.diff(path_rc.astype(np.float64), axis=0)
    seg = np.hypot(steps[:, 0], steps[:, 1]) * cell_size_m
    return np.concatenate([[0.0], np.cumsum(seg)])


def extract_all(
    dem: np.ndarray,
    transform,
    path_rc: np.ndarray,
    cell_size_m: float,
    towns: list,
    *,
    interval_km: float = 10.0,
    half_width_m: float = 2000.0,
    max_town_offset_m: float = 15000.0,
    crs: str = "EPSG:4326",
) -> tuple[list[CrossSection], list[str]]:
    """Sections at every named town plus regular chainages.

    Returns (sections, warnings). A town further than `max_town_offset_m` from
    the traced path gets a warning and no section, because a cross-section
    through terrain the river does not reach would be actively misleading.
    """
    from pyproj import Transformer

    warnings: list[str] = []
    ch = chainages(path_rc, cell_size_m)
    sections: list[CrossSection] = []

    to_grid = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    for town in towns:
        x, y = to_grid.transform(town.lon, town.lat)
        idx, offset = nearest_path_index(path_rc, transform, x, y)
        if offset > max_town_offset_m:
            warnings.append(
                f"{town.name} is {offset / 1000:.1f} km from the traced flow path "
                f"(limit {max_town_offset_m / 1000:.0f} km): no cross-section extracted. "
                f"Either the town is not on this reach, or the trace left the river."
            )
            continue
        section = extract(
            dem, transform, path_rc, idx, town.name, float(ch[idx]), half_width_m=half_width_m
        )
        section.offset_from_path_m = offset
        if offset > 2000.0:
            warnings.append(
                f"{town.name} is {offset / 1000:.1f} km from the traced channel. Its "
                f"cross-section is taken on the channel, not at the town, so depths "
                f"reported there are valley depths rather than depths in the town itself."
            )
        sections.append(section)

    interval_m = interval_km * 1000.0
    total = float(ch[-1]) if len(ch) else 0.0
    for target in np.arange(interval_m, total, interval_m):
        idx = int(np.argmin(np.abs(ch - target)))
        sections.append(
            extract(
                dem,
                transform,
                path_rc,
                idx,
                f"CH {ch[idx] / 1000:.0f} km",
                float(ch[idx]),
                half_width_m=half_width_m,
            )
        )

    sections.sort(key=lambda s: s.chainage_m)
    return sections, warnings
