# Data sources and licences

Every dataset FloodGuard India reads, where it comes from, what it may be used
for, and what it cannot tell you.

Machine-readable equivalents:
- `data/MANIFEST.json` — URL, SHA256, size, licence and fetch time for every
  file actually downloaded on this machine.
- `data/catalog/dams.geojson` — per-field citations for every dam attribute.

---

## Terrain

### Copernicus DEM GLO-30 — primary

| | |
| --- | --- |
| **Source** | ESA / DLR, distributed on AWS Open Data |
| **Endpoint** | `https://copernicus-dem-30m.s3.amazonaws.com/` |
| **Credentials** | none |
| **Resolution** | 30 m (1 arc-second) |
| **Vertical datum** | EGM2008 orthometric |
| **Licence** | Free for any use with attribution. © DLR e.V. 2010–2014, © ESA. See the [CSCDA mission-specific annex](https://spacedata.copernicus.eu/documents/20123/121286/CSCDA_ESA_Mission-specific+Annex.pdf) |

**What it cannot tell you.** It is a *surface* model. Over forest it returns
canopy top, over water the water surface at acquisition time, and over cities
rooftops. Depths computed on it are biased low in forested valleys, and the
reservoir bathymetry beneath the water surface is entirely invisible — see
`METHODOLOGY.md` §2.2.

### Microsoft Planetary Computer — fallback

STAC API at `https://planetarycomputer.microsoft.com/api/stac/v1`, collections
`cop-dem-glo-30`, `nasadem`, `alos-dem`. No credentials; assets are signed
automatically. Same ESA/DLR terms for the Copernicus product.

### OpenTopography — fallback, needs a key

`https://portal.opentopography.org/API/globaldem`. Requires a free academic key
in `OPENTOPO_API_KEY`. Rate-limited to roughly 200 calls per day, with a
450,000 km² cap per request for 30 m products — which is why the AOI is always
clipped to the routing corridor rather than a whole state. Cite OpenTopography
alongside the original product.

### ISRO Bhuvan CartoDEM — preferred when present

30 m, from the NRSC Open EO Data Archive at `https://bhuvan.nrsc.gov.in/`.
Registration is required and cannot be automated, so this is the one source
FloodGuard only ever reads from disk: place tiles at
`data/raw/dem/bhuvan/*.tif` and they are picked up automatically and preferred
over everything else, because it is the India-official product.

**Redistribution is restricted.** No CartoDEM data is bundled with this
repository.

---

## Dams and reservoirs

### CWC National Register of Large Dams (NRLD) — primary

| | |
| --- | --- |
| **Source** | Central Water Commission, Government of India |
| **Editions used** | [NRLD-2019](https://cwc.gov.in/sites/default/files/nrld-2019.pdf) for attributes and coordinates; [NRLD-2018](https://cwc.gov.in/sites/default/files/nrld06042018.pdf) for the dam-type legend |
| **Coverage** | 30 dams across 25 rivers in `data/catalog/dams.geojson` |
| **Licence** | Government of India publication. Reuse under the National Data Sharing and Accessibility Policy (NDSAP) |

Every attribute carries its own citation down to the page and PIC code, e.g.
*"NRLD-2019; Uttarakhand, p.263 (PIC UA34VH0012, Dam Type column)"*. The
transcription originates in the team's `hydrobreach` repository and is carried
across verbatim rather than re-keyed.

**What it does not contain.** The tables transcribed carry dam height,
crest length, storage, type and year — but **not** full reservoir level, minimum
drawdown level, crest elevation or spillway capacity. Those fields are
therefore `null` for every catalog record and are **never guessed**. Scenario
files supply them explicitly with their own citation, each flagged for
verification against the DEM-derived elevation–area–capacity curve.

**Known discrepancy carried forward.** NRLD-2019 lists Hirakud as an arch dam,
which is wrong. The 2018 composite classification is used and the disagreement
is recorded in the catalog record's notes rather than silently resolved.

**Known gap.** Idukki (Kerala, Periyar) is named in the problem statement but is
not in the transcription we inherited. It is recorded as a known gap in the
catalog metadata rather than omitted silently. Adding it means transcribing the
Kerala state sheet with the same per-field citations.

### Operating levels from project literature

FRL, MDDL and crest elevation in the bundled scenarios come from project
operator literature — THDC India Ltd for Tehri, the Odisha Department of Water
Resources for Hirakud. Each is cited in the scenario YAML and explicitly
flagged as *not* from the NRLD.

### Other registers, supported but not currently bundled

- **India-WRIS** (`https://indiawris.gov.in`) — dams, reservoir daily level and
  storage, river and basin layers.
- **data.gov.in** — CWC daily reservoir level resource.
- **GRanD v1.3** and **GOODD** — global dam databases.
- **DAM-IN** (Tiwari & Aadhar 2025, *Scientific Data*) — 5,715 Indian dam
  catchments with 45+ attributes.

---

## Hydrology and river networks

- **HydroSHEDS / HydroRIVERS / HydroBASINS** (`https://hydrosheds.org`) —
  river centrelines and basin polygons. Free for non-commercial use with
  attribution to WWF.
- **India-WRIS** river and basin layers — the India-official equivalent.
- **CWC gauge discharge** via India-WRIS — downstream boundary base flow.

The routing corridor is not taken from any of these: it is **derived** from
flow accumulation on the conditioned DEM and traced downstream from the snapped
dam point. That keeps the corridor consistent with the terrain the solver
actually runs on.

---

## Exposure layers

### OpenStreetMap via Overpass

| | |
| --- | --- |
| **Endpoint** | `https://overpass-api.de/api/interpreter`, with `overpass.kumi.systems` as a mirror |
| **Layers** | `building=*`, `highway=*`, `amenity=hospital\|clinic\|doctors`, `amenity=school\|college\|university`, `bridge=yes`, `place=city\|town\|village`, plus police, fire and power substations |
| **Licence** | © OpenStreetMap contributors, **ODbL 1.0**. Derived databases must be shared alike |

Overpass is a free, donated service. Raw responses are cached content-addressed
so a re-run costs nothing, and the demo works offline. One query per layer per
AOI, with mirror rotation and backoff — never a loop during a demo.

**What it cannot tell you.** OSM building coverage in rural India is uneven.
A low building count may mean few buildings or poor mapping, and the two are
not distinguishable from the data. Treat counts as a lower bound.

A Geofabrik India extract read with `pyrosm` is the offline fallback.

### WorldPop — population

| | |
| --- | --- |
| **Product** | Global 2000–2020 Constrained, UN-adjusted, India, 2020 |
| **URL** | `https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/BSGM/IND/` |
| **Resolution** | 100 m |
| **Units** | **persons per pixel**, not a density |
| **Licence** | CC BY 4.0. Cite Bondarenko M. et al., WorldPop, University of Southampton |

"Constrained" means population is only allocated where built settlement was
actually detected, which matters when summing inside a river valley.

**Units are the trap.** These are counts, not densities, so the correct zonal
statistic is a plain sum. Area-weighting them inflates the result by orders of
magnitude, and it is the classic error in flood exposure reporting.

**What it cannot tell you.** It is a modelled residential estimate, not a census
and not a casualty figure. It takes no account of time of day, seasonal
movement, or evacuation already under way. That note travels with every
population number into the PDF.

### GHSL GHS-POP — fallback

European Commission JRC, R2023A, 100 m, World Mollweide. CC BY 4.0. Cite
Schiavina, Freire & MacManus (2023).

### Land cover — not yet wired in

**ESA WorldCover 10 m v200** (CC BY 4.0) and **Bhuvan LULC** are the intended
sources for the agricultural-land metric and for a spatially varying Manning's
n. Neither is fetched yet, so agricultural area reports "not computed" and
roughness is uniform. Both facts are surfaced in the UI and the report rather
than filled with a plausible number.

---

## Satellite imagery and near-real-time detection

### Sentinel-1 GRD

| | |
| --- | --- |
| **Collection** | `COPERNICUS/S1_GRD` on Google Earth Engine |
| **Mode** | IW, VV/VH |
| **Revisit** | 6–12 days depending on coverage |
| **Licence** | Free and open under the Copernicus programme |

**What it cannot tell you.** It sees *open* water. Flooding under a forest
canopy or inside a built-up area is systematically under-detected, so a
Sentinel-1 extent is a lower bound. Radar shadow on steep terrain is as dark as
water, which is why local-incidence-angle masking is mandatory in Himalayan
scenes.

### JRC Global Surface Water

`JRC/GSW1_4/GlobalSurfaceWater`, occurrence band. Pekel et al. (2016), *Nature*
540, 418–422. Free with attribution. Used to exclude permanent water — without
it every river and reservoir in the scene is reported as flooding.

### CHIRPS daily rainfall

`UCSB-CHG/CHIRPS/DAILY`, 5 km. Funk et al. (2015), *Scientific Data*. Public
domain. Shown alongside the detected extent so the cause sits next to the
effect.

### Sentinel-2

`COPERNICUS/S2_SR_HARMONIZED` for a true-colour basemap and an NDWI
cross-check. Free and open under Copernicus. Cloud-limited, which is why
Sentinel-1 is primary.

---

## Reference and validation data

- **Wahl, T.L. (1998)**, *Prediction of Embankment Dam Breach Parameters*, USBR
  Dam Safety Report DSO-98-004 — observed Teton and Banqiao breach parameters.
- **Independent Panel to Review Cause of Teton Dam Failure (1976)** — Teton peak
  discharge.
- **Census of India 2011** — town populations in the bundled scenarios.
- **AIDR Handbook 7 (2017)**, *Managing the Floodplain*, Table 6.1 — flood
  hazard vulnerability classes.
- **Chow (1959)**, *Open-Channel Hydraulics*, Table 5-6 — Manning's n by
  surface type.

---

## Third-party code

Audited in `docs/AUDIT.md`. Licence position:

| Project | Licence | How it is used |
| --- | --- | --- |
| ANUGA | Apache-2.0 | Installed dependency, attribution in README and About |
| PySPH | BSD / MIT | Installed dependency; setup patterns copied with attribution |
| DualSPHysics | **LGPL-2.1** | **External binary invocation only. No source copied** |
| Delft3D | AGPL-3.0 / GPL-3.0 / LGPL-2.1 / BSD | **File formats read only. No source copied, no linking** |
| hydrobreach | none stated (team repo) | Modules ported with attribution; licence to be settled in writing before public release |

The DualSPHysics and Delft3D positions are the binding constraints. LGPL-2.1
permits use and dynamic linking but would impose obligations on copied source;
AGPL-3.0 on any linked part of Delft3D would reach this entire distribution.
FloodGuard therefore speaks to both only through files and argv.

---

## Reproducing a result

Every downloaded file is recorded in `data/MANIFEST.json` with its URL, SHA256,
size, licence and fetch time. `floodguard verify` re-hashes all of them and
reports any that drifted.

Every output — GeoTIFF, Shapefile, GeoJSON, KML, CSV, PDF — carries a
provenance block naming the DEM source and resolution, the dam parameters and
their citations, the engine and its version, the solver settings, the git
commit and a UTC timestamp.
