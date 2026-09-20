"""Numba-jitted kernels for the 2D shallow-water finite-volume solver.

The scheme is a second-order MUSCL-Hancock Godunov method with an HLLC
approximate Riemann solver and hydrostatic reconstruction for well-balancing.
Every piece of that sentence is load-bearing:

**HLLC** (Toro, *Riemann Solvers and Numerical Methods for Fluid Dynamics*,
3rd ed., ch. 10) resolves the contact/shear wave that plain HLL smears. In a
dam break the front IS a shock, and a scheme that smears it mistimes arrival —
the number a district disaster officer actually acts on.

**MUSCL with a minmod limiter** gives second-order accuracy in smooth regions
while remaining TVD across the front. First order is stable but so diffusive
that the peak depth is materially under-predicted at 30 m resolution.

**Hydrostatic reconstruction** (Audusse et al. 2004, *SIAM J. Sci. Comput.*
25(6)) is what makes the scheme well-balanced: a lake at rest over arbitrary
bed topography must stay exactly at rest. Without it, spurious velocities of
order sqrt(g*dz) appear on every slope, and on a Himalayan DEM that is metres
per second of fictional flow.

**Wetting and drying** is handled with a depth tolerance plus positivity-
preserving depth reconstruction. Velocity is computed with a desingularisation
(Kurganov & Petrova 2007) so h -> 0 does not produce u -> infinity.

All kernels operate on flat C-contiguous float64 arrays and are free of Python
objects so numba can compile them in nopython mode with parallel ranges.
"""

from __future__ import annotations

import numpy as np

try:
    from numba import njit, prange

    HAVE_NUMBA = True
except ImportError:  # pragma: no cover
    HAVE_NUMBA = False
    prange = range

    def njit(*args, **kwargs):  # type: ignore[misc]
        def wrap(fn):
            return fn

        if args and callable(args[0]):
            return args[0]
        return wrap


G = 9.81


@njit(cache=True, inline="always")
def _velocity(h, discharge, dry_tol):
    """Desingularised velocity u = hu / h.

    Kurganov & Petrova (2007): dividing by h near the wet/dry front amplifies
    round-off into enormous fictional velocities, which then force the CFL
    timestep to zero and stall the run. Using
    u = 2*h*q / (h^2 + max(h, tol)^2) is smooth, equals q/h when h >> tol, and
    goes to zero as h -> 0.
    """
    if h <= dry_tol:
        return 0.0
    denom = h * h + max(h, dry_tol) ** 2
    return 2.0 * h * discharge / denom


@njit(cache=True, inline="always")
def _minmod(a, b):
    """Minmod limiter: the most diffusive second-order TVD choice.

    Chosen over van Leer or superbee deliberately. Sharper limiters produce
    better-looking fronts but can generate spurious oscillations at the wet/dry
    interface on real terrain, and an oscillation there means negative depth.
    """
    if a * b <= 0.0:
        return 0.0
    if abs(a) < abs(b):
        return a
    return b


@njit(cache=True)
def hllc_flux(hL, huL, hvL, hR, huR, hvR, dry_tol):
    """HLLC flux in the face-normal direction.

    `hu` is normal momentum, `hv` transverse. Returns (F_h, F_hu, F_hv).

    Wave speeds use Toro's two-rarefaction estimate for the star region, with
    the dry-bed speeds substituted when either side is dry — the wet side then
    sends a rarefaction into the dry bed at u +/- 2c, which is what lets the
    front advance at the correct Ritter speed rather than stalling.
    """
    wetL = hL > dry_tol
    wetR = hR > dry_tol

    if not wetL and not wetR:
        return 0.0, 0.0, 0.0

    uL = _velocity(hL, huL, dry_tol)
    vL = _velocity(hL, hvL, dry_tol)
    uR = _velocity(hR, huR, dry_tol)
    vR = _velocity(hR, hvR, dry_tol)

    cL = np.sqrt(G * hL) if wetL else 0.0
    cR = np.sqrt(G * hR) if wetR else 0.0

    if not wetL:
        # Dry on the left: the right state expands into it.
        sL = uR - 2.0 * cR
        sR = uR + cR
    elif not wetR:
        sL = uL - cL
        sR = uL + 2.0 * cL
    else:
        # Two-rarefaction estimate of the star depth, then a
        # Rankine-Hugoniot correction where the star state is a shock.
        h_star = (0.5 * (cL + cR) + 0.25 * (uL - uR)) ** 2 / G
        if h_star > hL:
            qL = np.sqrt(0.5 * ((h_star + hL) * h_star) / (hL * hL))
        else:
            qL = 1.0
        if h_star > hR:
            qR = np.sqrt(0.5 * ((h_star + hR) * h_star) / (hR * hR))
        else:
            qR = 1.0
        sL = uL - cL * qL
        sR = uR + cR * qR

    # Physical fluxes.
    FL0 = huL
    FL1 = huL * uL + 0.5 * G * hL * hL
    FR0 = huR
    FR1 = huR * uR + 0.5 * G * hR * hR

    if sL >= 0.0:
        return FL0, FL1, huL * vL if wetL else 0.0
    if sR <= 0.0:
        return FR0, FR1, huR * vR if wetR else 0.0

    denom = sR - sL
    if abs(denom) < 1e-12:
        return 0.0, 0.0, 0.0

    F0 = (sR * FL0 - sL * FR0 + sL * sR * (hR - hL)) / denom
    F1 = (sR * FL1 - sL * FR1 + sL * sR * (huR - huL)) / denom

    # Contact wave speed decides which side supplies transverse momentum.
    # This is the single difference between HLLC and HLL, and it is what
    # preserves shear layers along a valley wall.
    num_s = sL * hR * (uR - sR) - sR * hL * (uL - sL)
    den_s = hR * (uR - sR) - hL * (uL - sL)
    if abs(den_s) < 1e-12:
        s_star = 0.0
    else:
        s_star = num_s / den_s

    v_up = vL if s_star >= 0.0 else vR
    return F0, F1, F0 * v_up


