# Reference Code Audit

**Date:** 2026-09-20 · **Auditor:** Phase 0.3 · **Gate:** Phase 2 may not start until this file exists.

Eight repositories were cloned into `reference/` (gitignored, read-only). Every
claim below was checked by reading the source, and where practical by executing
it. Nothing here is taken from a README.

Clone state, with commit hashes: `reference/clone_state.json`.
Regenerate with `python scripts/clone_references.py`.

**All 8 repositories cloned successfully. None 404'd.**

---

## 0. Executive summary

| Repo | Verdict | What we take |
| --- | --- | --- |
| **hydrobreach** (teammate) | **Real, verified, honest.** 21.8k LOC, 30 test files. Its HLLC solver reproduces Ritter and Stoker — *we ran the suite: 13/13 pass*. | **Port heavily.** Solver core, breach parameter models, exporters, GEE module, impact curves. |
| **dam-break-prototype** (teammate) | **Theatre.** Both "engines" are closed-form decay curves. The SPH does no SPH; the Delft3D fallback never reads the DEM. | **Port nothing.** Reuse two *ideas* only: the D-Flow FM deck writer's file list, and the anti-hallucination test pattern. |
| **NSUT-SIH-REPO** (teammate) | **No physics at all.** Auth + CRUD only. Contains a committed secret. | **Port nothing.** |
| **anuga_core** | Real, published, validated. Apache-2.0. | Use as an installed dependency (cross-check engine). |
| **pysph** | Real. BSD/MIT. | Use as an installed dependency; copy `dam_break_2d/3d` setup patterns. |
| **DualSPHysics** | Real GPU SPH. **LGPL-2.1.** | Run as an external binary only. Never link or vendor. |
| **Delft3D** | Official Deltares source. Mixed AGPL/GPL/LGPL/BSD. | Read for input-deck format. Run as external binary only. |
| **SimpleDambrk** | Real but dated (Python 2-era GDAL, QGIS-bound). | Read for method; port nothing. |

**The single most important finding:** `hydrobreach` already contains a
*verified* 2D shallow-water solver. That changes the plan for Phase 4.1 from
"write a solver and hope it validates" to "port a solver that already passes
Ritter and Stoker, then make it fast." This is the difference between a project
that demonstrably works and one that might.

---

## 1. `hydrobreach` — maharishia07/hydrobreach

`eacd948`, 2026-08-28 · 130 files · 21,768 lines of Python · 30 test modules ·
**no LICENSE file** (teammate repo; confirm intent before any external release).

### 1.1 What is real — verified by execution

`hydrobreach/solvers/swe2d.py` (563 lines) is a genuine Godunov finite-volume
shallow-water solver:

- `_hllc_flux()` implements the HLLC flux with **Toro's two-rarefaction star-state
  estimate**, a Rankine–Hugoniot shock correction, and **separate dry-bed wave
  speeds** for left-dry, right-dry and both-dry faces. This is textbook-correct,
  not a wrapper around a decay formula.
- The contact wave speed `s_star` selects which side supplies transverse
  momentum — the detail that distinguishes HLLC from plain HLL.
- Hydrostatic reconstruction (Audusse et al. 2004) for well-balancing, cited in
  the module docstring and present in the code.
- Backend dispatch (`resolve_backend`) selects NumPy or CuPy at runtime.

**We executed `tests/test_verification.py`: 13 passed in 134 s.** That suite asserts:

| Test | Threshold asserted |
| --- | --- |
| Ritter depth profile | RMSE / h₀ < **2 %** |
| Ritter h(x₀,t) = 4/9·h₀ | within **6 %** |
| Ritter front never outruns analytical | front_num ≤ front_ana + 2·dx |
| Ritter documented front lag | 0 ≤ lag < **25 %** |
| Mass conservation | mass error < **1e-10** |
| Stoker wet-bed depth profile | RMSE / h₀ < **3 %** |
| Stoker shock position | within 20·dx |
| Lake at rest, flat bed | \|hu\|, \|hv\| < 1e-10 |
| Lake at rest, steep irregular bed | water surface drift < 1e-9; spurious velocity < 1e-9 |

This is exactly the evidence SPEC.md rule 4 demands, and it already exists.

Also real and directly portable:

- `breach/parameters.py` (385 lines) — **Froehlich (2008)**, **Von Thun &
  Gillette (1990)** with the reservoir-size offset Cb, and **MacDonald &
  Langridge-Monopolis (1984)**, each returning a `BreachGeometry` tagged with the
  model that produced it. Paper citations in the docstring.
- `solvers/analytical.py` (167 lines) — Ritter, Stoker (with a `brentq` solve for
  the intermediate state), Stoker shock speed, and a **Dressler** frictional
  front celerity.
- `exporters.py` — GeoTIFF, polygonize, contour bands, Shapefile, GeoJSON, KML,
  **KML 3-D**, **time-animated KML**, arrival isochrones, run manifest. This is
  most of SPEC.md Phase 5's export list, already written.
- `gee/flood_mapping.py` — real `COPERNICUS/S1_GRD`, Refined Lee speckle filter,
  **Otsu thresholding**, `JRC/GSW1_4/GlobalSurfaceWater` permanent-water
  exclusion, and a **true local-incidence-angle terrain mask** accounting for
  Sentinel-1's right-looking geometry. More rigorous than SPEC.md Phase 8 asks for.
- `impact.py` — depth×velocity hazard classification, people-stability threshold,
  depth–damage curves, building/infrastructure assessment, evacuation feasibility.
- `adapters/delft3d.py` and `adapters/dualsphysics.py` — both **honest**.
  `run_case()` raises `RuntimeError("'dflowfm' not found on PATH. Delft3D FM is
  not bundled…")` rather than silently substituting. They write real input decks
  either way. This matches our engineering rule 2 exactly.

### 1.2 What is honestly reported as weak

`docs/VALIDATION.md` reports its own Sikkim 2023 validation as **CSI = 0.004**,
states plainly that this is "very poor", and explains why (simulated extent
~250× the observed extent; POD = 1.000, FN = 0). A team that publishes its own
bad number is a team whose good numbers can be trusted. We will carry this
document's posture into our own validation section.

### 1.3 Genuine gaps — this is what we still have to build

Searched and **absent** from hydrobreach:

- **No ANUGA engine.** SPEC.md 4.2 is ours to write.
- **No PySPH / SPH engine of any kind.** Only a DualSPHysics *adapter*, which
  needs binaries we do not have. **SPEC.md 4.4 is entirely ours**, and this is
  the largest remaining risk, since SPH-vs-2D comparison is a headline deliverable.
- **No Numba.** The solver is vectorised NumPy/CuPy. 134 s for the verification
  suite alone; too slow for a live demo at 30 m over a 120 km reach. **JIT work
  is ours** (SPEC.md 4.1).
- **No elevation–area–capacity curve** derived from the DEM, and **no level-pool
  reservoir routing**. SPEC.md 2.3 and 3 are ours. The reservoir-storage sanity
  check that SPEC.md calls out as impressive does not exist yet anywhere.
- **No auto cross-section extraction** at named towns (SPEC.md 2.4).
- **No COG / tile serving.** SPEC.md's large-raster requirement is ours.
- **No React frontend.** The dashboard is Streamlit — a different product from
  the three-column MapLibre/deck.gl UI in SPEC.md Phase 9.
- **No FastAPI/WebSocket job API.** SPEC.md Phase 7 is ours.
- **No MUSCL second-order reconstruction** found in `swe2d.py`; it appears to be
  first-order in space. Worth confirming and upgrading, since first-order
  smears the front and mistimes arrival — the number judges care about.

### 1.4 Decision

**Port, with attribution:** `solvers/swe2d.py`, `solvers/analytical.py`,
`breach/parameters.py`, `breach/hydrograph.py`, `exporters.py`,
`gee/flood_mapping.py`, `impact.py`, `adapters/delft3d.py`,
`adapters/dualsphysics.py`, and the `tests/test_verification.py` assertions.

**Rewrite rather than port:** the dashboard (wrong framework), the CLI (our
scenario/YAML contract differs), and the data fetchers (ours must be
content-addressed with `MANIFEST.json` per SPEC.md rule 6).

**Confirm before use:** the missing LICENSE. It is a teammate repo, so this is a
formality, but it must be settled in writing before anything ships publicly.

---

