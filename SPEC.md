# CLAUDE CODE BUILD PROMPT — FloodGuard India (SIH PS 26161)

> Paste everything below this line into Claude Code, in the empty folder where the project should live.
> Or: save this file as `SPEC.md` in that folder and say `Read SPEC.md and execute it phase by phase, starting with Phase 0.`

---

## 0. MISSION

Build **FloodGuard India** — a working dam-break / flash-flood simulation and inundation-mapping platform for Smart India Hackathon problem statement **26161: "Dam Break Inundation Modelling Using Hydrodynamic Modelling of any River."**

The deliverable is a running system, not a slide deck:

1. A generalized modelling framework that simulates dam break / river blockage from real DEM + hydrological data.
2. Two independent hydrodynamic engines — **SPH** and a **Delft3D-class 2D shallow-water solver** — run on the same scenario, with a quantitative comparison between them.
3. Flood inundation scenarios generated from swappable input datasets (any river, any dam).
4. A web dashboard (GUI) for model input and output visualization that handles large rasters.
5. Exports to **.shp**, **.kml**, **.geojson**, **.tif**, plus a PDF report.
6. A near-real-time flood-mapping module using **Google Earth Engine** + open Sentinel-1 data.
7. HADR loss-and-damage analysis: population, buildings, roads, hospitals, schools, agricultural land inside the inundation polygon.

**Demo case:** Tehri Dam (Uttarakhand) → Bhagirathi → Ganga through Devprayag, Rishikesh, Haridwar.
**Second case (must also work, proves generality):** Hirakud Dam → Mahanadi (Odisha). Flatter terrain, bigger spread, better for showing inundation area.

---

## 1. NON-NEGOTIABLE ENGINEERING RULES

Read these before writing a single line. They exist because this project will be judged by people who know hydrology.

1. **No fabricated numbers, anywhere.** Every number shown in the UI must come from a computation on real input data, traced back to a file on disk. If a value cannot be computed yet, the UI shows `—` and a "not computed" state. Never hardcode `31.7 km²`.
2. **Label every engine honestly.** If real Delft3D (`dflowfm`/`dimr`) binaries are not present and we fall back to our own solver, the API response, the UI badge, and the PDF report must all say `engine: "FloodGuard-SWE (Delft3D-class FV solver)"` — never `"Delft3D"`. Same for SPH: if PySPH runs, say PySPH; if DualSPHysics runs, say DualSPHysics. A judge asking "is this really Delft3D?" must get a straight answer from the screen.
3. **Provenance metadata on every output.** Every GeoTIFF/SHP/JSON we write carries: DEM source + resolution + acquisition, dam parameters and their source, engine name + version, solver settings, grid resolution, CFL, runtime, git commit hash, UTC timestamp.
4. **Validate the physics before trusting the map.** The 2D solver must reproduce the analytical **Ritter** (dry-bed) and **Stoker** (wet-bed) dam-break solutions to within a stated L2 error, and pass a benchmark for frictional dam break. Ship those plots in `docs/validation/`. This is the single most convincing thing we can show.
5. **Everything is a config, not a constant.** River, dam, DEM, breach parameters, resolution, duration — all from YAML/JSON scenario files. Adding a new dam must require zero code changes.
6. **Cache all downloads.** `data/raw/` is content-addressed and never re-downloaded; a `data/MANIFEST.json` records URL, SHA256, size, license, fetch time for every file. The demo must run fully offline once data is fetched.
7. **Commit in small, working increments** with clear messages. Never leave `main` broken. Do not run any interactive or destructive git command without asking.
8. **Tests are part of "done."** Every physics module ships with pytest cases. Every API endpoint ships with a test.

---

## 2. STACK

**Backend**
- Python 3.11, FastAPI, Uvicorn, Pydantic v2
- Job orchestration: asyncio background tasks + SQLite job store (upgrade to Celery/Redis only if needed); progress streamed over WebSocket
- Geo: `rasterio`, `rioxarray`, `xarray`, `geopandas`, `shapely`, `pyproj`, `fiona`, `richdem` or `whitebox` (hydro-conditioning), `pysheds`
- Numerics: `numpy`, `scipy`, `numba` (JIT the solver kernels — this is what makes it fast enough to demo)
- Engines: `anuga` (conda-forge) for 2D SWE reference, `pysph` for SPH, optional adapters for DualSPHysics and Delft3D-FM binaries
- Remote sensing: `earthengine-api`, `geemap`
- Report: `reportlab` or `weasyprint` + `matplotlib`