@njit(cache=True, parallel=True)
def compute_slopes(q, slope_x, slope_y, active):
    """Minmod-limited central slopes of a cell-centred field.

    Slopes are zeroed next to an inactive cell so the reconstruction never
    reaches across the domain mask into meaningless memory.
    """
    rows, cols = q.shape
    for r in prange(rows):
        for c in range(cols):
            if not active[r, c]:
                slope_x[r, c] = 0.0
                slope_y[r, c] = 0.0
                continue
            if c == 0 or c == cols - 1 or not active[r, c - 1] or not active[r, c + 1]:
                slope_x[r, c] = 0.0
            else:
                slope_x[r, c] = _minmod(q[r, c] - q[r, c - 1], q[r, c + 1] - q[r, c])
            if r == 0 or r == rows - 1 or not active[r - 1, c] or not active[r + 1, c]:
                slope_y[r, c] = 0.0
            else:
                slope_y[r, c] = _minmod(q[r, c] - q[r - 1, c], q[r + 1, c] - q[r, c])


@njit(cache=True, parallel=True)
def zero_slopes_at_wet_dry(slope_x, slope_y, h, active, dry_tol):
    """Drop to first order in any cell that touches a dry neighbour.

    Second-order reconstruction near a wet/dry front extrapolates the interior
    velocity gradient into the dry cell, which pushes a thin film ahead of
    where the water physically is. Measured on the Ritter problem, that film
    put the numerical front 200 m AHEAD of the exact solution — a flood
    arriving earlier than physics allows, which is the worst direction for an
    error in this application to go.

    Reverting to first order at the front costs a little sharpness there and
    removes the overshoot entirely. It is the standard treatment (Brufau,
    Vazquez-Cendon & Garcia-Navarro 2004; Liang & Marche 2009).
    """
    rows, cols = h.shape
    for r in prange(rows):
        for c in range(cols):
            if not active[r, c]:
                continue
            touches_dry = False
            for dr in range(-1, 2):
                for dc in range(-1, 2):
                    rr = r + dr
                    cc = c + dc
                    if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                        continue
                    if not active[rr, cc] or h[rr, cc] <= dry_tol:
                        touches_dry = True
            if touches_dry:
                slope_x[r, c] = 0.0
                slope_y[r, c] = 0.0


@njit(cache=True)
def max_wave_speed(h, hu, hv, active, dry_tol):
    """Largest |u| + sqrt(g*h) anywhere, for the CFL timestep."""
    rows, cols = h.shape
    fastest = 0.0
    for r in range(rows):
        for c in range(cols):
            if not active[r, c] or h[r, c] <= dry_tol:
                continue
            u = _velocity(h[r, c], hu[r, c], dry_tol)
            v = _velocity(h[r, c], hv[r, c], dry_tol)
            speed = np.sqrt(u * u + v * v) + np.sqrt(G * h[r, c])
            if speed > fastest:
                fastest = speed
    return fastest


