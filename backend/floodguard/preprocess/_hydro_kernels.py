"""Numba-jitted kernels for the hydrological conditioning hot loops.

The pure-Python versions of priority-flood and flow accumulation take minutes on
a 3.5 million cell grid, which is too slow to iterate on and far too slow for an
interactive job. These are the same algorithms with the inner loops compiled.

`heapq` is not supported inside nopython mode, so the priority queue is a
hand-rolled binary min-heap over flat arrays. It is keyed on (elevation, insertion
order) so the pop order is deterministic — without the tiebreak, two cells at the
same elevation could be filled in either order and the result would not be
reproducible run to run, which would break provenance.

If numba is unavailable the module still imports and exposes the same functions
as plain Python, so the package never hard-depends on the JIT.
"""

from __future__ import annotations

import numpy as np

try:
    from numba import njit

    HAVE_NUMBA = True
except ImportError:  # pragma: no cover - exercised only on installs without numba
    HAVE_NUMBA = False

    def njit(*args, **kwargs):  # type: ignore[misc]
        """No-op decorator so the module works without numba, just slowly."""

        def wrap(fn):
            return fn

        if args and callable(args[0]):
            return args[0]
        return wrap


# D8 neighbour offsets as plain int64 arrays. Duplicated from hydro.py rather
# than imported to keep this module free of circular imports and to guarantee
# the dtype numba needs.
DR = np.array([0, 1, 1, 1, 0, -1, -1, -1], dtype=np.int64)
DC = np.array([1, 1, 0, -1, -1, -1, 0, 1], dtype=np.int64)
CODES = np.array([1, 2, 4, 8, 16, 32, 64, 128], dtype=np.uint8)
DIST = np.array(
    [1.0, 1.4142135623730951, 1.0, 1.4142135623730951, 1.0, 1.4142135623730951, 1.0, 1.4142135623730951]
)


@njit(cache=True)
def _heap_push(keys, orders, items, size, key, order, item):
    """Push onto a binary min-heap held in three parallel arrays."""
    i = size
    keys[i] = key
    orders[i] = order
    items[i] = item
    while i > 0:
        parent = (i - 1) >> 1
        if keys[parent] < keys[i] or (keys[parent] == keys[i] and orders[parent] <= orders[i]):
            break
        keys[parent], keys[i] = keys[i], keys[parent]
        orders[parent], orders[i] = orders[i], orders[parent]
        items[parent], items[i] = items[i], items[parent]
        i = parent
    return size + 1


@njit(cache=True)
def _heap_pop(keys, orders, items, size):
    """Pop the minimum. Returns (key, item, new_size)."""
    top_key = keys[0]
    top_item = items[0]
    size -= 1
    keys[0] = keys[size]
    orders[0] = orders[size]
    items[0] = items[size]

    i = 0
    while True:
        left = 2 * i + 1
        right = left + 1
        smallest = i
        if left < size and (
            keys[left] < keys[smallest]
            or (keys[left] == keys[smallest] and orders[left] < orders[smallest])
        ):
            smallest = left
        if right < size and (
            keys[right] < keys[smallest]
            or (keys[right] == keys[smallest] and orders[right] < orders[smallest])
        ):
            smallest = right
        if smallest == i:
            break
        keys[smallest], keys[i] = keys[i], keys[smallest]
        orders[smallest], orders[i] = orders[i], orders[smallest]
        items[smallest], items[i] = items[i], items[smallest]
        i = smallest

    return top_key, top_item, size


@njit(cache=True)
def priority_flood(dem, nodata_mask, epsilon):
    """Barnes, Lehman & Mulla (2014) priority-flood depression filling.

    Seeds from the array edge and from every cell adjacent to nodata, then
    floods inward, raising each cell to at least its predecessor's level plus
    `epsilon`. The epsilon gradient is what lets D8 drain a filled flat instead
    of stalling in the middle of it.
    """
    rows, cols = dem.shape
    filled = dem.copy()
    closed = nodata_mask.copy()

    capacity = rows * cols + 1
    keys = np.empty(capacity, dtype=np.float64)
    orders = np.empty(capacity, dtype=np.int64)
    items = np.empty(capacity, dtype=np.int64)
    size = 0
    counter = 0

    for r in range(rows):
        for c in range(cols):
            on_edge = r == 0 or c == 0 or r == rows - 1 or c == cols - 1
            if closed[r, c]:
                continue
            seed = on_edge
            if not seed:
                for k in range(8):
                    rr = r + DR[k]
                    cc = c + DC[k]
                    if 0 <= rr < rows and 0 <= cc < cols and nodata_mask[rr, cc]:
                        seed = True
                        break
            if seed:
                closed[r, c] = True
                size = _heap_push(
                    keys, orders, items, size, filled[r, c], counter, r * cols + c
                )
                counter += 1

    while size > 0:
        elev, idx, size = _heap_pop(keys, orders, items, size)
        r = idx // cols
        c = idx % cols
        for k in range(8):
            rr = r + DR[k]
            cc = c + DC[k]
            if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                continue
            if closed[rr, cc]:
                continue
            closed[rr, cc] = True
            if filled[rr, cc] <= elev:
                filled[rr, cc] = elev + epsilon
            size = _heap_push(
                keys, orders, items, size, filled[rr, cc], counter, rr * cols + cc
            )
            counter += 1

    return filled


