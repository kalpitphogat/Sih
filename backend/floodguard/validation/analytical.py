"""Analytical dam-break solutions used to verify the solver.

Verification asks a narrow, answerable question: does the code solve the
equations it claims to solve? These closed-form solutions are the only way to
answer it, because they are exact. Comparing one numerical model against
another only establishes that two codes agree, which is weaker.

Solutions
---------
**Ritter (1892)** — instantaneous removal of a dam on a dry, horizontal,
frictionless bed. The wave is a centred rarefaction:

    h(x, t) = (1/9g) * (2*sqrt(g*h0) - (x-x0)/t)^2
    u(x, t) = (2/3) * ((x-x0)/t + sqrt(g*h0))

valid between the upstream characteristic x = x0 - t*sqrt(g*h0) and the front
x = x0 + 2*t*sqrt(g*h0). Two exact checks fall out: the front advances at
2*sqrt(g*h0), and the depth at the dam site is exactly 4/9 * h0 for all t > 0.

**Stoker (1957)** — dam break onto a *wet* bed of depth h1 < h0. A rarefaction
runs upstream and a shock runs downstream. The intermediate state (h2, u2)
solves a nonlinear equation matching the rarefaction's Riemann invariant to the
shock's Rankine-Hugoniot condition; we solve it with Brent's method.

**Dressler (1952) / Whitham (1955)** — frictional correction to the Ritter
front. Ritter's frictionless tip is unphysically fast over real terrain; the
correction gives the leading-edge celerity a finite, resistance-dependent value.

References
----------
Ritter, A. (1892). "Die Fortpflanzung der Wasserwellen." *Zeitschrift des
    Vereines Deutscher Ingenieure* 36(33), 947-954.
Stoker, J.J. (1957). *Water Waves: The Mathematical Theory with Applications.*
Dressler, R.F. (1952). "Hydraulic resistance effect upon the dam-break
    functions." *Journal of Research of the National Bureau of Standards* 49(3).
Whitham, G.B. (1955). "The effects of hydraulic resistance in the dam-break
    problem." *Proc. Royal Society A* 227, 399-407.
"""

from __future__ import annotations

import numpy as np

G = 9.81


def ritter(x: np.ndarray, t: float, h0: float, x0: float = 0.0):
    """Ritter (1892) dry-bed dam break. Returns (h, u).

    At t = 0 the solution is the initial discontinuity; for t > 0 it is the
    centred rarefaction fan.
    """
    x = np.asarray(x, dtype=float)
    if t <= 0:
        return np.where(x < x0, h0, 0.0), np.zeros_like(x)

    c0 = np.sqrt(G * h0)
    xi = (x - x0) / t

    h = np.empty_like(x)
    u = np.empty_like(x)

    upstream = xi <= -c0            # undisturbed reservoir
    fan = (xi > -c0) & (xi < 2 * c0)
    downstream = xi >= 2 * c0       # dry bed ahead of the front

    h[upstream] = h0
    u[upstream] = 0.0

    h[fan] = (1.0 / (9.0 * G)) * (2.0 * c0 - xi[fan]) ** 2
    u[fan] = (2.0 / 3.0) * (xi[fan] + c0)

    h[downstream] = 0.0
    u[downstream] = 0.0

    return h, u


def ritter_front_position(t: float, h0: float, x0: float = 0.0) -> float:
    """Position of the Ritter front: x0 + 2*t*sqrt(g*h0)."""
    return x0 + 2.0 * t * np.sqrt(G * h0)


def ritter_depth_at_dam(h0: float) -> float:
    """Ritter's exact result: h(x0, t) = 4/9 * h0 for every t > 0.

    A sharp, parameter-free check. Any scheme that gets this wrong has a
    problem in its rarefaction handling, not merely in its resolution.
    """
    return 4.0 / 9.0 * h0


def _stoker_intermediate(h0: float, h1: float) -> tuple[float, float]:
    """Solve for the intermediate depth h2 and velocity u2 in Stoker's solution.

    Matching the rarefaction invariant to the shock jump condition gives

        2*(sqrt(g*h0) - sqrt(g*h2)) = (h2 - h1) * sqrt(g/2 * (1/h2 + 1/h1))

    which is monotone in h2 on (h1, h0), so Brent's method is safe.
    """
    from scipy.optimize import brentq

    def residual(h2: float) -> float:
        left = 2.0 * (np.sqrt(G * h0) - np.sqrt(G * h2))
        right = (h2 - h1) * np.sqrt(0.5 * G * (1.0 / h2 + 1.0 / h1))
        return left - right

    h2 = brentq(residual, h1 * (1.0 + 1e-12), h0 * (1.0 - 1e-12), xtol=1e-14, rtol=1e-14)
    u2 = 2.0 * (np.sqrt(G * h0) - np.sqrt(G * h2))
    return float(h2), float(u2)