@njit(cache=True, parallel=True)
def primitives(h, hu, hv, z, eta, u, v, active, dry_tol):
    """Convert conserved variables to the primitives we reconstruct.

    MUSCL must be applied to (eta, u, v), NOT to (h, hu, hv). Two reasons, both
    of which we hit as real failures before this function existed:

    1. **Reconstructing discharge blows up at the wet/dry front.** A limited
       slope can hand a face a tiny depth beside a not-so-tiny momentum, and
       hu/h then produces velocities of order 10^4 m/s. That drives the CFL
       timestep to zero and the run dies within ~50 steps, at every resolution
       identically. Reconstructing velocity directly makes the pairing
       consistent by construction.

    2. **Reconstructing depth destroys well-balancing.** Over sloping terrain a
       lake at rest has constant eta = h + z but strongly varying h. Limiting h
       therefore produces non-zero slopes for water that is not moving, and the
       hydrostatic reconstruction can no longer cancel the bed source term.
       Reconstructing eta keeps the slope exactly zero for still water.
    """
    rows, cols = h.shape
    for r in prange(rows):
        for c in range(cols):
            if not active[r, c]:
                eta[r, c] = 0.0
                u[r, c] = 0.0
                v[r, c] = 0.0
                continue
            eta[r, c] = h[r, c] + z[r, c]
            u[r, c] = _velocity(h[r, c], hu[r, c], dry_tol)
            v[r, c] = _velocity(h[r, c], hv[r, c], dry_tol)