## 2. `dam-break-prototype` — ashutosh9544/dam-break-prototype

`3b263d3`, 2026-09-09 · 37 files · 1,899 lines of Python · **no LICENSE file**.

This repo is well-organised, well-commented, has tests and a document called
`MODEL_LIMITATIONS.md`. It is also, as a matter of verified fact, not running
the physics it claims.

### 2.1 `backend/core/delft3d_model.py` (368 lines) — **mock**

The file opens with a header promising *"STRICT ANTI-HALLUCINATION ENFORCEMENT"*.
Partially true, and wrong in a way that matters more.

**What is genuinely right:** `is_delft3d_available()` really does check PATH and
the standard Deltares install directories. `prepare_delft3d_inputs()` really does
write `.mdu`, `dimr_config.xml`, `.tim` and `.ext` files. When no binary is found,
`engine` is set to `"Fallback Prototype"` and never to `"Delft3D"` — the test
`test_delft3d_detection_and_anti_hallucination` enforces that.

**What is wrong, finding 1 — the fallback is not a solver.** The function is named
`_run_hydraulic_fallback_prototype` and documented as a *"calibrated 2D
diffusion-wave hydraulic prototype"* that *"accurately routes flood hydrograph
along DEM flowpaths"*. It does no such thing. Reading lines 199–265, the entire
computation is one closed-form expression on a fixed 60×50 lat/lon mesh:

```
depth = h_est · exp(−distance_km / 28) · exp(−(lateral_distance / (650 + 35·d_km))²)
```

There is no time loop, no continuity equation, no momentum equation, no flow
routing. `28 km` and `650 m` are tuned constants. **`dem_data` is passed in as the
first argument and then never referenced anywhere in the function body** — we
grepped the whole body to confirm. The terrain plays no part in a result
presented as terrain-routed. The river centreline is hardcoded as
`river_lat = 21.54 − 0.09·t + 0.015·sin(4πt)` — a fitted curve for the Mahanadi
that is meaningless for any other river, which defeats SPEC.md rule 5 outright.

**What is wrong, finding 2 — the real branch is the fabricated one.** If Delft3D
*is* installed and exits 0, control reaches `_parse_actual_delft3d_output()`
(line 355). Its docstring says "Parse real NetCDF output files." It parses
nothing. It returns a hardcoded literal:

```python
return {"engine": "Delft3D", "max_depth": 8.1, "arrival_time": 20.0,
        "flood_extent": [], "depth_grid": [], ...}
```

So the anti-hallucination guard protects the path where Delft3D is *absent*,
while the path where Delft3D is *present* returns `8.1 m` labelled **"Delft3D"**.
That is precisely the failure SPEC.md rule 1 exists to prevent, and it is
invisible to the test suite, which only ever runs on machines without the binary.

### 2.2 `backend/core/sph_model.py` (419 lines) — **mock**

Metadata claims `"method": "2D Shallow Water SPH (Monaghan formulation)"`, and
the docstring claims a Wendland C2 kernel and Velocity-Verlet integration under
a CFL condition.

`_cubic_spline_kernel()` is defined at line 110. **We grepped every call site: it
is never invoked.** `h_smooth` is used only to compute the kernel that is never
called, and to print `smoothing_length_m` in the output metadata.

The actual loop (lines 222–260) advances each particle independently under bed
slope and Manning friction — a **ballistic particle tracer**, not SPH. There is
no density summation, no pressure gradient, no equation of state, and no
particle–particle interaction of any kind. Removing every other particle would
not change a single trajectory. Depth is not evolved by continuity; it decays by
a fixed `depths *= (1 − 0.0018·dt)`. Velocity is capped at 22 m/s "to prevent
numerical divergence", and `max_depth` is floored at `0.8 m` and arrival time at
`2.0 min` — floors that guarantee a plausible-looking answer regardless of input.
The flow direction is hardcoded to `(0.85, −0.52)`, i.e. the Mahanadi's
south-easterly trend.

Labelling this "Monaghan formulation" in an output field that reaches the UI is
the exact thing a hydrology judge will catch.

### 2.3 What is genuinely usable

