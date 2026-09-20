# FloodGuard India

**Dam-break and flash-flood inundation modelling for any Indian river.**
Smart India Hackathon — Problem Statement **26161**.

Simulates a dam break or river blockage from real terrain and hydrological
data, routes the flood wave downstream with a verified two-dimensional
shallow-water solver, and reports what lies inside the inundated area — with
every number traceable to the file it came from.

---

## What makes this different

Most dam-break demos show a map. The three things that matter here are what
sits behind it.

**1. The solver is verified against exact analytical solutions.**

| Check | Result |
| --- | --- |
| Ritter (1892) dry-bed dam break | relative L2 **0.305%**, h(dam) error **0.94%** |
| Stoker (1957) wet-bed dam break | relative L2 **0.725%**, shock within **0.4 cells** |
| Lake at rest over irregular bed | spurious discharge **3.9e-12**, drift **1.4e-14 m** |
| Mass conservation, fully wet | relative error **5.2e-16** |
| Wet/dry mass budget | **0.002%** loss |
| Grid convergence (Ritter) | observed order **0.91** |
| Frictional dam break | front retarded, never outruns Ritter |

`make validate` reproduces all seven in under a minute. Plots and error tables
are in [`docs/validation/`](docs/validation/).

**2. Nothing is labelled as something it is not.**

`GET /api/health/engines` probes the machine. When Delft3D binaries are absent,
the API, the UI badge and the PDF cover all read
`FloodGuard-SWE (Delft3D-class FV solver)` — never `Delft3D`. A test fails the
build if any path emits the wrong string. The same rule holds for SPH: PySPH is
named when PySPH runs, and when it cannot, the engine reports itself unavailable
rather than returning a depth-averaged result under an SPH label.

**3. "Not computed" is never rendered as zero.**

A layer that failed to download shows an em dash and a reason on hover. Those
are different statements about the world, and conflating them in a flood
exposure report is dangerous.

---

## Quickstart

```bash
pip install -r requirements.txt && pip install -e backend
cd frontend && npm install && cd ..

make validate                                       # 7/7 in under a minute
make data      SCENARIO=tehri_bhagirathi            # fetch real Copernicus DEM
make simulate  SCENARIO=tehri_bhagirathi            # headless end-to-end
make serve-backend & make serve-frontend            # http://localhost:5173
```

Check what this machine can actually run:

```bash
python -m floodguard.cli engines
```

<details>
<summary>conda path, if you want ANUGA</summary>

```bash
conda env create -f environment.yml
conda activate floodguard
pip install -e backend
```

ANUGA ships on conda-forge only. Without it, the independent cross-check engine
is reported unavailable rather than silently skipped.
</details>

---

## Architecture

```
                 CWC NRLD          Copernicus GLO-30        OSM / WorldPop
                (30 dams,           (AWS, keyless,           (Overpass,
              cited per field)      SHA256-cached)            cached)
                     |                     |                      |
                     v                     v                      v
             +-------------------------------------------------------+
   Phase 1-2 |  scenario YAML  ->  DEM conditioning, corridor trace,  |
             |                     reservoir curve + bathymetry       |
             +-------------------------------------------------------+
                                        |
             +-------------------------------------------------------+
   Phase 3   |  Froehlich / Von Thun / MacDonald  ->  level-pool      |
             |  routing  ->  breach hydrograph Q(t)                   |
             +-------------------------------------------------------+
                                        |
             +-------------------------------------------------------+
   Phase 4   |              Engine interface (one contract)           |
             |  FloodGuard-SWE | ANUGA | Delft3D FM | PySPH | DualSPH |
             |  MUSCL-HLLC, well-balanced, numba-jitted               |
             +-------------------------------------------------------+
                                        |
             +-------------------------------------------------------+
   Phase 5-6 |  depth / velocity / arrival rasters  ->  hazard bands  |
             |  ->  COG, SHP, KML, GeoJSON, CSV  ->  HADR exposure    |
             +-------------------------------------------------------+
                                        |
             +---------------------------+---------------------------+
   Phase 7-10|  FastAPI + WebSocket  ->  React dashboard  ->  PDF     |
             +-------------------------------------------------------+
```

Every stage writes provenance: DEM source and resolution, dam parameters and
their citations, engine and version, solver settings, git commit, UTC timestamp.

---

## What is real, and what is a documented substitute