@njit(cache=True, parallel=True)
def flux_sweep(
    h, hu, hv, z,
    eta, u, v,
    s_eta_x, s_u_x, s_v_x, s_z_x,
    s_eta_y, s_u_y, s_v_y, s_z_y,
    dh, dhu, dhv,
    fy0, fy1, fy2, fy3,
    active, dx, dy, dry_tol, second_order,
):
    """One full flux sweep: accumulate -div(F) + bed source into dh/dhu/dhv.

    Second-order well-balanced scheme of Audusse et al. (2004). At each face:

        z_face = max(z_L_interface, z_R_interface)
        h*_L   = max(0, eta_L - z_face)
        h*_R   = max(0, eta_R - z_face)

    The Riemann problem is solved on the starred states. The bed is then
    accounted for in two pieces:

    * an **interface** term g/2 * (h_int^2 - h*^2) added to the momentum flux,
      which corrects for the depth having been cut down to the face elevation;
    * a **centred** term -g * h_bar * (z_right_int - z_left_int) / dx, which
      accounts for the bed slope across the interior of the cell.

    Together these cancel the pressure flux exactly when eta is constant, which
    is what makes still water stay still. Dropping the centred term leaves a
    scheme that is well-balanced only at first order.
    """
    rows, cols = h.shape

    for r in prange(rows):
        for c in range(cols):
            dh[r, c] = 0.0
            dhu[r, c] = 0.0
            dhv[r, c] = 0.0

    # --- x-direction faces (between c and c+1) ---
    for r in prange(rows):
        for c in range(cols - 1):
            if not active[r, c] or not active[r, c + 1]:
                continue

            if second_order:
                etaL = eta[r, c] + 0.5 * s_eta_x[r, c]
                uL = u[r, c] + 0.5 * s_u_x[r, c]
                vL = v[r, c] + 0.5 * s_v_x[r, c]
                zLi = z[r, c] + 0.5 * s_z_x[r, c]

                etaR = eta[r, c + 1] - 0.5 * s_eta_x[r, c + 1]
                uR = u[r, c + 1] - 0.5 * s_u_x[r, c + 1]
                vR = v[r, c + 1] - 0.5 * s_v_x[r, c + 1]
                zRi = z[r, c + 1] - 0.5 * s_z_x[r, c + 1]
            else:
                etaL = eta[r, c]
                uL = u[r, c]
                vL = v[r, c]
                zLi = z[r, c]
                etaR = eta[r, c + 1]
                uR = u[r, c + 1]
                vR = v[r, c + 1]
                zRi = z[r, c + 1]

            hLi = etaL - zLi
            if hLi < 0.0:
                hLi = 0.0
            hRi = etaR - zRi
            if hRi < 0.0:
                hRi = 0.0

            z_face = zLi if zLi > zRi else zRi

            hLs = etaL - z_face
            if hLs < 0.0:
                hLs = 0.0
            hRs = etaR - z_face
            if hRs < 0.0:
                hRs = 0.0

            F0, F1, F2 = hllc_flux(
                hLs, hLs * uL, hLs * vL, hRs, hRs * uR, hRs * vR, dry_tol
            )

            # Interface correction: the pressure the starred depth does not carry.
            srcL = 0.5 * G * (hLi * hLi - hLs * hLs)
            srcR = 0.5 * G * (hRi * hRi - hRs * hRs)

            inv_dx = 1.0 / dx
            dh[r, c] -= F0 * inv_dx
            dhu[r, c] -= (F1 + srcL) * inv_dx
            dhv[r, c] -= F2 * inv_dx

            dh[r, c + 1] += F0 * inv_dx
            dhu[r, c + 1] += (F1 + srcR) * inv_dx
            dhv[r, c + 1] += F2 * inv_dx

    # --- x-direction centred bed-slope source ---
    if second_order:
        for r in prange(rows):
            for c in range(cols):
                if not active[r, c]:
                    continue
                zp = z[r, c] + 0.5 * s_z_x[r, c]
                zm = z[r, c] - 0.5 * s_z_x[r, c]
                hp = eta[r, c] + 0.5 * s_eta_x[r, c] - zp
                hm = eta[r, c] - 0.5 * s_eta_x[r, c] - zm
                if hp < 0.0:
                    hp = 0.0
                if hm < 0.0:
                    hm = 0.0
                dhu[r, c] -= G * 0.5 * (hp + hm) * (zp - zm) / dx

    # --- y-direction faces (between r and r+1) ---
    #
    # Written as two row-major passes through a temporary face-flux array
    # rather than one loop over columns. The obvious formulation,
    #     for c in prange(cols): for r in range(rows - 1): ...
    # parallelises over columns and therefore walks a C-contiguous array down
    # a column, striding a full row (tens of kilobytes) on every access. On a
    # 1 Mcell grid that single choice accounted for roughly 30 ms of a 41 ms
    # sweep. Both passes below run along rows, in memory order, and neither
    # has a cross-thread write conflict.
    #
    # Roles swap in this direction: v is the normal component, u the transverse.
    for r in prange(rows - 1):
        for c in range(cols):
            fy0[r, c] = 0.0
            fy1[r, c] = 0.0
            fy2[r, c] = 0.0
            if not active[r, c] or not active[r + 1, c]:
                continue

            if second_order:
                etaB = eta[r, c] + 0.5 * s_eta_y[r, c]
                uB = u[r, c] + 0.5 * s_u_y[r, c]
                vB = v[r, c] + 0.5 * s_v_y[r, c]
                zBi = z[r, c] + 0.5 * s_z_y[r, c]

                etaT = eta[r + 1, c] - 0.5 * s_eta_y[r + 1, c]
                uT = u[r + 1, c] - 0.5 * s_u_y[r + 1, c]
                vT = v[r + 1, c] - 0.5 * s_v_y[r + 1, c]
                zTi = z[r + 1, c] - 0.5 * s_z_y[r + 1, c]
            else:
                etaB = eta[r, c]
                uB = u[r, c]
                vB = v[r, c]
                zBi = z[r, c]
                etaT = eta[r + 1, c]
                uT = u[r + 1, c]
                vT = v[r + 1, c]
                zTi = z[r + 1, c]

            hBi = etaB - zBi
            if hBi < 0.0:
                hBi = 0.0
            hTi = etaT - zTi
            if hTi < 0.0:
                hTi = 0.0

            z_face = zBi if zBi > zTi else zTi

            hBs = etaB - z_face
            if hBs < 0.0:
                hBs = 0.0
            hTs = etaT - z_face
            if hTs < 0.0:
                hTs = 0.0

            F0, F1, F2 = hllc_flux(
                hBs, hBs * vB, hBs * uB, hTs, hTs * vT, hTs * uT, dry_tol
            )

            # Store the flux together with each side's interface correction, so
            # the accumulation pass needs no state from this one.
            fy0[r, c] = F0
            fy1[r, c] = F1 + 0.5 * G * (hBi * hBi - hBs * hBs)
            fy2[r, c] = F2
            fy3[r, c] = F1 + 0.5 * G * (hTi * hTi - hTs * hTs)

    inv_dy = 1.0 / dy
    for r in prange(rows):
        for c in range(cols):
            if not active[r, c]:
                continue
            if r < rows - 1:
                dh[r, c] -= fy0[r, c] * inv_dy
                dhv[r, c] -= fy1[r, c] * inv_dy
                dhu[r, c] -= fy2[r, c] * inv_dy
            if r > 0:
                dh[r, c] += fy0[r - 1, c] * inv_dy
                dhv[r, c] += fy3[r - 1, c] * inv_dy
                dhu[r, c] += fy2[r - 1, c] * inv_dy

    # --- y-direction centred bed-slope source ---
    if second_order:
        for r in prange(rows):
            for c in range(cols):
                if not active[r, c]:
                    continue
                zp = z[r, c] + 0.5 * s_z_y[r, c]
                zm = z[r, c] - 0.5 * s_z_y[r, c]
                hp = eta[r, c] + 0.5 * s_eta_y[r, c] - zp
                hm = eta[r, c] - 0.5 * s_eta_y[r, c] - zm
                if hp < 0.0:
                    hp = 0.0
                if hm < 0.0:
                    hm = 0.0
                dhv[r, c] -= G * 0.5 * (hp + hm) * (zp - zm) * inv_dy