**Frontend**
- React 18 + TypeScript + Vite
- Tailwind CSS
- **MapLibre GL JS** for the map (raster/vector tiles, not a hundred GeoJSON features)
- **deck.gl** for the 3D (Beta) tab — TerrainLayer + water surface
- **Recharts** for hydrographs and cross-sections
- TanStack Query for API state

**Infra**
- `docker-compose.yml` for backend + frontend + tileserver
- `Makefile` with `make setup`, `make data`, `make validate`, `make demo`, `make test`
- `environment.yml` (conda, because ANUGA/GDAL) + `requirements.txt`

---

## 3. REPO LAYOUT

```
floodguard-india/
├── SPEC.md                     # this file
├── README.md                   # judge-facing: what it does, how to run, what's real
├── Makefile
├── docker-compose.yml
├── environment.yml
├── data/
│   ├── raw/                    # downloaded, immutable, gitignored
│   ├── processed/              # clipped/reprojected DEM, meshes, masks
│   ├── scenarios/              # tehri_bhagirathi.yaml, hirakud_mahanadi.yaml
│   ├── catalog/                # dams.geojson, rivers.geojson (our curated catalog)
│   └── MANIFEST.json
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/                # routes: scenarios, simulate, jobs, results, exports, monitoring
│   │   ├── core/               # config, provenance, job store
│   │   └── schemas/            # pydantic models = the API contract
│   ├── floodguard/             # the actual science package (importable, CLI-usable)
│   │   ├── data/               # fetchers: dem.py, dams.py, osm.py, population.py, gee.py
│   │   ├── preprocess/         # clip, reproject, hydro-condition, mesh, centerline, cross-sections
│   │   ├── breach/             # froehlich.py, macdonald.py, reservoir_routing.py
│   │   ├── engines/
│   │   │   ├── base.py         # Engine ABC: run(scenario) -> ResultBundle
│   │   │   ├── swe_fv.py       # our HLLC finite-volume solver (numba)
│   │   │   ├── anuga_engine.py
│   │   │   ├── delft3d_adapter.py   # writes .mdu/DIMR, runs dflowfm if present, parses NetCDF
│   │   │   ├── sph_pysph.py
│   │   │   └── dualsphysics_adapter.py
│   │   ├── postprocess/        # max depth/velocity rasters, arrival time, polygonize, exports
│   │   ├── impact/             # HADR exposure analysis
│   │   ├── compare/            # SPH vs SWE metrics
│   │   └── validation/         # ritter.py, stoker.py, benchmarks
│   └── tests/
├── frontend/
│   └── src/
│       ├── pages/              # Home, Simulation, RealtimeMonitoring, About
│       ├── components/         # InputPanel, MapView, ResultsPanel, ComparisonTable, Charts, ExportPanel
│       ├── api/
│       └── types/              # generated from backend OpenAPI
├── reference/                  # cloned teammate + third-party repos, READ-ONLY
└── docs/
    ├── AUDIT.md                # what we found in reference repos, what's real vs mock
    ├── METHODOLOGY.md          # equations, assumptions, limitations (judges will ask)
    ├── validation/             # Ritter/Stoker plots + error tables
    └── DATA_SOURCES.md         # every dataset, URL, license
```

---

## 4. PHASE 0 — SETUP + REFERENCE AUDIT

### 0.1 Scaffold
Create the repo layout above, `environment.yml`, `Makefile`, `.gitignore` (ignore `data/raw`, `data/processed`, `reference/`, outputs), and a FastAPI app that serves `/health` plus a Vite React app that renders an empty shell. Commit.

### 0.2 Clone reference repos into `reference/` (never import them wholesale)