- `dam_model.py` (138 lines) cites Froehlich (2008) and MacDonald &
  Langridge-Monopolis (1984) and computes a peak discharge and a hydrograph from
  them. Broadly sound, but `hydrobreach`'s implementation is more complete
  (three models, erodibility classes, explicit `BreachGeometry`), so we take that one.
- `tests/test_delft3d.py` has the right *shape* for an honesty test. We have
  already adopted the pattern in `backend/tests/test_health.py`, extended so it
  also covers the branch where the binary **is** present.
- `prepare_delft3d_inputs()` is a useful checklist of which D-Flow FM files a
  complete deck needs.

### 2.4 Decision

**Port no code.** Take the input-deck file list and the honesty-test pattern as
ideas. Add to our own test suite an assertion that no code path can emit
`engine == "Delft3D"` without a verified binary execution — the specific defect
found here, guarded against in our repo.

---

## 3. `NSUT-SIH-REPO` — PixelPilot5/NSUT-SIH-REPO

`c1b489c`, 2026-09-11 · 17 tracked files · MIT licence.

Source is shipped as two zip archives rather than tracked files, so nothing in it
is diffable or reviewable in git.

- `src/backend.zip` is **20 MB and 2,361 files, of which a committed
  `venv/` is all but five**. The five real files are `main.py` (219 lines),
  `models.py`, `database.py`, `database_models.py` and `.env`.
- `main.py` is FastAPI **auth and CRUD only**: user registration, JWT login,
  and a `Simulations` table storing `state, district, river, dam, scenario,
  model` as strings. **There is no hydrodynamics, no DEM handling and no GIS
  anywhere in this repository.**
- `database.py` hardcodes `postgresql://postgres:@localhost:5432/floodsim` — a
  passwordless superuser DSN.
- `src/Suraksha-Setu-Frontend-Final.zip` is 16 files of static HTML/CSS/JS
  (`simulation.html`, `results.html`, `live-monitor.html`). No build system, no
  framework, no API binding worth porting into a React/TypeScript app.
- `submission/` holds a PPTX and demo notes; `assets/screenshots/` holds six
  JPEGs of a UI.

### 3.1 Security finding — action required

**`backend/.env` is committed inside `src/backend.zip` and contains a
`JWT_SECRET_KEY`.** The value is not reproduced here. Because it is inside a zip
it is invisible to secret scanners and to casual review.

Recommended, and not ours to do unilaterally — **flag to the repo owner**:
rotate the key, remove `.env` from the archive, add it to `.gitignore`, and
consider that any JWT ever issued with it should be treated as compromised.

### 3.2 Decision

**Port nothing.** If the team later wants user accounts and saved-run history,
the schema (`Users`, `Simulations`) is a reasonable starting shape, but SPEC.md
does not ask for authentication and we should not add it.

---

## 4. Established engines

### 4.1 `anuga_core` — anuga-community · Apache-2.0

Real, published, extensively validated 2D SWE solver from Geoscience Australia,
with its own `benchmarks/` and `validation` trees and a CITATION.cff.

**Use as an installed dependency (`conda install -c conda-forge anuga`), never
vendored.** Its value to us is precisely that it is *independent*: agreement
between ANUGA and our ported solver on the same scenario is third-party evidence
that our answer is right.

**Environment note:** conda is not installed on the current build machine, so
ANUGA is currently reported `unavailable` by `/api/health/engines`. That is the
correct, honest state, not a bug.

### 4.2 `pysph` — pypr/pysph · BSD/MIT

Real Python SPH framework. `pysph run dam_break_2d` / `dam_break_3d` are the
canonical setup patterns for our SPH engine, and `dam_break_3d` is the SPHERIC
Test 2 case SPEC.md 4.5 asks us to validate against.

Permissive licence, so we may copy setup patterns with attribution.
Installation needs a C compiler; currently absent here, reported honestly.

**This is the critical path for Phase 4.4**, since no teammate repo contains any
working SPH whatsoever.

### 4.3 `DualSPHysics` — **LGPL-2.1**

Real GPU SPH with dam-break examples and a Dockerfile.

**Licence constraint, and it is the binding one in this audit:** LGPL-2.1 permits
*use* of the program and dynamic linking, but copying its source into our tree
would impose LGPL obligations on that code. **We invoke `GenCase` and
`DualSPHysics` as external processes only.** No source is copied. Our adapter
writes `CaseDef.xml`, shells out, and parses the VTK output.