@njit(cache=True, parallel=True)
def boundary_fluxes(
    h, hu, hv, z, eta, u, v,
    s_eta_x, s_u_x, s_v_x, s_z_x,
    s_eta_y, s_u_y, s_v_y, s_z_y,
    dh, dhu, dhv,
    active, dx, dy, dry_tol, second_order, open_edges,
):
    """Apply the missing flux at every face that has no active neighbour.

    Without this the scheme is simply wrong at its edges, and not subtly so.
    An interior cell receives a hydrostatic pressure flux from BOTH of its
    faces in each direction, and the two cancel for still water. A cell at the
    domain boundary receives only one, so it feels a one-sided push of
    0.5*g*h^2 per unit width with nothing to balance it. On a 10 m deep
    reservoir that is roughly 500 m^3/s^2 of spurious momentum injected every
    step, which contaminates the interior within a few dozen cells and destroys
    the solution. This was the defect behind a 94% error on the Ritter depth
    and a blow-up to 1e93 in the closed-basin mass test.

    Two boundary types:

    * **Wall** (an inactive neighbour inside the raster, i.e. the corridor mask
      edge or a nodata cell). The ghost state mirrors the interior with the
      normal velocity reversed, which reduces to the analytic wall flux
      [0, g*h^2/2, 0]. Nothing crosses, so mass is conserved exactly.
    * **Open** (the outer edge of the raster, when `open_edges` is true). The
      ghost state is a zero-gradient copy of the interior, so the flux is the
      cell's own physical flux and water leaves the domain freely. That is what
      makes the downstream end of a routing corridor behave like a river
      continuing past the edge, rather than a dam.

    Mass leaving through an open edge is legitimate, which is why the solver
    reports the resulting mass deficit as a diagnostic rather than an error.
    """
    rows, cols = h.shape

    for r in prange(rows):
        for c in range(cols):
            if not active[r, c]:
                continue

            if second_order:
                e_xp = eta[r, c] + 0.5 * s_eta_x[r, c]
                e_xm = eta[r, c] - 0.5 * s_eta_x[r, c]
                z_xp = z[r, c] + 0.5 * s_z_x[r, c]
                z_xm = z[r, c] - 0.5 * s_z_x[r, c]
                u_xp = u[r, c] + 0.5 * s_u_x[r, c]
                u_xm = u[r, c] - 0.5 * s_u_x[r, c]
                v_xp = v[r, c] + 0.5 * s_v_x[r, c]
                v_xm = v[r, c] - 0.5 * s_v_x[r, c]

                e_yp = eta[r, c] + 0.5 * s_eta_y[r, c]
                e_ym = eta[r, c] - 0.5 * s_eta_y[r, c]
                z_yp = z[r, c] + 0.5 * s_z_y[r, c]
                z_ym = z[r, c] - 0.5 * s_z_y[r, c]
                u_yp = u[r, c] + 0.5 * s_u_y[r, c]
                u_ym = u[r, c] - 0.5 * s_u_y[r, c]
                v_yp = v[r, c] + 0.5 * s_v_y[r, c]
                v_ym = v[r, c] - 0.5 * s_v_y[r, c]
            else:
                e_xp = e_xm = e_yp = e_ym = eta[r, c]
                z_xp = z_xm = z_yp = z_ym = z[r, c]
                u_xp = u_xm = u_yp = u_ym = u[r, c]
                v_xp = v_xm = v_yp = v_ym = v[r, c]

            # --- +x face ---
            missing = c == cols - 1 or not active[r, c + 1]
            if missing:
                hi = e_xp - z_xp
                if hi < 0.0:
                    hi = 0.0
                is_open = open_edges and c == cols - 1
                if is_open:
                    F0 = hi * u_xp
                    F1 = hi * u_xp * u_xp + 0.5 * G * hi * hi
                    F2 = hi * u_xp * v_xp
                else:
                    F0 = 0.0
                    F1 = 0.5 * G * hi * hi
                    F2 = 0.0
                dh[r, c] -= F0 / dx
                dhu[r, c] -= F1 / dx
                dhv[r, c] -= F2 / dx

            # --- -x face ---
            missing = c == 0 or not active[r, c - 1]
            if missing:
                hi = e_xm - z_xm
                if hi < 0.0:
                    hi = 0.0
                is_open = open_edges and c == 0
                if is_open:
                    F0 = hi * u_xm
                    F1 = hi * u_xm * u_xm + 0.5 * G * hi * hi
                    F2 = hi * u_xm * v_xm
                else:
                    F0 = 0.0
                    F1 = 0.5 * G * hi * hi
                    F2 = 0.0
                dh[r, c] += F0 / dx
                dhu[r, c] += F1 / dx
                dhv[r, c] += F2 / dx

            # --- +y face ---
            missing = r == rows - 1 or not active[r + 1, c]
            if missing:
                hi = e_yp - z_yp
                if hi < 0.0:
                    hi = 0.0
                is_open = open_edges and r == rows - 1
                if is_open:
                    F0 = hi * v_yp
                    F1 = hi * v_yp * v_yp + 0.5 * G * hi * hi
                    F2 = hi * v_yp * u_yp
                else:
                    F0 = 0.0
                    F1 = 0.5 * G * hi * hi
                    F2 = 0.0
                dh[r, c] -= F0 / dy
                dhv[r, c] -= F1 / dy
                dhu[r, c] -= F2 / dy

            # --- -y face ---
            missing = r == 0 or not active[r - 1, c]
            if missing:
                hi = e_ym - z_ym
                if hi < 0.0:
                    hi = 0.0
                is_open = open_edges and r == 0
                if is_open:
                    F0 = hi * v_ym
                    F1 = hi * v_ym * v_ym + 0.5 * G * hi * hi
                    F2 = hi * v_ym * u_ym
                else:
                    F0 = 0.0
                    F1 = 0.5 * G * hi * hi
                    F2 = 0.0
                dh[r, c] += F0 / dy
                dhv[r, c] += F1 / dy
                dhu[r, c] += F2 / dy


