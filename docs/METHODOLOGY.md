# Methodology

What FloodGuard India computes, how, and what each step assumes. Written for a
reviewer who knows hydrology and will ask where a number came from.

The structure follows the pipeline: terrain → reservoir → breach → routing →
hydrodynamics → consequences.

---

## 1. Terrain

### 1.1 Source

Copernicus DEM GLO-30, fetched from the AWS open-data bucket without
credentials. Tile existence is verified with a HEAD request before download, so
a wrong key pattern fails loudly rather than leaving a hole in the mosaic.
Fallbacks are Microsoft Planetary Computer STAC and OpenTopography; a manually
placed ISRO Bhuvan CartoDEM is preferred over all of them when present.

Vertical datum is EGM2008 (orthometric), which is kept rather than converted,
because the dam's FRL is also quoted above mean sea level.

**Limitation.** GLO-30 is a *surface* model. Over forested Himalayan valleys it
sits above true ground, which biases computed depths low and arrival times
late. Over water it returns the water surface at acquisition time, which is the
reason §2.2 exists.

### 1.2 Hydrological conditioning

- **Depression filling** by priority-flood (Barnes, Lehman & Mulla 2014), with
  an epsilon gradient so filled flats remain drainable. Implemented with a
  deterministic binary heap keyed on (elevation, insertion order), so the same
  DEM always yields the same filled surface — a non-deterministic fill would
  break provenance.
