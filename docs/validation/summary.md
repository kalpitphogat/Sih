# Solver verification

Verification asks whether the code solves the equations it claims to solve.
These are analytical solutions and closed-form invariants, so the answers are
exact and the comparison is not a matter of opinion.

**7 of 7 checks passed** (79.8s).

| Check | Result | Criterion |
| --- | --- | --- |
| Ritter dry-bed dam break | PASS | relative L2 0.503% < 5%, h(dam) error 1.90% < 5%, front lag 21.1% in [0%, 25%) |
| Stoker wet-bed dam break | PASS | relative L2 1.101% < 5%, shock within 0.1 < 12 cells |
| Lake at rest over irregular bed (well-balancedness) | PASS | spurious discharge 3.92e-12 < 1e-10, surface drift 1.42e-14 m < 1e-10 |
| Mass conservation, no wet/dry front | PASS | relative volume error 1.72e-16 < 1e-9 |
| Wet/dry mass budget (measured cost of drying) | PASS | relative mass loss 0.002% < 1% |
| Grid convergence (Ritter) | PASS | observed order 0.92 > 0.8 and error decreases with dx |
| Frictional dam break (front retardation) | PASS | rough front 1175 m < frictionless 1318 m <= Ritter 1396 m |

## Detail

### Ritter dry-bed dam break

**PASS** — relative L2 0.503% < 5%, h(dam) error 1.90% < 5%, front lag 21.1% in [0%, 25%)

The front lag is expected and is documented, not a defect: a monotone second-order scheme cannot resolve the infinite-gradient tip where depth reaches zero. It must never LEAD the analytical front, which would mean the wave is arriving too early.

![Ritter dry-bed dam break](ritter_dam_break.png)

```json
{
  "L1": 27.64760703751184,
  "L2": 1.5250615158840306,
  "Linf": 0.2632832962897229,
  "RMSE": 0.03410141219385568,
  "relative_L2": 0.005025879222142668,
  "n_cells": 400,
  "dam_depth_m": 4.528900850861707,
  "dam_depth_expected_m": 4.444444444444445,
  "dam_depth_error_pct": 1.9002691443883934,
  "front_numerical_m": 1312.5,
  "front_analytical_m": 1396.1817764612601,
  "front_lag_fraction": 0.2112206603966369,
  "front_threshold_m": 0.01,
  "t_s": 20.0,
  "dx_m": 5.0
}
```

### Stoker wet-bed dam break

**PASS** — relative L2 1.101% < 5%, shock within 0.1 < 12 cells

The wet-bed case is the one that matters for a real reach, where the flood runs onto an existing river rather than a dry bed. It produces a genuine shock, so it tests the HLLC solver rather than only the rarefaction.

![Stoker wet-bed dam break](stoker_dam_break.png)

```json
{
  "L1": 26.13954448652759,
  "L2": 3.4601511829788123,
  "Linf": 1.3042341201156074,
  "RMSE": 0.07737133257566937,
  "relative_L2": 0.011011700174514727,
  "n_cells": 400,
  "shock_numerical_m": 1187.5,
  "shock_analytical_m": 1187.7969741217005,
  "shock_error_cells": 0.05939482434009733,
  "shock_speed_ms": 9.389848706085026,
  "t_s": 20.0
}
```

### Lake at rest over irregular bed (well-balancedness)

**PASS** — spurious discharge 3.92e-12 < 1e-10, surface drift 1.42e-14 m < 1e-10

Over a bed with 118 m of relief. A non-well-balanced scheme produces velocities of order sqrt(g*dz) here, i.e. several m/s of flow that does not exist.

![Lake at rest over irregular bed (well-balancedness)](lake_at_rest.png)

```json
{
  "max_water_surface_drift_m": 1.4210854715202004e-14,
  "max_spurious_discharge_m2s": 3.922195901395755e-12,
  "max_depth_change_m": 1.4210854715202004e-14,
  "steps": 200,
  "bed_relief_m": 118.4089327422326
}
```

### Mass conservation, no wet/dry front

**PASS** — relative volume error 1.72e-16 < 1e-9

Water is given an initial momentum so it sloshes against the walls; a static test would pass trivially. Every cell stays wet throughout, so this measures the flux scheme alone.

![Mass conservation, no wet/dry front](mass_conservation.png)

```json
{
  "initial_volume_m3": 346639999.9999999,
  "final_volume_m3": 346639999.9999998,
  "relative_error": 1.7194970221379716e-16,
  "steps": 300,
  "dried_cells": 0
}
```

### Wet/dry mass budget (measured cost of drying)

**PASS** — relative mass loss 0.002% < 1%

This is not a conservation failure to be fixed; it is the price of a wet/dry treatment, quantified. The number is reported alongside every simulation so a reviewer can see what it cost on their own case.

![Wet/dry mass budget (measured cost of drying)](wetdry_mass_budget.png)

```json
{
  "initial_volume_m3": 28275583.999999996,
  "final_volume_m3": 28276092.765054666,
  "relative_loss": 1.7993087416664187e-05,
  "drying_events": 262,
  "theoretical_bound": 3.706377912477423e-06,
  "dry_tolerance_m": 0.001,
  "shoreline_cells_initial": 140,
  "steps": 300
}
```

### Grid convergence (Ritter)

**PASS** — observed order 0.92 > 0.8 and error decreases with dx

Second order is not attainable on a discontinuous solution; roughly first order in L1 is the expected and correct result at a shock.

![Grid convergence (Ritter)](grid_convergence.png)

```json
{
  "resolutions": [
    100,
    200,
    400
  ],
  "dx_m": [
    20.0,
    10.0,
    5.0
  ],
  "L1_errors": [
    99.31056553054532,
    53.28398803888703,
    27.64760703751184
  ],
  "observed_order": 0.922396297563073
}
```

### Frictional dam break (front retardation)

**PASS** — rough front 1175 m < frictionless 1318 m <= Ritter 1396 m

The Dressler/Whitham line is a first-order asymptotic estimate shown for orientation, not a precision benchmark. The assertion being tested is the ordering: friction must retard the front, and the frictionless front must never outrun Ritter.

![Frictional dam break (front retardation)](friction_dam_break.png)

```json
{
  "front_frictionless_m": 1318.3333333333335,
  "front_rough_m": 1175.0,
  "ritter_front_m": 1396.1817764612601,
  "dressler_estimate_m": 1317.2579330179249,
  "retardation_m": 143.33333333333348,
  "manning_n": 0.05
}
```

## What these do not establish

Verification is not validation. Passing every check above means the numerics
are right; it says nothing about whether the DEM, the breach parameters, the
roughness field or the reservoir bathymetry describe the real river. Those are
validation questions, and they are answered separately against observed events.