@njit(cache=True, parallel=True)
def reconstruct(
    eta, u, v, h, z,
    s_eta_x, s_eta_y, s_u_x, s_u_y, s_v_x, s_v_y, s_z_x, s_z_y,
    s_z_x_base, s_z_y_base,
    active, dry_tol,
):
    """All slope limiting for one stage, in a single pass over memory.

    Replaces three `compute_slopes` calls, four `zero_slopes_at_wet_dry` calls
    and two array copies — ten separate traversals of multi-megabyte arrays —
    with one. On a grid large enough to miss cache, the traversals themselves
    cost more than the arithmetic in them.

    The wet/dry test is done once per cell and applied to all four fields,
    which is also what keeps the bed slope limited on exactly the same cells as
    the water-surface slope. Limiting them inconsistently manufactures water
    out of the terrain gradient (see swe_fv._tendencies).
    """
    rows, cols = h.shape
    for r in prange(rows):
        for c in range(cols):
            if not active[r, c]:
                s_eta_x[r, c] = 0.0
                s_eta_y[r, c] = 0.0
                s_u_x[r, c] = 0.0
                s_u_y[r, c] = 0.0
                s_v_x[r, c] = 0.0
                s_v_y[r, c] = 0.0
                s_z_x[r, c] = 0.0
                s_z_y[r, c] = 0.0
                continue

            # Two distinct conditions, kept distinct on purpose.
            #
            # `near_dry` is a 3x3 test: a cell adjacent to dry ground drops to
            # first order in BOTH directions, because second-order
            # reconstruction there pushes a film ahead of the physical wave.
            #
            # `edge_x` / `edge_y` are per-direction: a central slope simply
            # cannot be formed without both neighbours. Collapsing these two
            # into one condition — treating an out-of-bounds neighbour as
            # "dry" — makes a cell at the domain edge first order in the
            # direction the flow is actually moving. The resulting mismatch
            # across the edge face leaks momentum into the interior and cost
            # 1.0 percentage point of Ritter L2 error and a third of the
            # observed convergence order when it was briefly present.
            near_dry = False
            for dr in range(-1, 2):
                rr = r + dr
                if rr < 0 or rr >= rows:
                    continue
                for dc in range(-1, 2):
                    cc = c + dc
                    if cc < 0 or cc >= cols:
                        continue
                    if not active[rr, cc] or h[rr, cc] <= dry_tol:
                        near_dry = True

            if near_dry:
                s_eta_x[r, c] = 0.0
                s_eta_y[r, c] = 0.0
                s_u_x[r, c] = 0.0
                s_u_y[r, c] = 0.0
                s_v_x[r, c] = 0.0
                s_v_y[r, c] = 0.0
                s_z_x[r, c] = 0.0
                s_z_y[r, c] = 0.0
                continue

            edge_x = c == 0 or c == cols - 1 or not active[r, c - 1] or not active[r, c + 1]
            edge_y = r == 0 or r == rows - 1 or not active[r - 1, c] or not active[r + 1, c]

            if edge_x:
                s_eta_x[r, c] = 0.0
                s_u_x[r, c] = 0.0
                s_v_x[r, c] = 0.0
                s_z_x[r, c] = 0.0
            else:
                s_eta_x[r, c] = _minmod(
                    eta[r, c] - eta[r, c - 1], eta[r, c + 1] - eta[r, c]
                )
                s_u_x[r, c] = _minmod(u[r, c] - u[r, c - 1], u[r, c + 1] - u[r, c])
                s_v_x[r, c] = _minmod(v[r, c] - v[r, c - 1], v[r, c + 1] - v[r, c])
                s_z_x[r, c] = s_z_x_base[r, c]

            if edge_y:
                s_eta_y[r, c] = 0.0
                s_u_y[r, c] = 0.0
                s_v_y[r, c] = 0.0
                s_z_y[r, c] = 0.0
            else:
                s_eta_y[r, c] = _minmod(
                    eta[r, c] - eta[r - 1, c], eta[r + 1, c] - eta[r, c]
                )
                s_u_y[r, c] = _minmod(u[r, c] - u[r - 1, c], u[r + 1, c] - u[r, c])
                s_v_y[r, c] = _minmod(v[r, c] - v[r - 1, c], v[r + 1, c] - v[r, c])
                s_z_y[r, c] = s_z_y_base[r, c]


