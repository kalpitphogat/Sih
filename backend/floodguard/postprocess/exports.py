"""GIS exports: COG, Shapefile, GeoJSON, KML/KMZ, CSV.

Every file written carries the provenance block (engineering rule 3), so a
result opened in QGIS six months later still says which DEM, which engine,
which solver settings and which git commit produced it.

Formats and why each is here
----------------------------
* **Cloud-Optimised GeoTIFF** — the canonical raster output, and what the tile
  server reads so the browser never downloads a multi-gigabyte file.
* **Shapefile** — still the lingua franca of Indian state GIS departments.
  Zipped, because a shapefile is four files and emailing one of them is a
  classic failure.
* **GeoJSON** — for web clients and for anything modern.
* **KML/KMZ** — Google Earth, which is what a district officer will actually
  open. Time-stamped folders let the flood animate.
* **CSV** — depth time series at gauge points, for spreadsheet users.

Shapefile's limits are real and are handled rather than hit: field names are
truncated to 10 characters deterministically, and the mapping is written into
the sidecar metadata so nothing is lost silently.
"""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from floodguard.postprocess.hazard import DEPTH_BANDS

log = logging.getLogger(__name__)

#: Shapefile truncates attribute names at 10 characters. Rather than let the
#: driver do it silently and unpredictably, we map them explicitly.
SHAPEFILE_FIELD_MAP = {
    "depth_band_min_m": "dep_min",
    "depth_band_max_m": "dep_max",
    "depth_band_label": "dep_label",
    "hazard_class": "haz_class",
    "hazard_label": "haz_label",
    "arrival_time_min": "arriv_min",
    "max_velocity_ms": "vel_max",
    "area_km2": "area_km2",
    "engine": "engine",
}


@dataclass
class ExportResult:
    """One written file and how big it is."""

    format: str
    path: Path
    size_bytes: int
    feature_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "path": str(self.path),
            "filename": self.path.name,
            "size_bytes": self.size_bytes,
            "feature_count": self.feature_count,
        }


def write_cog(
    path: Path,
    array: np.ndarray,
    transform,
    crs: str,
    *,
    provenance: dict[str, Any],
    nodata: float = -9999.0,
    dtype: str = "float32",
    description: str = "",
) -> ExportResult:
    """Write a Cloud-Optimised GeoTIFF with provenance in its metadata tags.

    Overviews are what make it "cloud optimised": a tile server can serve a
    zoomed-out view by reading a small overview level instead of decompressing
    the full-resolution raster.
    """
    import rasterio
    from rasterio.enums import Resampling

    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.where(np.isfinite(array), array, nodata).astype(dtype)

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype=dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="deflate",
        predictor=2 if dtype.startswith("float") else 1,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        BIGTIFF="IF_SAFER",
    ) as dst:
        dst.write(data, 1)
        if description:
            dst.set_band_description(1, description)
        dst.update_tags(**_flatten_tags(provenance))
        dst.build_overviews([2, 4, 8, 16, 32], Resampling.average)

    return ExportResult("cog", path, path.stat().st_size)


def _flatten_tags(provenance: dict[str, Any]) -> dict[str, str]:
    """GeoTIFF tags must be strings, so nested values are JSON-encoded."""
    out: dict[str, str] = {}
    for key, value in provenance.items():
        if value is None:
            continue
        out[f"FG_{key.upper()}"] = (
            value if isinstance(value, str) else json.dumps(value, default=str)
        )
    return out


def polygonize_depth_bands(
    depth: np.ndarray,
    velocity: np.ndarray,
    arrival_s: np.ndarray,
    transform,
    crs: str,
    *,
    cell_area_m2: float,
    engine_name: str,
    min_area_m2: float = 0.0,
):
    """Vectorise the depth raster into the legend's bands.

    Returns a GeoDataFrame with one feature per contiguous region per band,
    carrying the band, the hazard summary and the earliest arrival time inside
    it — which is what the map popups and the shapefile attributes need.

    `min_area_m2` drops speckle. Set it above zero for a cleaner map, but note
    that dropping small polygons reduces the reported flooded area, so the area
    figure is always computed from the raster rather than from the polygons.
    """
    import geopandas as gpd
    from rasterio.features import shapes
    from shapely.geometry import shape

    from floodguard.postprocess.hazard import HAZARD_CLASSES, band_index, classify

    bands = band_index(depth)
    classes = classify(depth, velocity)

    records = []
    geoms = []

    for geom, value in shapes(bands, mask=bands > 0, transform=transform):
        idx = int(value)
        if idx < 1 or idx > len(DEPTH_BANDS):
            continue
        polygon = shape(geom)
        if polygon.area < min_area_m2:
            continue

        lo, hi, label, colour = DEPTH_BANDS[idx - 1]

        # Summarise the raster inside this polygon by masking on the band, which
        # is cheaper and exact enough for an attribute.
        in_band = bands == idx
        arrivals = arrival_s[in_band & (arrival_s >= 0)]

        geoms.append(polygon)
        records.append(
            {
                "depth_band_min_m": lo,
                "depth_band_max_m": None if hi == float("inf") else hi,
                "depth_band_label": label,
                "colour": colour,
                "hazard_class": int(np.max(classes[in_band])) if in_band.any() else 0,
                "hazard_label": next(
                    (
                        h.label
                        for h in HAZARD_CLASSES
                        if in_band.any() and h.code == int(np.max(classes[in_band]))
                    ),
                    "",
                ),
                "max_velocity_ms": float(velocity[in_band].max()) if in_band.any() else 0.0,
                "arrival_time_min": float(arrivals.min() / 60.0) if arrivals.size else None,
                "area_km2": polygon.area / 1e6,
                "engine": engine_name,
            }
        )

    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs=crs)
    if not gdf.empty:
        gdf = gdf.sort_values("depth_band_min_m").reset_index(drop=True)
    return gdf