- **D8 flow direction** (O'Callaghan & Mark 1984), distance-weighted so a
  diagonal neighbour must be √2 times lower to win. Omitting that weighting
  biases traced channels into diagonal staircases.
- **Flow accumulation** by descending-elevation traversal, which is an exact
  topological sort for the price of one argsort.

### 1.3 Dam snapping

Published NRLD coordinates are single points of unstated convention at roughly
30 m precision, so they frequently land on an abutment rather than in the
channel. The dam is snapped to the highest-accumulation cell within `max(500 m,
crest_length / 2)` — see §2.3 for why a fixed radius fails — and the snap
distance is reported. At Tehri it moves 285–306 m and lands on the channel
*below* the embankment, which is where breach outflow physically enters the valley.

### 1.4 Domain

The area of interest is the union of two boxes: a downstream corridor
containing every named town plus the lateral buffer, and an upstream box large
enough to hold the reservoir. The upstream half is easy to forget and its
absence is silent — the pipeline runs, the map looks plausible, and the storage
is wrong by two orders of magnitude because most of the pool was never in the
raster.

The compute domain is then cropped to the bounding box of the routing corridor,
which removes about 70% of the cells for no loss of accuracy.

---

## 2. Reservoir

### 2.1 Pool delineation

The impoundment is the largest connected component of `dem ≤ FRL` **within the
dam's upstream catchment**, the catchment being computed by reverse-D8
traversal from the snapped dam cell.

Both restrictions are necessary:

- Without the catchment mask, a fill at FRL leaks straight past the dam. On a
  steep reach the river bed 60 km downstream sits hundreds of metres *below*
  the reservoir surface and therefore satisfies `dem ≤ FRL` too. At Tehri this
  produced 330,155 MCM against a registered 3,540.
- The snapped dam cell cannot seed the fill, because it is on the far side of
  the embankment from the pool. Taking the largest component instead is both
  robust and the correct definition of what water at FRL occupies.

At Tehri this yields **42.82 km²** against a published reservoir area of about
42 km².

### 2.2 Bathymetry reconstruction

A 30 m DSM cannot see through water. At Tehri it samples the reservoir surface
at 814.5 m and nothing beneath, so DEM integration yields only 593–639 MCM
against a registered 3,540 — an 82% shortfall. A dam break driven by 639 MCM
would be dramatically too small.

The submerged prism is reconstructed with the conic reservoir approximation
standard in USBR/USACE practice and the ICOLD sedimentation literature:

```
A(z) = A_ws · ((z − z_bed) / (z_ws − z_bed))^m
V(z) = A(z) · (z − z_bed) / (m + 1)
z_bed = z_ws − V_missing · (m + 1) / A_ws
```

with `m = 2` for a V-shaped gorge. The bed elevation is solved so that total
storage at FRL equals the registered value.

**This is a calibration, not a measurement**, and it is labelled as such
everywhere it appears. A bathymetric survey would replace it.

**Independent cross-check.** The reconstruction implies a bed at 512–577 m MSL
depending on the DEM resolution used. Crest elevation (839.5 m) minus the NRLD
structural height (260.5 m) gives 579 m. At 30 m resolution the two agree to
2 m from entirely separate data paths — the DEM on one side, the dam register
on the other.

### 2.3 When the DEM cannot delineate the pool

The Tehri result above is the favourable case: a deep reservoir in a confined
gorge, where a 30 m DSM resolves the pool almost exactly. Hirakud is the
unfavourable case, and it is worth stating plainly because it generalises.

Hirakud impounds **743 km²** of large, shallow, dendritic water on flat deltaic
terrain. At 120 m resolution the DEM delineates **212 km²** of it. Two things
defeat it: narrow arms fall below the cell size, and the flat water surface
gives the drainage network no gradient to follow, so D8 accumulation across the
pool is diffuse and the catchment boundary becomes unreliable.

Three consequences, each with a guard:

1. **The snap radius must scale with the dam.** A fixed 300 m window is right
   for Tehri's 575 m crest and badly wrong for Hirakud's 4.8 km embankment,
   where it snapped to a local drain and produced a 28 km² catchment for a dam
   that drains 83,000 km². The radius is now `max(500 m, crest_length / 2)`.

2. **Flow accumulation is only meaningful inside the fetched raster.** A dam
   whose catchment dwarfs the domain will snap to a tributary and nothing about
   the arithmetic will look wrong. If the snapped cell carries less than 5% of
   the domain's largest drainage, the run warns that the reservoir, the routed
   path and every downstream number should be treated as wrong.

3. **The reconstruction must be refused, not qualified, when it is impossible.**
   §2.2 solves for whatever bed depth makes the storage match, so a pool 500×
   too small yields a bed 1,000 m too deep — at Hirakud, −957 m MSL for a 61 m
   dam. Any bed below the dam's own foundation is now rejected outright. The
   numbers that follow an impossible geometry are not worth caveating; they are
   worth withholding.

**The escape hatch is the professional input.** Dam authorities publish the
reservoir surface area at FRL and the full elevation–area–capacity curve, so
`reservoir.area_at_frl_km2` accepts it with a cited source. For Hirakud,
supplying the published 743 km² moves derived storage from **−92%** to
**−4.4%** against the NRLD, with a bed at 160 m MSL — comfortably above the
135 m foundation. The substitution is recorded and warned about, so a reader
knows the area came from the operator rather than from the terrain.

### 2.4 Elevation–area–capacity curve

At each sampled level the connected pool is re-delineated and storage computed
as `Σ (level − bed) · cell_area` over submerged cells. Summing the prism depth
rather than multiplying area by depth is what makes this a hypsometric
integration rather than a bathtub approximation. The curve is checked for
monotonicity; non-monotonicity would mean the delineation jumped basins, and is
surfaced rather than smoothed away.

---

## 3. Breach

### 3.1 Parameter models

Three published regressions, all run, never just one:

| Model | Form |
| --- | --- |
| Froehlich (2008) | `B_avg = 0.27·k₀·V^0.32·h^0.04`, `t_f = 63.2·√(V/(g·h²))` |
| Von Thun & Gillette (1990) | `B_avg = 2.5·h_w + C_b`, `t_f` from erodibility class |
| MacDonald & Langridge-Monopolis (1984) | `V_er = 0.0261·(V_out·h_w)^0.769`, `t_f = 0.0179·V_er^0.364` |

They are run together because they disagree — at Tehri by a factor of 7.8 on
width — and the spread is reported with the note that when it exceeds about 2,
breach geometry rather than the solver is the dominant uncertainty in the
resulting map.

For a **concrete or masonry dam all three are marked inapplicable**. They are
regressions on embankment failures, where the breach grows by progressive
erosion; a concrete dam fails by structural collapse of one or more monoliths,
essentially instantaneously.

MacDonald predicts eroded *volume*, not width. The conversion to a width
assumes a prism of base thickness 3× the dam height with 0.5H:1V sides — our
assumption, not the authors', and recorded as such in the caveats.

### 3.2 Validation against historical failures

Run with `floodguard breach --validate`. Reported as computed, including where
poor.

**Teton Dam, 5 June 1976** (observed: width 151 m, formation 72 min, peak
65,120 m³/s):

| Model | Width error | Formation time error |
| --- | ---: | ---: |
| Froehlich (2008) | **+11%** | **+6%** |
| Von Thun & Gillette (1990) | +65% | −3% |
| MacDonald & Langridge-Monopolis | −33% | +218% |

**Banqiao Dam, 8 August 1975** errors are substantially larger. The published
parameters for that event vary far more widely, and that is stated rather than
hidden.

Routing the **observed** Teton geometry gives a peak of 110,852 m³/s against an
observed 65,120. That test is kept separate from the parameter-model scores on
purpose: it isolates whether our routing converts a known breach into the right
discharge, rather than conflating that with whether the models predict the
right breach. Most of the remaining gap is the synthetic conic reservoir
standing in for Teton's real elevation–area–capacity curve, which is not public.

Source for observed values: Wahl (1998), *Prediction of Embankment Dam Breach
Parameters*, USBR Dam Safety Report DSO-98-004.

### 3.3 Outflow and reservoir routing

Trapezoidal breach as a broad-crested weir:

```
Q = C_r·B·H^(3/2) + C_s·z·H^(5/2)
```

with `C_r = 1.7`, `C_s = 1.35` (SI, DAMBRK/HEC-RAS convention), plus a
Villemonte (1947) submergence correction when tailwater rises above the invert.
A piping failure discharges through an orifice, `Q = C·A·√(2gH)` with
`C = 0.6`, until the roof collapses and it becomes a weir.

The breach develops in **both width and depth** over the formation time, under
a linear, sine or parabolic growth law. A breach that reached full depth
instantly while widening slowly would release full head from t = 0 and
overstate the peak.

Level-pool routing integrates

```
dS/dt = I(t) − Q_breach(t) − Q_spillway(t)
```

on the DEM-derived curve, with an adaptive midpoint (RK2) step sized so the
water level falls at most 5 cm per step. That is what makes the computed peak a
property of the physics rather than of the timestep; there is a test asserting
the peak moves less than 2% when the step cap is refined 5×.

Mass closure is reported with every hydrograph. On the Tehri case it is
0.0000%.

---

## 4. Hydrodynamics

### 4.1 Governing equations

Two-dimensional shallow water in conservative form:

```
∂h/∂t  + ∂(hu)/∂x + ∂(hv)/∂y = S_mass
∂(hu)/∂t + ∂(hu² + gh²/2)/∂x + ∂(huv)/∂y = −gh·∂z/∂x − g·n²·u|u|/h^(1/3)
∂(hv)/∂t + ∂(huv)/∂x + ∂(hv² + gh²/2)/∂y = −gh·∂z/∂y − g·n²·v|u|/h^(1/3)
```

**Assumptions this makes**, and where they fail:

- Hydrostatic pressure. Fails at the breach face, where the flow is strongly
  accelerating and the free surface overturns — which is what the SPH near-field
  model exists to cover.
- Depth-uniform velocity. Fails in the same region.
- Small bed slope. Himalayan reaches stretch this; the well-balanced treatment
  keeps the scheme stable but the depth-averaged assumption is still an
  approximation on a 1:10 slope.

### 4.2 Numerical scheme — FloodGuard-SWE

Godunov finite volume on a uniform Cartesian grid:

- **HLLC approximate Riemann solver** (Toro 2009, ch. 10) with the
  two-rarefaction star-state estimate, a Rankine–Hugoniot shock correction, and
  separate dry-bed wave speeds. HLLC rather than HLL because HLL smears the
  contact wave, and in a dam break the front *is* a shock — a smeared front
  mistimes arrival, which is the number a disaster officer acts on.
- **MUSCL reconstruction with a minmod limiter**, applied to the **primitive**
  variables (η = h + z, u, v) rather than the conserved ones. This matters
  twice: reconstructing discharge hands wet/dry faces a tiny depth beside a
  large momentum and yields velocities of order 10⁴ m/s, and reconstructing
  depth destroys well-balancing because still water over a slope has constant
  η but varying h.
- **Hydrostatic reconstruction** (Audusse et al. 2004) with the centred
  bed-slope term, so the scheme is well-balanced at second order.
- **Wet/dry handling**: desingularised velocity (Kurganov & Petrova 2007),
  first-order reconstruction adjacent to dry cells, thin-film momentum damping,
  and a positivity-preserving timestep constraint.
- **Semi-implicit Manning friction**, because an explicit treatment goes
  unstable in exactly the thin fast films where friction matters most.
- **SSP-RK2 (Heun)** in time.
- **Boundaries**: reflective at inactive cells, transmissive at the raster edge.

### 4.3 Verification

Run with `make validate`. All seven checks pass; plots and error tables are in
`docs/validation/`.

| Check | Result |
| --- | --- |
| Ritter dry-bed dam break | relative L2 **0.503%**, h(dam) error **1.90%**, front lag 21.1% |
| Stoker wet-bed dam break | relative L2 **1.101%**, shock within **0.06 cells** |
| Lake at rest, irregular bed | spurious discharge **3.92e-12**, surface drift **1.42e-14 m** |
| Mass conservation, fully wet | relative error **1.72e-16** |
| Wet/dry mass budget | **0.002%** loss |
| Grid convergence (Ritter) | observed order **0.92** |
| Frictional dam break | front retarded, never outruns Ritter |

Notes on interpretation:

- **The front lag is expected, not a defect.** A monotone second-order scheme
  cannot resolve the infinite-gradient tip where depth reaches zero. What
  matters is that it must never *lead* the analytical front — a flood arriving
  earlier than physics allows is the worst direction for this error to go.
- **First order at a shock is the correct result.** Second order is unattainable
  on a discontinuous solution; roughly first order in L1 is what the literature
  expects, and we report the observed order rather than claiming the design one.
- **Verification is not validation.** Passing every check above means the
  numerics are right. It says nothing about whether the DEM, the breach
  parameters, the roughness field or the reconstructed bathymetry describe the
  real river.

### 4.4 Boundary condition at the breach

The outflow is distributed over a footprint approximating the breach width, and
injected **with momentum** at the critical-flow velocity √(gh) along the traced
downstream direction.

Both details are physics, not optimisation. Tehri's predicted breach is 498 m
wide, which at 90 m resolution is 21 cells; putting the 1.29 million m³/s peak
into one 8,100 m² cell adds 64 m of water in a single step. And water leaving a
breach with zero momentum forms a static column that collapses radially — an
artificial second dam break whose signature then propagates downstream as
though it were the real wave.

### 4.5 Other engines

| Engine | Status |
| --- | --- |
| **ANUGA** | Independent cross-check. Different mesh, reconstruction and Riemann solver, so agreement is evidence rather than a tautology. conda-forge only. |
| **Delft3D FM** | A complete, runnable input deck is generated whether or not the solver exists; the solver runs when `dflowfm` is on PATH. |
| **PySPH** | Near-field WCSPH/δ-SPH at the breach face, coupled to the 2D engine at a transfer section. |
| **DualSPHysics** | GPU SPH through `GenCase`/`DualSPHysics` as external processes. LGPL-2.1: no source is copied. |

When a requested engine is unavailable the orchestrator substitutes a native
solver **in one visible place** and labels the substitution in the API
response, the UI badge and the PDF. No adapter falls back internally.

---

## 5. Consequences

### 5.1 Hazard classification

Depth alone does not describe danger: a metre of standing water is an
inconvenience, a metre at 3 m/s overturns a car. Classification uses the
depth–velocity product per **AIDR Handbook 7 (2017), *Managing the Floodplain*,
Table 6.1**, after Smith et al. (2014) ARR Project 10.

A cell takes the lowest class satisfying *all* of its depth, velocity and
depth-velocity limits, so deep-but-slow and shallow-but-fast both classify as
dangerous.

### 5.2 Exposure

Buildings, roads, healthcare, education, bridges and settlements from
OpenStreetMap via Overpass; population from WorldPop 100 m constrained.

- **Roads are geometrically clipped**, not counted: half a highway inside the
  extent is half its length, not one road.
- **Population is summed as persons-per-pixel**, not area-weighted. WorldPop
  and GHS-POP store counts, not densities; treating them as densities is the
  classic error in flood exposure reporting and inflates the number by orders
  of magnitude.
- **A layer that failed to fetch reports "not computed", never zero.** Those are
  different statements and conflating them is the most dangerous thing this
  module could do.

Population figures are modelled residential estimates, not a census and not a
casualty figure. The note saying so travels with the number into the PDF.

### 5.3 Evacuation priority

Per settlement: arrival time, depth and population, **sorted by lead time**,
because that is the order a response is executed in. Settlements that are not
flooded are omitted rather than listed with nulls — a warning list containing
non-events will not be read.

---

## 6. Known limitations

1. **DSM, not DTM.** Depths biased low and arrival late under forest canopy.
2. **Reconstructed bathymetry.** Calibrated to registered storage, not surveyed.
   On flat dendritic reservoirs the DEM cannot even delineate the pool, and the
   published surface area must be supplied — see §2.3.
3. **Uniform Manning's n** unless a land-cover raster is supplied. Friction is
   the second most sensitive parameter after resolution.
4. **Breach geometry dominates.** The empirical models disagree by more than the
   solver's own error in most scenarios.
5. **Resolution sensitivity.** A coarse cell averages the channel with its banks
   and under-predicts the peak. Every output records the resolution it used.
6. **Single-limb routing.** At Devprayag the Alaknanda's own discharge is a
   boundary inflow, not a simulated limb. A real confluence study would model both.
7. **No sediment, no debris, no structural failure downstream.** A real dam-break
   surge carries a large sediment load and destroys bridges; none of that is modelled.
8. **Verification ≠ validation.** See §4.3.

---

## References

- Audusse, E., Bouchut, F., Bristeau, M.-O., Klein, R. & Perthame, B. (2004). A fast and stable well-balanced scheme with hydrostatic reconstruction for shallow water flows. *SIAM J. Sci. Comput.* 25(6), 2050–2065.
- Barnes, R., Lehman, C. & Mulla, D. (2014). Priority-flood: An optimal depression-filling and watershed-labeling algorithm for digital elevation models. *Computers & Geosciences* 62, 117–127.
- Chow, V.T. (1959). *Open-Channel Hydraulics.* McGraw-Hill.
- Dressler, R.F. (1952). Hydraulic resistance effect upon the dam-break functions. *J. Res. NBS* 49(3).
- Froehlich, D.C. (2008). Embankment dam breach parameters and their uncertainties. *J. Hydraulic Eng.* 134(12), 1708–1721.
- Kurganov, A. & Petrova, G. (2007). A second-order well-balanced positivity preserving central-upwind scheme for the Saint-Venant system. *Commun. Math. Sci.* 5(1), 133–160.
- MacDonald, T.C. & Langridge-Monopolis, J. (1984). Breaching characteristics of dam failures. *J. Hydraulic Eng.* 110(5), 567–586.
- O'Callaghan, J.F. & Mark, D.M. (1984). The extraction of drainage networks from digital elevation data. *Computer Vision, Graphics and Image Processing* 28(3).
- Ritter, A. (1892). Die Fortpflanzung der Wasserwellen. *Zeitschrift des Vereines Deutscher Ingenieure* 36(33), 947–954.
- Smith, G., Davey, E. & Cox, R. (2014). *Flood Hazard.* ARR Project 10 Report.
- Stoker, J.J. (1957). *Water Waves: The Mathematical Theory with Applications.* Interscience.
- Toro, E.F. (2009). *Riemann Solvers and Numerical Methods for Fluid Dynamics*, 3rd ed. Springer.
- Villemonte, J.R. (1947). Submerged weir discharge studies. *Engineering News-Record* 139.
- Von Thun, J.L. & Gillette, D.R. (1990). *Guidance on Breach Parameters.* USBR.
- Wahl, T.L. (1998). *Prediction of Embankment Dam Breach Parameters.* USBR DSO-98-004.
- Whitham, G.B. (1955). The effects of hydraulic resistance in the dam-break problem. *Proc. R. Soc. A* 227, 399–407.