@njit(cache=True, parallel=True)
def save_state(h, hu, hv, h0, hu0, hv0, active):
    """Copy the state into persistent buffers for the RK2 average."""
    rows, cols = h.shape
    for r in prange(rows):
        for c in range(cols):
            h0[r, c] = h[r, c]
            hu0[r, c] = hu[r, c]
            hv0[r, c] = hv[r, c]


@njit(cache=True, parallel=True)
def rk2_average(h, hu, hv, h0, hu0, hv0, active, dry_tol):
    """u_new = (u^n + u^(2)) / 2, in place, with the dry-cell cleanup fused in.

    Written as a kernel rather than as numpy expressions because
    `h *= 0.5; h += 0.5 * h0` allocates a fresh full-size temporary for the
    right-hand side on every one of six statements, every step.
    """
    rows, cols = h.shape
    for r in prange(rows):
        for c in range(cols):
            if not active[r, c]:
                continue
            nh = 0.5 * (h[r, c] + h0[r, c])
            if nh < dry_tol:
                h[r, c] = 0.0
                hu[r, c] = 0.0
                hv[r, c] = 0.0
            else:
                h[r, c] = nh
                hu[r, c] = 0.5 * (hu[r, c] + hu0[r, c])
                hv[r, c] = 0.5 * (hv[r, c] + hv0[r, c])


@njit(cache=True)
def positivity_dt(h, dh, active, dry_tol):
    """Largest dt for which no wet cell is driven to negative depth.

    The CFL condition bounds the wave speed, but it does not bound how much
    water a single cell can lose in one step. At a shoreline a thin cell can
    have an outflux large enough to drain more than it holds, and the
    positivity guard in `apply_update` then zeroes it — discarding not the
    dry tolerance but the cell's entire remaining content.

    Measured on a violently sloshing partially-dry basin, that mechanism lost
    8.2% of the domain's water, far more than the ~1% the dry tolerance alone
    can account for. Constraining dt by min(h / -dh) over draining cells makes
    the loss bounded by the dry tolerance again, which is the only mass error a
    wet/dry scheme should have.

    Returns a large value when nothing is draining fast enough to matter.
    """
    rows, cols = h.shape
    limit = 1.0e30
    for r in range(rows):
        for c in range(cols):
            if not active[r, c]:
                continue
            depth = h[r, c]
            if depth <= dry_tol:
                continue
            drain = -dh[r, c]
            if drain <= 0.0:
                continue
            allowed = depth / drain
            if allowed < limit:
                limit = allowed
    return limit