### 4.4 `Delft3D` — Stichting Deltares · **mixed AGPL-3.0 / GPL-3.0 / LGPL-2.1 / BSD**

The official source tree, C/C++/Fortran, built with conan.

**We do not build it and we copy nothing from it.** AGPL-3.0 on any part we
linked would reach our whole distribution. Its role in this project is as a
*specification*: `examples/` and `doc/` define the `.mdu`, `_net.nc`, `.bc`/`.pli`
and DIMR formats our adapter must emit. Reading a format is not copying code.

When a licensed `dflowfm` binary exists on a machine, our adapter runs it and
labels the result `Delft3D`. Otherwise — as here — the deck is still generated
and offered for download, and the solve is labelled
`FloodGuard-SWE (Delft3D-class FV solver)`.

---

## 5. `SimpleDambrk` — FloodRiskGroup/SimpleDambrk

`bf784c0`, **2022-03-14** (last commit "Update import osr") · 75 files.

Closest single repo to our end-to-end scope: breach outflow → 1-D downstream
propagation → DEM-based delineation of flood-prone areas, with two real
validation cases (**Gleno**, a historical 1923 dam failure, and **San Giuliano**)
shipped as shapefiles and clipped DTMs.

Eight scripts: `SimpleDambrk_main.py`, `RoutingCinemat.py` (kinematic routing),
`CalFloodArea.py`, `InterpolateCrossSec.py`, `GridTools.py`, plus per-case
upload/calc scripts.

**Limitations:** it is a QGIS-plugin-era tool tied to a SQLite GeoDB and the
pre-3.x `osr` GDAL bindings, and it targets *concrete* dam failure with
simplified equations — a weaker physical model than the full 2D SWE we are
running.

**Decision: read, port nothing.** Two things are worth borrowing as method —
the cross-section interpolation approach for SPEC.md 2.4, and the Gleno case as
a potential additional validation target if the shapefiles prove usable.

---

## 6. What this audit changes about the plan

1. **Phase 4.1 becomes a port-and-optimise, not a write-from-scratch.** We take
   hydrobreach's verified HLLC solver, re-run its verification suite in *our*
   CI as the Phase 4.5 gate, then add Numba JIT and confirm MUSCL second-order
   reconstruction. Days saved, and the validation evidence exists on day one.
2. **Phase 4.4 (SPH) is the real risk and moves up in priority.** No teammate
   repo has working SPH. PySPH needs a C compiler that this machine lacks. The
   SPH-vs-2D comparison is a headline deliverable, so this needs an early
   decision, and an honest reduced scope if it proves infeasible.
3. **Phase 5 exports are largely done.** hydrobreach already writes GeoTIFF,
   SHP, GeoJSON, KML, 3-D KML and time-animated KML. COG tiling remains ours.
4. **Phase 8 (GEE) is stronger than specced** in hydrobreach, including a
   local-incidence-angle terrain mask. Port it nearly intact.
5. **Phases 2.3, 3 (reservoir curve + level-pool routing), 7 (API) and 9
   (React UI) are entirely ours.** Nothing in any reference repo helps.
6. **Two honesty defects found in `dam-break-prototype` become tests in our
   repo**: no path may emit `engine == "Delft3D"` without a verified binary run,
   and no solver may report a DEM-routed result without reading the DEM.

## 7. Licence register

| Repo | Licence | How we may use it |
| --- | --- | --- |
| hydrobreach | **none stated** | Teammate code — confirm licence in writing before public release |
| dam-break-prototype | **none stated** | Not porting any code; moot |
| NSUT-SIH-REPO | MIT | Not porting any code |
| anuga_core | Apache-2.0 | Installed dependency; attribution in README + About page |
| pysph | BSD / MIT | Installed dependency; setup patterns copied with attribution |
| DualSPHysics | LGPL-2.1 | **External binary invocation only.** No source copied |
| Delft3D | AGPL-3.0 / GPL-3.0 / LGPL-2.1 / BSD | **File formats read only.** No source copied, no linking |
| SimpleDambrk | not checked (porting nothing) | Method reference only |

Full dataset licences are tracked separately in `docs/DATA_SOURCES.md`.