def stoker_shock_speed(h0: float, h1: float) -> float:
    """Speed of the downstream shock in Stoker's wet-bed solution."""
    h2, u2 = _stoker_intermediate(h0, h1)
    return u2 * h2 / (h2 - h1)


def stoker(x: np.ndarray, t: float, h0: float, h1: float, x0: float = 0.0):
    """Stoker (1957) wet-bed dam break. Returns (h, u).

    Degenerates to Ritter when the downstream depth is zero.
    """
    x = np.asarray(x, dtype=float)
    if h1 <= 0:
        return ritter(x, t, h0, x0)
    if t <= 0:
        return np.where(x < x0, h0, h1), np.zeros_like(x)

    c0 = np.sqrt(G * h0)
    h2, u2 = _stoker_intermediate(h0, h1)
    c2 = np.sqrt(G * h2)
    shock_speed = u2 * h2 / (h2 - h1)

    xi = (x - x0) / t
    h = np.empty_like(x)
    u = np.empty_like(x)

    # Four regions, left to right: undisturbed reservoir, rarefaction fan,
    # constant intermediate state, undisturbed tailwater.
    reservoir = xi <= -c0
    fan = (xi > -c0) & (xi <= u2 - c2)
    star = (xi > u2 - c2) & (xi < shock_speed)
    tail = xi >= shock_speed

    h[reservoir] = h0
    u[reservoir] = 0.0

    h[fan] = (1.0 / (9.0 * G)) * (2.0 * c0 - xi[fan]) ** 2
    u[fan] = (2.0 / 3.0) * (xi[fan] + c0)

    h[star] = h2
    u[star] = u2

    h[tail] = h1
    u[tail] = 0.0

    return h, u


def dressler_front_celerity(h0: float, manning_n: float, distance_m: float) -> float:
    """Leading-edge celerity with hydraulic resistance, after Dressler/Whitham.

    Ritter's front travels at 2*sqrt(g*h0) forever, which over 100 km of real
    valley is badly too fast. Dressler's first-order correction reduces the tip
    celerity as resistance acts over the travelled distance.

    Implemented as the standard first-order correction

        c_front = 2*sqrt(g*h0) * (1 - alpha)
        alpha   = C_f * L / h0,  C_f = g * n^2 / h0^(1/3)

    clamped to a physically sensible floor. This is an approximation to an
    asymptotic result, used here as a sanity bound on the numerical front
    rather than as a precision benchmark, and it is labelled that way in the
    validation report.
    """
    if manning_n <= 0:
        return 2.0 * np.sqrt(G * h0)
    friction = G * manning_n**2 / h0 ** (1.0 / 3.0)
    alpha = min(friction * distance_m / max(h0, 1e-9), 0.9)
    return 2.0 * np.sqrt(G * h0) * (1.0 - alpha)


def error_norms(numerical: np.ndarray, analytical: np.ndarray, dx: float) -> dict[str, float]:
    """L1, L2 and L-infinity errors, plus RMSE and a relative L2.

    Norms are discretised with the cell width so they are grid-independent and
    comparable across a convergence study. Reporting only RMSE would hide the
    fact that the error concentrates entirely at the shock.
    """
    diff = np.asarray(numerical, dtype=float) - np.asarray(analytical, dtype=float)
    n = diff.size
    l1 = float(np.sum(np.abs(diff)) * dx)
    l2 = float(np.sqrt(np.sum(diff**2) * dx))
    linf = float(np.max(np.abs(diff)))
    rmse = float(np.sqrt(np.mean(diff**2)))
    denom = float(np.sqrt(np.sum(np.asarray(analytical, float) ** 2) * dx))
    return {
        "L1": l1,
        "L2": l2,
        "Linf": linf,
        "RMSE": rmse,
        "relative_L2": l2 / denom if denom > 0 else float("nan"),
        "n_cells": n,
    }