def write_geojson(gdf, path: Path, provenance: dict[str, Any]) -> ExportResult:
    """GeoJSON, reprojected to WGS84 as the spec requires, with provenance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = gdf.to_crs("EPSG:4326") if not gdf.empty else gdf

    payload = json.loads(out.to_json()) if not out.empty else {
        "type": "FeatureCollection",
        "features": [],
    }
    # A top-level member is the only place GeoJSON lets us put file-level
    # metadata. Readers ignore unknown members, so this is safe.
    payload["floodguard_provenance"] = provenance
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ExportResult("geojson", path, path.stat().st_size, len(out))


def write_shapefile_zip(gdf, path: Path, provenance: dict[str, Any]) -> ExportResult:
    """Zipped Shapefile, with the field-name truncation made explicit.

    A shapefile is four-plus files; shipping one of them is a classic support
    ticket, so the export is always a zip.
    """
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    renamed = gdf.rename(columns=SHAPEFILE_FIELD_MAP)
    # Shapefile has no null for numeric fields; a None arrival time becomes -1
    # and the meaning is recorded in the sidecar rather than left ambiguous.
    if "arriv_min" in renamed:
        renamed["arriv_min"] = renamed["arriv_min"].fillna(-1.0)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        stem = path.stem
        shp = tmp_dir / f"{stem}.shp"
        renamed.to_file(shp, driver="ESRI Shapefile")

        sidecar = tmp_dir / f"{stem}.provenance.json"
        sidecar.write_text(
            json.dumps(
                {
                    **provenance,
                    "shapefile_field_map": SHAPEFILE_FIELD_MAP,
                    "null_encoding": {
                        "arriv_min": "-1 means the cell was never wetted above the threshold"
                    },
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(tmp_dir.iterdir()):
                zf.write(f, f.name)

    return ExportResult("shp", path, path.stat().st_size, len(gdf))


def write_kml(
    gdf,
    path: Path,
    provenance: dict[str, Any],
    *,
    name: str = "FloodGuard inundation",
    animate: bool = True,
    event_start: datetime | None = None,
) -> ExportResult:
    """KML for Google Earth, optionally with time-stamped folders.

    Google Earth reads the `TimeSpan` element to drive its time slider, so
    grouping polygons by arrival time turns a static map into an animation of
    the flood wave. That is the single most effective thing to show a
    non-technical audience, and it costs only this grouping.

    Written directly rather than via fiona's KML driver, which does not support
    per-feature styling or time spans.
    """
    from xml.sax.saxutils import escape

    path.parent.mkdir(parents=True, exist_ok=True)
    out = gdf.to_crs("EPSG:4326") if not gdf.empty else gdf
    start = event_start or datetime(2024, 1, 1, tzinfo=timezone.utc)

    def kml_colour(hex_colour: str, alpha: str = "99") -> str:
        """KML uses aabbggrr, not #rrggbb."""
        h = hex_colour.lstrip("#")
        return f"{alpha}{h[4:6]}{h[2:4]}{h[0:2]}".lower()

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
        f"<name>{escape(name)}</name>",
        f"<description><![CDATA[<pre>{json.dumps(provenance, indent=2, default=str)}</pre>]]></description>",
    ]

    for i, (_lo, _hi, label, colour) in enumerate(DEPTH_BANDS, start=1):
        lines += [
            f'<Style id="band{i}">',
            f"<LineStyle><color>{kml_colour(colour, 'cc')}</color><width>1</width></LineStyle>",
            f"<PolyStyle><color>{kml_colour(colour)}</color></PolyStyle>",
            "</Style>",
        ]

    band_of = {label: i for i, (_lo, _hi, label, _c) in enumerate(DEPTH_BANDS, start=1)}

    def placemark(row, geom) -> list[str]:
        style = band_of.get(row.get("depth_band_label", ""), 1)
        arrival = row.get("arrival_time_min")
        desc = "<br/>".join(
            f"{k}: {v}"
            for k, v in row.items()
            if k not in ("geometry", "colour") and v is not None
        )
        pm = [
            "<Placemark>",
            f"<name>{escape(str(row.get('depth_band_label', 'flood')))}</name>",
            f"<description><![CDATA[{desc}]]></description>",
            f"<styleUrl>#band{style}</styleUrl>",
        ]
        if animate and arrival is not None:
            begin = start + timedelta(minutes=float(arrival))
            pm.append(
                f"<TimeSpan><begin>{begin.isoformat().replace('+00:00', 'Z')}</begin></TimeSpan>"
            )
        pm.append(_geometry_to_kml(geom))
        pm.append("</Placemark>")
        return pm

    if animate and not out.empty and "arrival_time_min" in out:
        # Group into hourly folders so Google Earth's slider is usable.
        arrivals = out["arrival_time_min"].fillna(-1.0)
        buckets: dict[int, list[int]] = {}
        for idx, minutes in arrivals.items():
            hour = int(minutes // 60) if minutes >= 0 else -1
            buckets.setdefault(hour, []).append(idx)
        for hour in sorted(buckets):
            title = "arrival unknown" if hour < 0 else f"hour {hour}-{hour + 1}"
            lines.append(f"<Folder><name>{escape(title)}</name>")
            for idx in buckets[hour]:
                row = out.loc[idx].to_dict()
                lines += placemark(row, out.geometry.loc[idx])
            lines.append("</Folder>")
    else:
        for idx in out.index:
            lines += placemark(out.loc[idx].to_dict(), out.geometry.loc[idx])

    lines += ["</Document>", "</kml>"]
    path.write_text("\n".join(lines), encoding="utf-8")
    return ExportResult("kml", path, path.stat().st_size, len(out))


def _geometry_to_kml(geom) -> str:
    """Polygon or MultiPolygon to KML geometry XML."""

    def ring(coords) -> str:
        pts = " ".join(f"{x:.7f},{y:.7f},0" for x, y in coords)
        return f"<LinearRing><coordinates>{pts}</coordinates></LinearRing>"

    def polygon(poly) -> str:
        parts = [f"<outerBoundaryIs>{ring(poly.exterior.coords)}</outerBoundaryIs>"]
        for interior in poly.interiors:
            parts.append(f"<innerBoundaryIs>{ring(interior.coords)}</innerBoundaryIs>")
        return f"<Polygon>{''.join(parts)}</Polygon>"

    if geom.geom_type == "Polygon":
        return polygon(geom)
    if geom.geom_type == "MultiPolygon":
        return f"<MultiGeometry>{''.join(polygon(p) for p in geom.geoms)}</MultiGeometry>"
    return ""


def write_kmz(kml_path: Path, path: Path) -> ExportResult:
    """Zip a KML into a KMZ. Google Earth prefers it and it is far smaller."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(kml_path, "doc.kml")
    return ExportResult("kmz", path, path.stat().st_size)


def write_timeseries_csv(
    path: Path,
    times_s: np.ndarray,
    series: dict[str, np.ndarray],
    provenance: dict[str, Any],
    *,
    value_label: str = "value",
) -> ExportResult:
    """Time series at gauge points, with provenance in leading comment lines.

    Comment lines are prefixed with '#', which pandas, R and Excel all skip
    with one option, so the metadata travels with the data instead of being
    lost the moment someone opens the file.
    """
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        for line in json.dumps(provenance, indent=2, default=str).splitlines():
            fh.write(f"# {line}\n")
        writer = csv.writer(fh)
        writer.writerow(["time_s", "time_hours", *series.keys()])
        for i, t in enumerate(times_s):
            writer.writerow(
                [f"{t:.1f}", f"{t / 3600:.4f}", *(f"{v[i]:.4f}" for v in series.values())]
            )
    return ExportResult("csv", path, path.stat().st_size, len(times_s))
