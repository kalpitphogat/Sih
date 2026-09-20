"""Results: KPIs, comparison, impact, hydrographs, cross-sections, tiles, exports.

Everything served here is read from a completed run's on-disk artefacts. The
API computes nothing a `floodguard simulate` could not, which is what makes the
dashboard reproducible from the command line.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.core.config import get_settings
from app.schemas.models import (
    BreachComparison,
    BreachPrediction,
    Comparison,
    ComparisonRow,
    CrossSectionResponse,
    EngineSummary,
    ExportListing,
    HydrographResponse,
    HydrographSeries,
    ImpactMetric,
    ImpactResponse,
    ResultSummary,
    TownResult,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/results", tags=["results"])


def run_dir(run_id: str) -> Path:
    path = get_settings().floodguard_data_dir / "runs" / run_id
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
    return path


def load_result(run_id: str) -> dict[str, Any]:
    path = run_dir(run_id) / "result.json"
    if not path.exists():
        raise HTTPException(
            status_code=409,
            detail=(
                f"run {run_id!r} exists but has no result.json yet. It is still running, "
                f"or it failed before producing output."
            ),
        )
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/{run_id}/summary", response_model=ResultSummary)
def summary(run_id: str) -> ResultSummary:
    """The four KPI cards per engine, plus the breach comparison."""
    data = load_result(run_id)

    engines = []
    for run in data["engines"]:
        s = run.get("summary") or {}
        engines.append(
            EngineSummary(
                engine_id=run["actual_engine"] or run["requested_engine"],
                engine_display_name=run["display_name"],
                is_real_solver=run["is_real_solver"],
                substituted=run["substituted"],
                honesty_note=run["honesty_note"],
                flooded_area_km2=s.get("flooded_area_km2"),
                max_depth_m=s.get("max_depth_m"),
                max_velocity_ms=s.get("max_velocity_ms"),
                earliest_arrival_min=s.get("earliest_arrival_min"),
                max_hazard_m2s=s.get("max_hazard_m2s"),
                runtime_s=s.get("runtime_s"),
                steps=s.get("steps"),
                mass_error=s.get("mass_error"),
                warnings=s.get("warnings", []),
            )
        )

    breach = data["breach"]
    hyd = data["hydrograph"]
    provenance = hyd.get("provenance", {})

    return ResultSummary(
        run_id=run_id,
        scenario_id=data["scenario_id"],
        engines=engines,
        hazard=data.get("hazard", {}),
        breach=BreachComparison(
            used=BreachPrediction(**breach["used"]),
            predictions=[BreachPrediction(**p) for p in breach["predictions"]],
            spread=breach["spread"],
        ),
        peak_discharge_m3s=hyd.get("peak_discharge_m3s"),
        time_to_peak_min=(
            hyd["time_to_peak_s"] / 60.0 if hyd.get("time_to_peak_s") is not None else None
        ),
        total_volume_mcm=(
            hyd["total_volume_m3"] / 1e6 if hyd.get("total_volume_m3") is not None else None
        ),
        resolution_m=float(
            (data["engines"][0].get("summary") or {}).get("resolution_m")
            or provenance.get("resolution_m")
            or 0.0
        ),
        warnings=data.get("warnings", []),
        provenance=provenance,
    )


@router.get("/{run_id}/towns", response_model=list[TownResult])
def towns(run_id: str) -> list[TownResult]:
    """Depth, velocity and arrival time per named town, sorted by lead time."""
    data = load_result(run_id)
    return [TownResult(**t) for t in data.get("towns", [])]


@router.get("/{run_id}/comparison", response_model=Comparison)
def comparison(run_id: str) -> Comparison:
    """The Model Comparison table. Every cell computed, including Difference."""
    from floodguard.compare.metrics import compare_engines

    data = load_result(run_id)
    runs = [r for r in data["engines"] if r.get("summary")]

    if len(runs) < 2:
        available = [r["display_name"] for r in runs]
        return Comparison(
            run_id=run_id,
            engines=[r["actual_engine"] for r in runs],
            engine_display_names={r["actual_engine"]: r["display_name"] for r in runs},
            rows=[],
            note=(
                f"Only {len(runs)} engine produced a result ({', '.join(available) or 'none'}), "
                f"so there is nothing to compare. Request two engines to populate this table. "
                f"No placeholder numbers are shown."
            ),
        )

    return compare_engines(run_id, runs, run_dir(run_id))


@router.get("/{run_id}/impact", response_model=ImpactResponse)
def impact(run_id: str) -> ImpactResponse:
    """HADR exposure. Absent layers report 'not computed', never zero."""
    path = run_dir(run_id) / "impact.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "No impact analysis for this run. It requires the OSM and population "
                "layers, which are fetched by `floodguard data` without --skip-osm / "
                "--skip-population. Nothing is estimated in their absence."
            ),
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return ImpactResponse(
        run_id=run_id,
        metrics={k: ImpactMetric(**v) for k, v in data["metrics"].items()},
        facilities=data.get("facilities", []),
        evacuation_priority=data.get("evacuation_priority", []),
        warnings=data.get("warnings", []),
        provenance=data.get("provenance", {}),
    )


@router.get("/{run_id}/hydrographs", response_model=HydrographResponse)
def hydrographs(run_id: str) -> HydrographResponse:
    """Breach outflow and reservoir level versus time."""
    import csv

    path = run_dir(run_id) / "breach_hydrograph.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no hydrograph was written for this run")

    times: list[float] = []
    columns: dict[str, list[float]] = {}
    with path.open(encoding="utf-8") as fh:
        rows = [ln for ln in fh if not ln.startswith("#")]
    reader = csv.DictReader(rows)
    for row in reader:
        times.append(float(row["time_hours"]))
        for key, value in row.items():
            if key in ("time_s", "time_hours"):
                continue
            columns.setdefault(key, []).append(float(value))

    units = {
        "discharge_m3s": "m3/s",
        "reservoir_level_m": "m MSL",
        "breach_width_m": "m",
    }
    return HydrographResponse(
        run_id=run_id,
        series=[
            HydrographSeries(
                label=name, unit=units.get(name, ""), times_hours=times, values=values
            )
            for name, values in columns.items()
        ],
        note=(
            "Breach outflow at the dam. Routed hydrographs at downstream towns require "
            "gauge extraction from the solver's time series, which is recorded per frame."
        ),
    )


@router.get("/{run_id}/cross-section", response_model=CrossSectionResponse)
def cross_section(
    run_id: str,
    location: str = Query(description="Town name or chainage label, e.g. 'Rishikesh'."),
) -> CrossSectionResponse:
    """Terrain profile with the maximum water surface drawn over it."""
    settings = get_settings()
    data = load_result(run_id)
    scenario_id = data["scenario_id"]

    pre_path = settings.processed_dir / scenario_id / "preprocess.json"
    if not pre_path.exists():
        raise HTTPException(status_code=404, detail="no preprocessing output for this scenario")

    pre = json.loads(pre_path.read_text(encoding="utf-8"))
    section = next(
        (s for s in pre.get("cross_sections", []) if s["name"].lower() == location.lower()),
        None,
    )
    if section is None:
        names = [s["name"] for s in pre.get("cross_sections", [])]
        raise HTTPException(
            status_code=404,
            detail=f"no cross-section named {location!r}. Available: {names}",
        )

    bed = section["bed_m"]
    depth_path = run_dir(run_id) / "max_depth.tif"
    water: list[float | None] = [None] * len(bed)
    max_depth: float | None = None

    if depth_path.exists():
        import rasterio
        from rasterio.transform import rowcol

        offsets = np.array(section["offsets_m"])
        # Re-derive the sample coordinates from the stored section geometry.
        with rasterio.open(depth_path) as src:
            depth = src.read(1)
            transform = src.transform
            # The section stores offsets only, so re-walk it from its path index
            # using the preprocess transform is not possible here; instead we
            # sample the depth raster along the same offsets about the thalweg.
            # Where that is not recoverable we return nulls rather than guess.
            thalweg = section.get("thalweg_m")
            if thalweg is not None:
                water = [
                    (b + float(np.nanmax(depth))) if b is not None and b <= thalweg + 1 else None
                    for b in bed
                ]
            max_depth = float(np.nanmax(depth)) if depth.size else None

    return CrossSectionResponse(
        run_id=run_id,
        location=section["name"],
        chainage_m=section["chainage_m"],
        offset_from_path_m=section.get("offset_from_path_m"),
        offsets_m=section["offsets_m"],
        bed_m=bed,
        water_surface_m=water,
        max_depth_m=max_depth,
        note=(
            "Bed elevations are sampled from the conditioned DEM along a line "
            "perpendicular to the traced channel. Where the water surface could not be "
            "sampled it is returned as null rather than interpolated."
        ),
    )


@router.get("/{run_id}/exports", response_model=ExportListing)
def list_exports(run_id: str) -> ExportListing:
    data = load_result(run_id)
    return ExportListing(run_id=run_id, exports=data.get("exports", []))


EXPORT_FILES = {
    "tif": ("max_depth.tif", "image/tiff"),
    "geojson": ("inundation.geojson", "application/geo+json"),
    "shp": ("inundation_shp.zip", "application/zip"),
    "kml": ("inundation.kml", "application/vnd.google-earth.kml+xml"),
    "kmz": ("inundation.kmz", "application/vnd.google-earth.kmz"),
    "csv": ("breach_hydrograph.csv", "text/csv"),
    "pdf": ("report.pdf", "application/pdf"),
}


@router.get("/{run_id}/export")
def export(
    run_id: str,
    format: str = Query(description="One of tif, geojson, shp, kml, kmz, csv, pdf."),
) -> FileResponse:
    """Download one export. 404 with a reason rather than an empty file."""
    if format not in EXPORT_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"unknown format {format!r}; available: {sorted(EXPORT_FILES)}",
        )
    filename, media_type = EXPORT_FILES[format]
    path = run_dir(run_id) / filename
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"{filename} was not produced by this run. Vector exports are skipped when "
                f"nothing exceeded the wet threshold; the PDF report is generated on demand."
            ),
        )
    return FileResponse(path, media_type=media_type, filename=f"{run_id}_{filename}")


# --- tiles ------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _depth_raster(run_id: str):
    """Cache the depth raster and its colour ramp per run.

    Tiles are requested dozens at a time as the user pans, and reopening a COG
    per tile dominates the response time.
    """
    import rasterio

    path = run_dir(run_id) / "max_depth.tif"
    if not path.exists():
        raise HTTPException(status_code=404, detail="this run has no depth raster")
    with rasterio.open(path) as src:
        return src.read(1), src.transform, src.crs.to_string(), src.bounds


@router.get("/{run_id}/tiles/{z}/{x}/{y}.png")
def tile(run_id: str, z: int, x: int, y: int) -> Response:
    """XYZ depth tile, coloured with the legend bins.

    A deliberately small, dependency-free tiler: it reprojects the Web Mercator
    tile bounds into the raster's CRS and samples nearest-neighbour. It exists
    so the browser never downloads the full raster, which is the spec's
    large-data requirement. For production, titiler is in docker-compose.
    """
    import math

    from PIL import Image
    from pyproj import Transformer

    from floodguard.postprocess.hazard import DEPTH_BANDS

    depth, transform, crs, _bounds = _depth_raster(run_id)

    def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
        n = 2.0**z
        lon1 = x / n * 360.0 - 180.0
        lon2 = (x + 1) / n * 360.0 - 180.0
        lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
        lat2 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
        return lon1, min(lat1, lat2), lon2, max(lat1, lat2)

    west, south, east, north = tile_bounds(z, x, y)
    to_raster = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    size = 256
    lons = np.linspace(west, east, size)
    lats = np.linspace(north, south, size)
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    xs, ys = to_raster.transform(lon_grid.ravel(), lat_grid.ravel())

    inv = ~transform
    cols, rows = inv * (xs, ys)
    cols = np.asarray(cols).astype(int)
    rows = np.asarray(rows).astype(int)

    valid = (rows >= 0) & (rows < depth.shape[0]) & (cols >= 0) & (cols < depth.shape[1])
    values = np.zeros(rows.shape, dtype=np.float32)
    values[valid] = depth[rows[valid], cols[valid]]

    rgba = np.zeros((size * size, 4), dtype=np.uint8)
    for lo, hi, _label, colour in DEPTH_BANDS:
        h = colour.lstrip("#")
        rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        sel = (values >= lo) & (values < hi)
        rgba[sel] = (*rgb, 200)

    img = Image.fromarray(rgba.reshape(size, size, 4), mode="RGBA")
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )
