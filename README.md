# FloodGuard India

**Dam-break and flash-flood inundation modelling for any Indian river.**
Smart India Hackathon — Problem Statement **26161**.

> **Build status: Phase 0 (scaffold).** The repository structure, the backend
> capability API and the frontend shell are up and tested. No hydrodynamic
> results exist yet, and the UI shows none. See [SPEC.md](SPEC.md) for the full
> phase plan and [docs/AUDIT.md](docs/AUDIT.md) for the reference-code audit.

---

## What it will do

1. Simulate dam break / river blockage from **real DEM and hydrological data**, for any dam in the catalog.
2. Run **two independent hydrodynamic engines** — SPH and a Delft3D-class 2D shallow-water solver — on the same scenario, and compare them quantitatively.
3. Produce inundation maps from swappable input datasets (any river, any dam — configuration only, no code changes).
4. Serve a web dashboard that handles large rasters via COG tiling.
5. Export **.shp / .kml / .geojson / .tif** plus a filing-grade PDF report.
6. Map floods in near-real-time from **Sentinel-1** via Google Earth Engine.
7. Quantify HADR exposure: population, buildings, roads, hospitals, schools and cropland inside the inundation polygon.

**Demo reaches:** Tehri Dam → Bhagirathi → (Devprayag) → Ganga → Rishikesh → Haridwar, and Hirakud Dam → Mahanadi.

---

## Quickstart

```bash
# 1. Backend deps (pip path; see below for conda)
pip install -r requirements.txt && pip install -e backend

# 2. Frontend deps
cd frontend && npm install && cd ..

# 3. Run
make serve-backend     # http://localhost:8000/docs
make serve-frontend    # http://localhost:5173
```

Check what this machine can actually run:

```bash
python -m floodguard.cli engines
```

### conda path (recommended if you want ANUGA)

```bash
conda env create -f environment.yml
conda activate floodguard
pip install -e backend
```

ANUGA ships on conda-forge only. Without it, the ANUGA cross-check engine is
reported as unavailable rather than silently skipped.

---

## What is real, and what is a documented substitute

This is the project's central commitment, enforced in code and tested in CI:

- `GET /api/health/engines` probes the machine and reports each engine truthfully.
- If the `dflowfm` binary is absent, **nothing anywhere says "Delft3D"**. The API,
  the UI badge and the PDF all read `FloodGuard-SWE (Delft3D-class FV solver)`.
  FloodGuard still auto-generates a complete D-Flow FM input deck as a
  downloadable artefact — that deck is a real deliverable regardless.
- Same rule for SPH: PySPH is named when PySPH runs, DualSPHysics when
  DualSPHysics runs.
- Every raster, shapefile and JSON we write carries a provenance block: DEM
  source and resolution, dam parameters and their source, engine name and
  version, solver settings, grid resolution, CFL, runtime, git commit, UTC
  timestamp.
- Any value that has not been computed renders as `—`, never as a plausible
  placeholder.

---

## Repository layout

```
backend/app/          FastAPI application (routes, config, provenance, schemas)
backend/floodguard/   the science package — importable and CLI-usable
backend/tests/        pytest suite
frontend/src/         React 18 + TypeScript + Vite + Tailwind
data/scenarios/       YAML scenario definitions (adding a dam = adding a file)
data/catalog/         curated dams.geojson / rivers.geojson
docs/                 AUDIT.md, METHODOLOGY.md, DATA_SOURCES.md, validation/
reference/            third-party repos, cloned read-only, never vendored
```

## Make targets

| Target | What it does |
| --- | --- |
| `make setup` | Install backend + frontend dependencies |
| `make data` | Fetch and cache all input layers for a scenario |
| `make preprocess` | Condition the DEM, build grids, reservoir curve, cross-sections |
| `make validate` | Ritter / Stoker / lake-at-rest / mass balance → `docs/validation/` |
| `make simulate` | Headless end-to-end run |
| `make demo` | Start the full stack on the precomputed demo bundle |
| `make test` | Run the test suite |

Targets for unimplemented phases exit non-zero with a message naming the phase.
They never print a fabricated result.

## Licence and data sources

Input datasets and their licences are recorded in `docs/DATA_SOURCES.md` and in
`data/MANIFEST.json` (URL, SHA256, size, licence, fetch time per file).
