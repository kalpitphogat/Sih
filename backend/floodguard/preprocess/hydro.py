"""DEM hydrological conditioning: pit filling, flow direction, accumulation, tracing.

Implemented directly rather than delegated to richdem/whitebox for three
reasons: the algorithms are short and standard, it removes a fragile binary
dependency from the critical path, and we need the intermediate D8 pointer grid
in a specific form for the downstream trace.

Algorithms used, with references:

* **Priority-flood depression filling** — Barnes, Lehman & Mulla (2014),
  *Priority-flood: An optimal depression-filling and watershed-labeling
  algorithm for digital elevation models*, Computers & Geosciences 62.
  O(n log n), handles flats and nested depressions correctly, and is what makes
  the flow-direction grid usable.
* **D8 flow direction** — O'Callaghan & Mark (1984). Steepest descent to one of
  eight neighbours, distance-weighted so diagonals are not unfairly favoured.
* **Flow accumulation** — topological ordering by descending filled elevation,
  which is exact and avoids the recursion depth problems of a naive traversal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from floodguard.preprocess import _hydro_kernels as kernels

log = logging.getLogger(__name__)

#: D8 neighbour offsets (row, col) and their codes. Index order is fixed and
#: shared by every function here: E, SE, S, SW, W, NW, N, NE.
D8_OFFSETS = np.array(
    [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)], dtype=np.int8
)
#: ESRI-convention D8 codes, so the pointer grid is readable in QGIS.
D8_CODES = np.array([1, 2, 4, 8, 16, 32, 64, 128], dtype=np.uint8)
#: Euclidean length of each step in cell units.
D8_DISTANCE = np.array(
    [1.0, np.sqrt(2), 1.0, np.sqrt(2), 1.0, np.sqrt(2), 1.0, np.sqrt(2)], dtype=np.float64
)

NO_FLOW = np.uint8(0)


def fill_depressions(dem: np.ndarray, nodata_mask: np.ndarray, epsilon: float = 1e-4) -> np.ndarray:
    """Priority-flood depression filling with an epsilon gradient.

    Barnes, Lehman & Mulla (2014). The epsilon gradient is what makes flats
    drainable: each filled cell is raised a hair above its predecessor, so a
    subsequent D8 pass always finds a downhill neighbour instead of stalling in
    the middle of a filled lake.

    The heavy loop lives in `_hydro_kernels.priority_flood`, which is
    numba-jitted. On a 3.5 Mcell grid that is the difference between ~2 minutes
    and a couple of seconds.

    Parameters
    ----------
    dem
        Elevation array. Not modified.
    nodata_mask
        True where the DEM has no data. These cells are outlets that seed the
        flood, alongside the array edge.
    epsilon
        Vertical increment applied across flats, in DEM units (metres).

    Returns
    -------
    The filled DEM. Cells that were already drainable keep their exact value.
    """
    return kernels.priority_flood(
        np.ascontiguousarray(dem, dtype=np.float64),
        np.ascontiguousarray(nodata_mask, dtype=np.bool_),
        float(epsilon),
    )


def burn_streams(
    dem: np.ndarray, stream_mask: np.ndarray, depth_m: float = 10.0
) -> np.ndarray:
    """Lower known stream cells to force flow along a mapped centreline.

    Stream burning is a blunt instrument: it guarantees the traced path follows
    the real river, at the cost of a channel that is artificially incised. We
    burn *before* filling and only when the scenario asks for it, and we record
    that we did, because a burned DEM must not be used for depth reporting.
    """
    out = np.array(dem, dtype=np.float64, copy=True)
    out[stream_mask] -= depth_m
    return out


def d8_flow_direction(filled: np.ndarray, nodata_mask: np.ndarray) -> np.ndarray:
    """D8 pointer grid from a depression-filled DEM.

    Slope is computed per unit *distance*, so a diagonal neighbour must be
    sqrt(2) times lower to win against a cardinal one. Skipping that weighting
    is a common bug that biases traced channels into diagonal staircases.
    """
    return kernels.d8_direction(
        np.ascontiguousarray(filled, dtype=np.float64),
        np.ascontiguousarray(nodata_mask, dtype=np.bool_),
    )


def flow_accumulation(
    direction: np.ndarray, filled: np.ndarray, nodata_mask: np.ndarray
) -> np.ndarray:
    """Number of upstream cells draining through each cell.

    Processed in order of descending filled elevation. Because D8 flow always
    goes from higher to lower (or equal-plus-epsilon), that order guarantees a
    cell's own accumulation is final before it is passed downstream — an exact
    topological sort obtained for the price of one argsort.
    """
    flat_elev = np.where(nodata_mask, -np.inf, filled).ravel()
    order = np.argsort(flat_elev)[::-1].astype(np.int64)
    return kernels.accumulate(
        np.ascontiguousarray(direction, dtype=np.uint8),
        np.ascontiguousarray(order),
        np.ascontiguousarray(nodata_mask, dtype=np.bool_),
    )


def snap_to_stream(
    accum: np.ndarray,
    row: int,
    col: int,
    search_radius_cells: int = 10,
) -> tuple[int, int, float]:
    """Move a point to the highest-accumulation cell nearby.

    Dam coordinates in the NRLD are single points of unstated convention at
    ~30 m precision, so the published lat/lon frequently lands on the abutment
    rather than in the channel. Releasing the flood from a hillside cell
    produces a confident, wrong answer, so snapping is mandatory, not optional.

    Returns (row, col, accumulation) of the snapped cell.
    """
    rows, cols = accum.shape
    r0 = max(0, row - search_radius_cells)
    r1 = min(rows, row + search_radius_cells + 1)
    c0 = max(0, col - search_radius_cells)
    c1 = min(cols, col + search_radius_cells + 1)

    window = accum[r0:r1, c0:c1]
    if window.size == 0:
        raise ValueError(f"snap window empty for ({row}, {col}); point outside the DEM?")

    local = np.unravel_index(int(np.argmax(window)), window.shape)
    return r0 + int(local[0]), c0 + int(local[1]), float(window[local])


def trace_downstream(
    direction: np.ndarray,
    start_row: int,
    start_col: int,
    cell_size_m: float,
    max_length_m: float,
) -> tuple[np.ndarray, float]:
    """Follow D8 from a start cell until the domain edge or a length limit.

    Returns (path as an (n, 2) array of (row, col), total length in metres).
    A cycle guard is included: on a correctly filled DEM D8 cannot cycle, but a
    partially conditioned one can, and an infinite loop during a demo is worse
    than a truncated path.
    """
    path, length = kernels.trace(
        np.ascontiguousarray(direction, dtype=np.uint8),
        int(start_row),
        int(start_col),
        float(cell_size_m),
        float(max_length_m),
    )
    path = np.asarray(path, dtype=np.int32)
    if length < max_length_m:
        log.info("downstream trace ended after %.1f km", length / 1000.0)
    return path, float(length)


def upstream_watershed(
    direction: np.ndarray, outlet_row: int, outlet_col: int
) -> np.ndarray:
    """Boolean mask of every cell draining through (outlet_row, outlet_col).

    Used to confine reservoir delineation to the dam's own catchment. Without
    it, a fill at FRL leaks past the dam and down the valley, because on a
    steep reach the downstream river bed sits hundreds of metres *below* the
    reservoir surface and is therefore "under" the fill level too.
    """
    return kernels.watershed(
        np.ascontiguousarray(direction, dtype=np.uint8), int(outlet_row), int(outlet_col)
    )


def corridor_mask(
    shape: tuple[int, int],
    path: np.ndarray,
    cell_size_m: float,
    buffer_m: float,
) -> np.ndarray:
    """Boolean mask of cells within `buffer_m` of the traced flow path.

    Uses an exact Euclidean distance transform from the path rather than a
    square dilation, so the corridor width is the stated width in every
    direction rather than sqrt(2) times wider on the diagonals.
    """
    from scipy import ndimage

    seeds = np.ones(shape, dtype=bool)
    seeds[path[:, 0], path[:, 1]] = False
    distance_cells = ndimage.distance_transform_edt(seeds)
    return distance_cells * cell_size_m <= buffer_m


@dataclass
class ConditionedDem:
    """Everything the downstream stages need from DEM conditioning."""

    raw: np.ndarray
    filled: np.ndarray
    direction: np.ndarray
    accumulation: np.ndarray
    nodata_mask: np.ndarray
    cell_size_m: float
    transform: object
    crs: str
    stream_burned: bool = False

    @property
    def fill_depth(self) -> np.ndarray:
        """How much each cell was raised. Large values flag DEM problems."""
        return np.where(self.nodata_mask, 0.0, self.filled - self.raw)

    def stats(self) -> dict[str, float]:
        depth = self.fill_depth
        return {
            "cells": int(self.raw.size),
            "nodata_cells": int(self.nodata_mask.sum()),
            "cells_filled": int((depth > 1e-3).sum()),
            "max_fill_m": float(depth.max()),
            "mean_fill_m": float(depth[depth > 1e-3].mean()) if (depth > 1e-3).any() else 0.0,
            "max_accumulation_cells": float(self.accumulation.max()),
        }


def condition(
    dem: np.ndarray,
    nodata_mask: np.ndarray,
    cell_size_m: float,
    transform,
    crs: str,
    *,
    stream_mask: np.ndarray | None = None,
    burn_depth_m: float = 10.0,
) -> ConditionedDem:
    """Full conditioning pass: optional burn, fill, D8, accumulation."""
    working = dem.astype(np.float64)
    burned = False
    if stream_mask is not None:
        working = burn_streams(working, stream_mask, burn_depth_m)
        burned = True
        log.info("burned %d stream cells by %.1f m", int(stream_mask.sum()), burn_depth_m)

    log.info("filling depressions over %d cells…", working.size)
    filled = fill_depressions(working, nodata_mask)

    log.info("computing D8 flow direction…")
    direction = d8_flow_direction(filled, nodata_mask)

    log.info("computing flow accumulation…")
    accum = flow_accumulation(direction, filled, nodata_mask)

    return ConditionedDem(
        raw=dem.astype(np.float64),
        filled=filled,
        direction=direction,
        accumulation=accum,
        nodata_mask=nodata_mask,
        cell_size_m=cell_size_m,
        transform=transform,
        crs=crs,
        stream_burned=burned,
    )