Teammate / SIH-targeted repos (these are our own team's prior work — reuse freely, but verify):
- `https://github.com/ashutosh9544/dam-break-prototype`
- `https://github.com/maharishia07/hydrobreach`
- `https://github.com/PixelPilot5/NSUT-SIH-REPO`

Established open-source engines and references:
- `https://github.com/DualSPHysics/DualSPHysics` — real GPU SPH solver, has dam-break cases + Dockerfile
- `https://github.com/Deltares/Delft3D` — official Delft3D / D-Flow FM source
- `https://github.com/anuga-community/anuga_core` — validated Python 2D shallow-water solver (conda-forge: `conda install anuga`)
- `https://github.com/pypr/pysph` — Python SPH framework; `pysph run dam_break_2d` / `dam_break_3d` (SPHERIC Test 2)
- `https://github.com/FloodRiskGroup/SimpleDambrk` — breach hydrograph → downstream propagation → DEM inundation

If a clone 404s, note it in `docs/AUDIT.md` and move on — do not invent a substitute.

Also search GitHub yourself for anything better: `dam break inundation python`, `shallow water HLLC python flood`, `breach hydrograph Froehlich`, `LISFLOOD-FP`, `flood inundation Sentinel-1 GEE`. Add anything genuinely useful to `reference/` and to the audit. Check the license before reusing code; record it.

### 0.3 Write `docs/AUDIT.md`
For each reference repo, file-by-file: what is **actually implemented and runnable**, what is a **stub/mock/fallback**, what is **only README claims**. Specifically check whether the Delft3D "integration" executes a real binary or silently falls back. Then state exactly which modules we will port, which we will rewrite, and why.

**Gate:** do not start Phase 2 until AUDIT.md exists. It decides how much we build vs reuse.

---

## 5. PHASE 1 — DATA ACQUISITION (`make data`)

Write `backend/floodguard/data/` fetchers, each idempotent, cached, checksummed, and registered in `data/MANIFEST.json`. Each fetcher must degrade gracefully: if a source needs a key we don't have, fall back to a keyless source and log which one was used.

### 1.1 DEM (required)
Priority order, first that works:
1. **AWS open bucket, no key:** `https://copernicus-dem-30m.s3.amazonaws.com/` — Copernicus GLO-30 COGs, tile-per-degree naming like `Copernicus_DSM_COG_10_N30_00_E078_00_DEM/Copernicus_DSM_COG_10_N30_00_E078_00_DEM.tif`. Verify the exact key pattern by listing the bucket at runtime; don't hardcode a guessed path.
2. **Microsoft Planetary Computer STAC, no key:** `https://planetarycomputer.microsoft.com/api/stac/v1`, collections `cop-dem-glo-30`, `nasadem`, `alos-dem`. Use `pystac-client` + bbox search.
3. **OpenTopography Global DEM API** (needs a free key in `.env` as `OPENTOPO_API_KEY`):
   `https://portal.opentopography.org/API/globaldem?demtype=COP30&south=..&north=..&west=..&east=..&outputFormat=GTiff&API_Key=..`
   Valid `demtype`: `COP30`, `COP90`, `SRTMGL1`, `SRTMGL3`, `NASADEM`, `AW3D30`. Note the rate limit (≈200 calls/day academic) and the 450,000 km² per-request cap for 30 m data — so always clip to the basin bbox, never request a whole state.
4. **Bhuvan CartoDEM** (ISRO, 30 m, registration required) — document it in `DATA_SOURCES.md` as the India-official option and support a manually-placed file at `data/raw/dem/bhuvan/*.tif`. Judges from ISRO/NRSC like seeing CartoDEM supported.

Mosaic tiles, reproject to a metric CRS (UTM 44N for Tehri, UTM 45N for Hirakud), clip to the routing corridor, and write a COG.

### 1.2 Dam + reservoir attributes
- **India-WRIS** (`https://indiawris.gov.in`) — dams, reservoir daily level/storage, river/basin layers. Inspect the site's network calls and write a polite JSON client with caching and a hard fallback to a bundled snapshot. Never scrape in a loop during a demo.
- **CWC National Register of Large Dams (NRLD)** — dam height, FRL, MDDL, gross/live storage, year, type. Parse the published PDF/XLS into `data/catalog/dams.geojson`.
- **data.gov.in** — "Daily reservoir level (CWC)" resource for live-ish storage.
- **Global fallbacks:** GRanD v1.3 and GOODD dam databases, and **DAM-IN** (Tiwari & Aadhar 2025, *Scientific Data*) which covers 5,715 Indian dam catchments with 45+ attributes.

Bundle a hand-verified `dams.geojson` with at least 8 major Indian dams (Tehri, Hirakud, Bhakra, Sardar Sarovar, Nagarjuna Sagar, Srisailam, Idukki, Mettur) so the dropdown is never empty offline. Each record: name, river, state, lat/lon, dam type, structural height, crest length, FRL, MDDL, gross storage, live storage, spillway capacity, commissioned year, **source URL per field**.

> **Factual correction to carry into the catalog:** Tehri Dam is on the **Bhagirathi**, not the Alaknanda. Alaknanda + Bhagirathi meet at **Devprayag** to form the Ganga, which then flows through Rishikesh and Haridwar. So the demo scenario is `Bhagirathi → (Devprayag confluence) → Ganga`. Get this right in the dropdown and the map labels — a judge from Uttarakhand will catch it instantly.

### 1.3 River network / hydrology
- **HydroSHEDS / HydroRIVERS / HydroBASINS** (`hydrosheds.org`) for river centerlines and basin polygons.
- **India-WRIS** river + basin layers for the India-official version.
- Gauge discharge from **CWC** / India-WRIS for the downstream base flow boundary condition.
- Derive the routing corridor: flow accumulation from the conditioned DEM, snap dam point to the stream, trace downstream N km (configurable, default 120 km).

### 1.4 Exposure layers for HADR impact
- **OSM via Overpass API** (`https://overpass-api.de/api/interpreter`): `building=*`, `highway=*` (with length), `amenity=hospital|clinic|school|college`, `bridge=yes`, `place=city|town|village`. Cache raw responses. Add a `pyrosm`/Geofabrik India extract fallback so the demo works offline.
- **WorldPop** 100 m constrained population for IND (`hub.worldpop.org`) and/or **GHSL GHS-POP**.
- **ESA WorldCover 10 m** or **Bhuvan LULC** for agricultural land.
- **Optional:** NHAI/Bhuvan critical-infrastructure layers.

### 1.5 Satellite imagery
- **Sentinel-2** true colour for the map basemap over the demo reach (GEE or Copernicus Data Space).
- **Sentinel-1 GRD** for the near-real-time flood-detection module (Phase 8).

**Phase 1 acceptance:** `make data SCENARIO=tehri_bhagirathi` populates `data/raw` + `data/processed`, writes `MANIFEST.json`, and prints a table of every layer with source, CRS, resolution, extent and licence. Re-running downloads nothing.

---

## 6. PHASE 2 — PREPROCESSING

`backend/floodguard/preprocess/`:

1. **DEM conditioning:** void fill, optional stream burning, pit filling (`richdem`/`whitebox`), flow direction + accumulation, downstream trace from the dam, build the corridor mask with a configurable buffer (default 5 km either side of the valley, widened by valley width).
2. **Multi-resolution grids:** generate the compute grid at configurable resolution (30 m default, 10 m optional near the dam). Dam-break peaks are resolution-sensitive — expose it, don't hide it.
3. **Reservoir geometry:** delineate the reservoir pool from DEM + FRL, derive the **elevation–area–capacity curve** by integrating the DEM. Sanity-check the derived gross storage against the catalog value and report the % difference in the UI. This single check impresses judges.
4. **Cross-sections:** auto-extract perpendicular cross-sections at configurable chainages and at named towns (Devprayag, Rishikesh, Haridwar) — these feed the "Cross-section View" panel.
5. **Manning's n field:** from land cover (channel 0.03, forest 0.10, urban 0.08, agriculture 0.05...), written as a raster, overridable in the scenario YAML.
6. **Mesh generation** for the FM-style engine (triangular, refined along the channel) via `triangle`/`meshpy`/ANUGA's mesh tools.

---

## 7. PHASE 3 — BREACH + RESERVOIR ROUTING

`backend/floodguard/breach/`:

- **Breach parameter models:** Froehlich (2008), MacDonald & Langridge-Monopolis, Von Thun & Gillette — predicting breach width, side slope, formation time from dam height and storage. User-entered values override predictions; show both side by side so the user sees whether their input is physically plausible.
- **Breach growth:** linear / trapezoidal / sine progression over formation time, parabolic option.
- **Outflow:** broad-crested weir + orifice for the trapezoidal breach, with submergence correction.
- **Reservoir routing:** level-pool mass balance `dS/dt = I(t) − Q_breach(t) − Q_spillway(t)` using the elevation–area–capacity curve, adaptive timestep.
- **Scenario types:** `complete_dam_break`, `partial_breach`, `piping_failure`, `overtopping`, `controlled_release` (gate schedule), `landslide_dam_breach` (natural blockage — this is literally the Rishi Ganga 2021 case in the problem statement, so support it).
- **Output:** the breach outflow hydrograph `Q(t)`, peak discharge, time to peak, total volume released → this is the upstream boundary condition for both engines.

Validate against a published case (Teton Dam 1976 and/or Banqiao) and put the comparison in `docs/METHODOLOGY.md`.

---

## 8. PHASE 4 — HYDRODYNAMIC ENGINES

Define one interface, then implement four backends behind it:

```python
class Engine(ABC):
    name: str
    is_real_solver: bool          # False for any fallback/approximation
    def run(self, scenario: Scenario, progress_cb) -> ResultBundle: ...
```

`ResultBundle` = time series of depth/velocity rasters (NetCDF/Zarr) + derived max-depth, max-velocity, arrival-time rasters + runtime metadata.

### 4.1 `swe_fv.py` — our own 2D solver (the workhorse)
Godunov finite-volume solution of the 2D shallow-water equations:
- **HLLC approximate Riemann solver**, MUSCL 2nd-order reconstruction with a slope limiter
- Well-balanced treatment of the bed-slope source term (hydrostatic reconstruction) — must preserve lake-at-rest
- Robust **wetting/drying** with a depth tolerance
- Manning friction, semi-implicit
- Adaptive timestep from the CFL condition
- **Numba-jitted kernels**, domain decomposition over threads; optional CuPy GPU path
- Checkpointing + progress callback every N steps for the WebSocket

This is what actually powers the demo. Make it fast and correct.

### 4.2 `anuga_engine.py`
Wrap ANUGA (conda-forge) as an independent, externally-validated 2D SWE engine. Use it to cross-check `swe_fv` on the same scenario — agreement between an independent published solver and ours is strong evidence for judges.

### 4.3 `delft3d_adapter.py`
Generate a genuine D-Flow FM case: `_net.nc` unstructured grid, bathymetry samples, `.mdu`, boundary-condition files (`.bc`/`.pli`) carrying the breach hydrograph, DIMR config. If `dflowfm`/`dimr` is on PATH or a Delft3D Docker image is available, **run it for real** and parse the NetCDF map output. If not, raise a clear, typed `EngineUnavailable` and let the orchestrator substitute `swe_fv` **with the substitution surfaced everywhere in the UI and report**. Keep the generated Delft3D input files as downloadable artifacts either way — "here are the Delft3D input decks our tool auto-generated" is a legitimate, demonstrable deliverable.

### 4.4 `sph_pysph.py` + `dualsphysics_adapter.py`
- **PySPH** path: weakly-compressible SPH (δ-SPH / WCSPH) of the near-field breach flow — the 3D collapse at the dam face, first 60–300 s, where SPH genuinely beats depth-averaged models. Reference `pysph run dam_break_2d` / `dam_break_3d` for setup patterns.
- **DualSPHysics** path: write `CaseDef.xml`, run `GenCase` + `DualSPHysics` (Docker or local binary), parse output with `PartVTK`. Gate behind availability detection, same as Delft3D.
- **Coupling:** SPH near-field → extract the outflow hydrograph and momentum flux at a transfer section a few hundred metres downstream → feed the 2D engine for the far field. Document the coupling assumptions honestly. Also run SPH standalone over a coarse bathymetry for the full reach so the SPH-vs-Delft3D comparison table has like-for-like numbers.

### 4.5 Validation (`make validate`) — do this before building the dashboard
- **Ritter (1892)** dry-bed analytical solution — report L1/L2/L∞ error, plot overlay
- **Stoker** wet-bed solution
- **Dressler / Whitham** frictional dam break
- **SPHERIC Test 2** for the SPH engine
- **Malpasset 1959** real dam-break benchmark if survey data can be obtained — the gold standard
- Lake-at-rest well-balancedness test, mass-conservation test (report % mass error), grid-convergence study

Write plots + error tables to `docs/validation/`. Put a "Validation" section in the About page of the app linking to them.

---

## 9. PHASE 5 — POSTPROCESSING, GIS EXPORTS, COMPARISON

- Max water depth, max velocity, max `h·v` (hazard), flood arrival time (first time depth > threshold, default 0.3 m), time-varying extent.
- **Hazard classification** per a defined standard (e.g. depth×velocity hazard classes for people/vehicles/buildings) — cite the standard used.
- Polygonize into depth bands matching the legend: `>10`, `5–10`, `2–5`, `0.5–2`, `0.1–0.5` m.
- Export: **GeoTIFF (COG)**, **Shapefile (zipped)**, **KML/KMZ** (Google Earth ready, with time-stamped folders for the animation), **GeoJSON**, and depth time series at gauge points as CSV.
- **Tiling for large data:** serve rasters as COG + `titiler`-style dynamic tiles or pre-generated XYZ tiles so the browser never downloads a 2 GB raster. The PS explicitly demands large-volume support — this is how we satisfy it.
- **Comparison module** (`compare/`): on a common grid, compute per-engine max depth, max velocity, flooded area, arrival time at each named town, computation time, plus **Critical Success Index / F1 of flood extent overlap**, RMSE of depth field, and a difference raster. Drive the "Model Comparison (SPH vs Delft3D)" table entirely from this — every cell computed, including the `Difference` column.

---

## 10. PHASE 6 — HADR IMPACT ANALYSIS

Intersect the inundation polygon (and each depth band) with the exposure layers:
- Buildings: count, by depth band, by type
- Roads: length in km, by class; flag severed links
- Population: zonal sum of WorldPop/GHS-POP inside the flood extent, with a stated assumption note
- Agricultural land: area in km²
- Critical facilities: hospitals, schools, police, power substations — listed by name with depth and arrival time
- Bridges and dams downstream at risk
- **Evacuation value-add:** per settlement, arrival time + depth → a prioritized warning list sorted by lead time. That table is worth more to HADR judges than any 3D animation.

Output `impact.json` + a CSV, and expose it in the UI exactly as the "Affected Elements" cards.

---

## 11. PHASE 7 — BACKEND API

Pydantic-typed, OpenAPI-documented, with generated TS types for the frontend.

```
GET  /api/rivers                        -> catalog of rivers
GET  /api/dams?river=                   -> dams, with attributes + defaults (FRL, height, storage)
GET  /api/dams/{id}/reservoir-curve     -> elevation-area-capacity
POST /api/scenarios                     -> validate + persist a scenario config
POST /api/simulate                      -> {scenario_id, engines:["sph","swe"], resolution} -> {job_id}
GET  /api/jobs/{id}                     -> status, phase, % , ETA, logs
WS   /ws/jobs/{id}                      -> live progress + intermediate frames
GET  /api/results/{job_id}/summary      -> KPIs per engine (area, max depth, max velocity, arrival)
GET  /api/results/{job_id}/comparison   -> comparison table payload
GET  /api/results/{job_id}/impact       -> HADR numbers
GET  /api/results/{job_id}/hydrographs  -> discharge vs time at dam + towns
GET  /api/results/{job_id}/cross-section?location=  -> terrain + max water level
GET  /api/results/{job_id}/tiles/{z}/{x}/{y}.png?t=  -> depth tiles, time-indexed
GET  /api/results/{job_id}/export?format=shp|kml|geojson|tif|pdf
POST /api/results/{job_id}/share        -> short shareable link
POST /api/monitoring/analyze            -> GEE near-real-time flood extent for an AOI + date range
GET  /api/health/engines                -> which engines are actually available on this machine
```

`/api/health/engines` matters: it drives the honest engine badges in the UI.

Uploads: accept user DEM (GeoTIFF), boundary hydrograph (CSV), custom AOI (SHP/GeoJSON/KML) for the "Upload Custom Data" panel. Validate CRS, extent and units on upload with clear error messages.

---

## 12. PHASE 8 — GOOGLE EARTH ENGINE NEAR-REAL-TIME MODULE

`backend/floodguard/data/gee.py` + the "Real-time Monitoring" page:
- Service-account auth via `GOOGLE_APPLICATION_CREDENTIALS`; if absent, the page shows a clear "GEE not configured" state and a cached demo result — never a fake live feed.
- **Sentinel-1 GRD flood mapping:** pre-event vs post-event VV/VH, speckle filter, Otsu or fixed-threshold water classification, change detection, exclude permanent water using JRC Global Surface Water, terrain-shadow masking with the DEM.
- **Sentinel-2 / MODIS NDWI** cloud-permitting cross-check.
- **CHIRPS / IMERG** rainfall accumulation for the upstream catchment.
- Export the detected flood extent as GeoJSON/SHP/KML and **overlay it on the simulated extent**, reporting CSI/F1 agreement. Validating the model against the observed Feb 2021 Rishi Ganga / Chamoli event or the 2008 Kosi flood would be the strongest possible result — attempt it and report whatever comes out, good or bad.

---

## 13. PHASE 9 — FRONTEND (match the reference design exactly)

Dark navy chrome (`#0f2440`-ish header/footer), light content area, blue primary. Header: wave logo, **"FloodGuard India"**, subtitle *"Dam Break & Flash Flood Simulation for a Safer Tomorrow"*, nav **Home | Simulation | Real-time Monitoring | About**, right-side tagline *"Data Driven | Resilient Communities | Safer India"*, notification bell + account icon. Footer: *"Indian Rivers. Safer Communities."* left, *"Built for a Resilient India | HADR | v1.0.0"* right with the flag.

### Simulation page — three-column layout

**Left: "Simulation Input"**
1. *Select River & Dam* — River dropdown, Dam dropdown (filtered by river, showing state).
2. *Scenario Configuration* — Scenario Type dropdown (Complete Dam Break / Partial Breach / Piping Failure / Overtopping / Controlled Release / Landslide Dam Breach); numeric inputs: Reservoir Water Level (m), Breach Width (m), Breach Depth (m), Breach Formation Time (min), Simulation Duration (hours). Prefill from the dam catalog; show the empirical-model prediction next to each breach field as a ghost hint; validate against dam height/FRL with inline errors.
3. *Select Models* — checkboxes: Smooth Particle Hydrodynamics (SPH), Delft3D. Each carries a live availability badge from `/api/health/engines` (`real binary` / `FloodGuard-SWE substitute`).
4. **Run Simulation** primary button + **Reset**. While running: progress bar with phase name and live log line.
5. *Additional Options* — collapsible "Advanced Parameters" (grid resolution, Manning's n overrides, CFL, depth threshold, output interval) and "Upload Custom Data" (DEM / hydrograph / AOI).

**Center: map area with tabs — Flood Inundation Map | Comparison View | 3D View (Beta)**
- MapLibre, satellite basemap, dam marker (red triangle + label), town markers, river centerline, depth raster overlay with the exact legend bins (`>10` dark red, `5–10` orange, `2–5` yellow, `0.5–2` light blue, `0.1–0.5` blue), layer switcher, zoom controls, north arrow, scale bar.
- **Time slider + play/pause** animating the flood wave across the simulation duration (the screenshot lacks this; it is the single best live-demo feature — add it).
- **Comparison View:** swipe/split map SPH vs Delft3D-class, plus the difference raster.
- **3D View (Beta):** deck.gl TerrainLayer with the DEM + animated water surface.

**Right column**
- *Simulation Results (engine name)* — four KPI cards: Flooded Area (km²), Maximum Depth (m), Maximum Velocity (m/s), Earliest Arrival Time (min). An engine toggle switches between SPH and the 2D engine.
- *Affected Elements (within inundation area)* — six icon cards: Buildings, Roads (km), Population, Agricultural Land (km²), Hospitals, Schools. Each clickable → detail table/GeoJSON download.
- *Model Comparison (SPH vs Delft3D)* — table: Maximum Depth, Maximum Velocity, Flooded Area, Arrival Time at <town>, Computation Time; columns SPH | Delft3D-class | Difference (%, signed, green/red).
- *Export Results* — Download Flood Map (.SHP) [green], Download as KML [blue], Generate Report (PDF).
- *Share Scenario* — Generate Share Link.

**Bottom row**
- *Flood Arrival Time* — multi-series discharge (m³/s) vs time (hours) hydrograph: Dam, and each downstream town. Real routed hydrographs, not smoothed decoration.
- *Cross-section View (at <town>)* — terrain profile (grey fill) + max water level (blue fill) vs distance (m), annotated max depth arrow, with a dropdown to switch section location.

**Other pages**
- **Home:** what the tool does, the 3-step flow, the two demo scenarios as one-click launches, key capability cards.
- **Real-time Monitoring:** AOI picker, date range, "Detect flood extent" → Sentinel-1 result map, CSI agreement vs simulation, rainfall chart, export.
- **About:** problem statement, methodology summary, **engine availability + honesty statement**, validation results with plots, data sources + licences, team.

**UX requirements:** skeleton loaders, error toasts with actionable messages, responsive down to laptop 1366×768 (that is the projector resolution at the hackathon), keyboard-accessible controls, and a "Demo Mode" toggle that loads a precomputed result instantly for when the venue Wi-Fi dies.

---

## 14. PHASE 10 — REPORT, DEMO, PACKAGING

- **PDF report generator:** cover page, scenario parameters, methodology + equations, engine used (honest), inundation map figure, depth/velocity/arrival maps, hydrographs, cross-sections, impact tables, comparison table, validation summary, data sources, assumptions and limitations, provenance footer with git hash. This is what a district disaster-management officer would actually file.
- **Precomputed demo bundle:** commit (or fetch via one command) finished results for Tehri and Hirakud so `make demo` gives a full working dashboard in under 60 seconds with no internet.
- **`make demo`** = start backend + frontend + tile server, seeded, browser opens on the Tehri scenario.
- **README.md** for judges: one-paragraph what/why, 3-command quickstart, architecture diagram, screenshot, "what is real vs what is a documented substitute", validation results table, data sources, roadmap.
- **5-minute demo script** in `docs/DEMO_SCRIPT.md`: exact click order, expected numbers, what to say, the three questions judges will ask and the answers.

---

## 15. EXECUTION ORDER

Work in this order, committing at each step, and pause for my review at the marked gates:

1. Phase 0 scaffold + clone references → **GATE: show me `docs/AUDIT.md`**
2. Phase 1 data fetchers → `make data` works for Tehri → **GATE: show me the layer table + MANIFEST**
3. Phase 2 preprocessing + reservoir curve sanity check
4. Phase 3 breach + routing, with Teton validation
5. Phase 4.1 `swe_fv` + Phase 4.5 validation → **GATE: show me Ritter/Stoker error plots before any UI work**
6. Phase 5 postprocessing + exports (CLI-driven end-to-end run, headless)
7. Phase 6 impact analysis
8. Phase 7 API
9. Phase 9 frontend (Simulation page first, exactly as specced)
10. Phase 4.2–4.4 ANUGA / PySPH / adapters + comparison
11. Phase 8 GEE monitoring
12. Phase 10 report, demo bundle, docs, second scenario (Hirakud)

If any phase turns out to be infeasible in the available time, **say so and propose the honest reduced scope** rather than shipping a mock. A smaller system that is provably correct beats a large one that is partly theatre — and the second kind loses badly the moment a judge clicks something unexpected.

Start with Phase 0 now. Before you begin, list your plan for Phase 0 and any assumptions you're making about my environment (OS, GPU, conda availability, API keys I need to supply).