@njit(cache=True)
def d8_direction(filled, nodata_mask):
    """Steepest-descent D8, distance-weighted so diagonals are not favoured."""
    rows, cols = filled.shape
    direction = np.zeros((rows, cols), dtype=np.uint8)

    for r in range(rows):
        for c in range(cols):
            if nodata_mask[r, c]:
                continue
            centre = filled[r, c]
            best_slope = 0.0
            best_code = np.uint8(0)
            for k in range(8):
                rr = r + DR[k]
                cc = c + DC[k]
                if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                    continue
                if nodata_mask[rr, cc]:
                    continue
                slope = (centre - filled[rr, cc]) / DIST[k]
                if slope > best_slope:
                    best_slope = slope
                    best_code = CODES[k]
            direction[r, c] = best_code

    return direction


@njit(cache=True)
def accumulate(direction, order, nodata_mask):
    """Flow accumulation by descending-elevation traversal.

    `order` is a flat index array pre-sorted by descending filled elevation.
    Because D8 always points downhill, that order guarantees each cell's own
    total is final before it contributes downstream — an exact topological sort.
    """
    rows, cols = direction.shape
    accum = np.ones((rows, cols), dtype=np.float64)
    for r in range(rows):
        for c in range(cols):
            if nodata_mask[r, c]:
                accum[r, c] = 0.0

    for n in range(order.shape[0]):
        idx = order[n]
        r = idx // cols
        c = idx % cols
        code = direction[r, c]
        if code == 0:
            continue
        for k in range(8):
            if CODES[k] == code:
                rr = r + DR[k]
                cc = c + DC[k]
                if 0 <= rr < rows and 0 <= cc < cols:
                    accum[rr, cc] += accum[r, c]
                break

    return accum


@njit(cache=True)
def trace(direction, start_row, start_col, cell_size_m, max_length_m):
    """Follow D8 downstream from a start cell.

    Returns (path array of shape (n, 2), length in metres). Guards against
    cycles with a visited mask: on a correctly filled DEM D8 cannot cycle, but
    an infinite loop during a live demo is worse than a truncated path.
    """
    rows, cols = direction.shape
    visited = np.zeros((rows, cols), dtype=np.bool_)

    max_steps = int(max_length_m / cell_size_m) + 2
    path = np.empty((max_steps + 1, 2), dtype=np.int64)
    path[0, 0] = start_row
    path[0, 1] = start_col
    visited[start_row, start_col] = True
    count = 1
    length = 0.0

    r = start_row
    c = start_col
    while length < max_length_m and count <= max_steps:
        code = direction[r, c]
        if code == 0:
            break
        moved = False
        for k in range(8):
            if CODES[k] == code:
                rr = r + DR[k]
                cc = c + DC[k]
                if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                    break
                if visited[rr, cc]:
                    break
                length += DIST[k] * cell_size_m
                path[count, 0] = rr
                path[count, 1] = cc
                visited[rr, cc] = True
                count += 1
                r = rr
                c = cc
                moved = True
                break
        if not moved:
            break

    return path[:count], length


@njit(cache=True)
def watershed(direction, outlet_row, outlet_col):
    """Cells draining through an outlet, by reverse D8 traversal.

    This is the hydrological definition of a reservoir's catchment, and it is
    what makes pool delineation correct. Thresholding `dem < FRL` and
    flood-filling from the dam instead lets water leak *past* the dam into the
    downstream valley, which on a steep reach inflates the derived storage by
    orders of magnitude. Restricting the fill to this mask makes that
    impossible by construction.

    Walks upstream with an explicit stack: a neighbour belongs to the watershed
    if its D8 pointer points at a cell already in the watershed.
    """
    rows, cols = direction.shape
    inside = np.zeros((rows, cols), dtype=np.bool_)
    inside[outlet_row, outlet_col] = True

    stack = np.empty(rows * cols, dtype=np.int64)
    stack[0] = outlet_row * cols + outlet_col
    top = 1

    while top > 0:
        top -= 1
        idx = stack[top]
        r = idx // cols
        c = idx % cols
        for k in range(8):
            rr = r + DR[k]
            cc = c + DC[k]
            if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                continue
            if inside[rr, cc]:
                continue
            # Does this neighbour flow INTO (r, c)? Its pointer must be the
            # reverse of the offset we just walked, i.e. index (k + 4) % 8.
            back = CODES[(k + 4) % 8]
            if direction[rr, cc] == back:
                inside[rr, cc] = True
                stack[top] = rr * cols + cc
                top += 1

    return inside