@njit(cache=True, parallel=True)
def apply_update(h, hu, hv, dh, dhu, dhv, active, dt, dry_tol, max_speed, counters):
    """Explicit Euler update with positivity and thin-film momentum control.

    Three safeguards, in order of how often they fire:

    1. **Positivity.** A cell driven below the dry tolerance is set dry and its
       momentum zeroed. This is a small, bounded mass violation, reported by
       the caller as the mass-error diagnostic, and vastly preferable to a
       negative depth — which makes sqrt(g*h) complex and kills the run.

    2. **Thin-film damping.** Between `dry_tol` and `10*dry_tol` the momentum is
       scaled down linearly towards zero. Without it, a cell left holding a
       0.01 mm film inherits the momentum of the water that just drained out of
       it, and hu/h yields velocities of order 100 m/s. Those then drive the
       CFL timestep to zero and the solution diverges — measured here as a
       blow-up to 1e91 in a closed basin before this damping existed. Damping
       touches momentum only, never depth, so mass is conserved exactly.

    3. **Absolute speed cap.** A final backstop at `max_speed`. Physically the
       fastest a dam-break front can travel is the Ritter speed 2*sqrt(g*h0),
       about 99 m/s for a 250 m head, so anything beyond the configured cap is
       numerical. Every intervention is counted into `counters` and surfaced in
       the run diagnostics: a solver that silently clips is a solver that
       silently lies.

    `counters` is a 2-element array: [cells dried by positivity, cells capped].
    """
    rows, cols = h.shape
    thin = 10.0 * dry_tol
    dried = 0
    capped = 0

    for r in prange(rows):
        for c in range(cols):
            if not active[r, c]:
                continue
            new_h = h[r, c] + dt * dh[r, c]

            if new_h <= dry_tol:
                if h[r, c] > dry_tol:
                    dried += 1
                h[r, c] = 0.0
                hu[r, c] = 0.0
                hv[r, c] = 0.0
                continue

            h[r, c] = new_h
            new_hu = hu[r, c] + dt * dhu[r, c]
            new_hv = hv[r, c] + dt * dhv[r, c]

            if new_h < thin:
                scale = new_h / thin
                new_hu *= scale
                new_hv *= scale

            u = new_hu / new_h
            v = new_hv / new_h
            speed = np.sqrt(u * u + v * v)
            if speed > max_speed:
                factor = max_speed / speed
                new_hu *= factor
                new_hv *= factor
                capped += 1

            hu[r, c] = new_hu
            hv[r, c] = new_hv

    counters[0] += dried
    counters[1] += capped


@njit(cache=True, parallel=True)
def apply_friction(h, hu, hv, manning, active, dt, dry_tol):
    """Semi-implicit Manning friction.

        d(hu)/dt = -g * n^2 * |u| * hu / h^(4/3)

    Solved implicitly as hu_new = hu / (1 + dt * k) with
    k = g * n^2 * |u| / h^(4/3). An explicit treatment goes unstable in thin
    fast films, which is precisely the wet/dry front where friction matters
    most, so the implicit form is not an optimisation but a requirement.
    """
    rows, cols = h.shape
    for r in prange(rows):
        for c in range(cols):
            if not active[r, c] or h[r, c] <= dry_tol:
                continue
            depth = h[r, c]
            u = _velocity(depth, hu[r, c], dry_tol)
            v = _velocity(depth, hv[r, c], dry_tol)
            speed = np.sqrt(u * u + v * v)
            if speed < 1e-12:
                continue
            n = manning[r, c]
            k = G * n * n * speed / depth ** (4.0 / 3.0)
            factor = 1.0 / (1.0 + dt * k)
            hu[r, c] *= factor
            hv[r, c] *= factor


@njit(cache=True, parallel=True)
def update_maxima(h, hu, hv, z, max_depth, max_speed, max_hazard, arrival, active,
                  t, wet_threshold, dry_tol):
    """Track the derived rasters the results panel reports.

    Arrival time is recorded the first time a cell exceeds `wet_threshold`,
    not the first time it gets wet at all. A 2 cm film arriving 20 minutes
    before the 30 cm wave is not the number an evacuation plan needs.
    """
    rows, cols = h.shape
    for r in prange(rows):
        for c in range(cols):
            if not active[r, c]:
                continue
            depth = h[r, c]
            if depth <= dry_tol:
                continue
            if depth > max_depth[r, c]:
                max_depth[r, c] = depth
            u = _velocity(depth, hu[r, c], dry_tol)
            v = _velocity(depth, hv[r, c], dry_tol)
            speed = np.sqrt(u * u + v * v)
            if speed > max_speed[r, c]:
                max_speed[r, c] = speed
            hazard = depth * speed
            if hazard > max_hazard[r, c]:
                max_hazard[r, c] = hazard
            if depth >= wet_threshold and arrival[r, c] < 0.0:
                arrival[r, c] = t


@njit(cache=True)
def total_volume(h, active, cell_area):
    """Total water volume in the domain, for the mass-balance diagnostic."""
    rows, cols = h.shape
    total = 0.0
    for r in range(rows):
        for c in range(cols):
            if active[r, c] and h[r, c] > 0.0:
                total += h[r, c]
    return total * cell_area