| Component | Status on a clean machine |
| --- | --- |
| **FloodGuard-SWE** 2D solver | **Real.** Verified 7/7. This is what powers the demo |
| Breach models | **Real.** Froehlich, Von Thun & Gillette, MacDonald — all three run |
| DEM acquisition | **Real.** Copernicus GLO-30 from AWS, checksummed |
| Dam catalog | **Real.** 30 dams, CWC NRLD-2019, cited per field |
| GIS exports | **Real.** COG, zipped SHP, GeoJSON, KML, KMZ, CSV |
| PDF report | **Real.** 6 pages with provenance on every page |
| **Delft3D FM** | Deck **generated**; solver runs only if `dflowfm` is on PATH |
| **DualSPHysics** | `CaseDef.xml` **generated**; solver needs GenCase + DualSPHysics |
| **ANUGA** | conda-forge only; reported unavailable otherwise |
| **PySPH** | Needs a C compiler; case definition present, reports unavailable |
| **GEE monitoring** | Inert without `GOOGLE_APPLICATION_CREDENTIALS`; says so |
| Exposure analysis | Real, but reports "not computed" unless OSM/WorldPop were fetched |
| 3D view | **Not built.** The tab says so rather than showing a placeholder |

The Delft3D deck is a deliverable in its own right: a complete UGRID `_net.nc`,
`.mdu`, boundary `.pli` and `.bc` carrying the breach hydrograph, and a DIMR
config, generated from a DEM and a dam record. It runs the moment you point it
at a licensed solver.

---

## Demo scenarios

Deliberately opposite regimes, to show the framework is general rather than
tuned to one valley. Adding a dam is a YAML file, not a code change.

| | Tehri | Hirakud |
| --- | --- | --- |
| River | Bhagirathi → Ganga | Mahanadi |
| Head | 260 m | 61 m |
| Storage | 3,540 MCM | 8,136 MCM |
| Terrain | Himalayan gorge | deltaic plain |
| Wave | deep, fast, minutes of warning | wide, slow, hours of warning |

Tehri is on the **Bhagirathi**, not the Alaknanda. Those two meet at
**Devprayag** to form the Ganga, which then flows through Rishikesh and
Haridwar.

---

## Repository layout

```
backend/app/          FastAPI: routes, config, provenance, job store, schemas
backend/floodguard/   the science package — importable and CLI-usable
  data/               fetchers: DEM, dams, OSM, population, GEE
  preprocess/         conditioning, corridor, reservoir, cross-sections
  breach/             parameter models, outflow, level-pool routing
  engines/            Engine interface + 5 backends, numba kernels
  postprocess/        hazard classification, GIS exports
  impact/             HADR exposure
  compare/            cross-engine metrics
  validation/         analytical solutions + the verification runner
frontend/src/         React 18 + TypeScript + MapLibre + Recharts
data/scenarios/       YAML scenario definitions
data/catalog/         dams.geojson with per-field citations
docs/                 AUDIT, METHODOLOGY, DATA_SOURCES, DEMO_SCRIPT, validation/
```

## Make targets

| Target | What it does |
| --- | --- |
| `make setup` | Install backend and frontend dependencies |
| `make data` | Fetch and cache every input layer, checksummed |
| `make preprocess` | DEM conditioning, corridor, reservoir curve |
| `make validate` | The seven verification checks → `docs/validation/` |
| `make simulate` | Headless end-to-end run |
| `make test` | 115 tests |

Targets for unimplemented phases exit non-zero with a message naming the phase.
They never print a fabricated result.

---

## Documentation

- **[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)** — equations, schemes,
  assumptions, verification results, and eight stated limitations.
- **[`docs/AUDIT.md`](docs/AUDIT.md)** — what we found in every reference
  repository, file by file, including two that were not what they claimed.
- **[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)** — every dataset, URL,
  licence, and what each one *cannot* tell you.
- **[`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)** — five-minute walkthrough
  and the three questions judges ask.
- **[`docs/validation/summary.md`](docs/validation/summary.md)** — the
  verification report with plots.

---

## Licence and attribution

Input dataset licences are in `docs/DATA_SOURCES.md` and in `data/MANIFEST.json`
(URL, SHA256, size, licence and fetch time per file).

Third-party solvers are used within their licences: **DualSPHysics (LGPL-2.1)**
and **Delft3D (AGPL/GPL/LGPL/BSD)** are invoked as external processes or read
only for their file formats — no source from either is copied into this
repository. **ANUGA (Apache-2.0)** and **PySPH (BSD/MIT)** are installed
dependencies.
