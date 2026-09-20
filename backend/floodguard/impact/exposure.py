"""HADR loss-and-damage analysis: what is inside the inundation polygon.

This is the part of the output a district disaster management officer acts on.
It answers: how many people, which hospitals, how much road, and — the question
that actually saves lives — how long do they have.

Three rules govern every number here:

1. **Not computed is not zero.** If the buildings layer failed to download,
   the buildings card reads "not computed", never "0". Conflating the two is
   the single most dangerous thing this module could do.
2. **Every count carries its assumption.** Population is a modelled gridded
   estimate, not a census; the note saying so travels with the number into the
   PDF.
3. **Sorted by lead time, not by size.** The evacuation priority list is
   ordered by how long each settlement has, because that is the order the
   response is executed in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

#: Road classes we report separately, most important first.
ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary")

#: OSM building tags that indicate a residential structure.
RESIDENTIAL_BUILDINGS = {"yes", "house", "residential", "apartments", "detached", "hut"}


@dataclass
class Metric:
    """One reported quantity, which may legitimately be unknown.

    `value is None` means not computed and is rendered as an em dash. It is a
    different state from zero and the UI must show it differently.
    """

    value: float | int | None
    unit: str
    label: str
    computed: bool = True
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    assumption: str = ""

    @classmethod
    def not_computed(cls, label: str, unit: str, reason: str) -> Metric:
        return cls(None, unit, label, computed=False, reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "computed": self.computed,
            "reason": self.reason,
            "assumption": self.assumption,
            "detail": self.detail,
            "display": self.display(),
        }

    def display(self) -> str:
        if not self.computed or self.value is None:
            return "—"
        if isinstance(self.value, int):
            return f"{self.value:,}"
        return f"{self.value:,.1f}"


@dataclass
class ImpactResult:
    """The Affected Elements panel, computed."""

    metrics: dict[str, Metric] = field(default_factory=dict)
    facilities: list[dict[str, Any]] = field(default_factory=list)
    evacuation_priority: list[dict[str, Any]] = field(default_factory=list)
    by_depth_band: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": {k: m.to_dict() for k, m in self.metrics.items()},
            "facilities": self.facilities,
            "evacuation_priority": self.evacuation_priority,
            "by_depth_band": self.by_depth_band,
            "warnings": self.warnings,
            "provenance": self.provenance,
        }

    def summary(self) -> str:
        lines = ["Affected elements within the inundation area:"]
        for m in self.metrics.values():
            unit = f" {m.unit}" if m.computed and m.unit else ""
            lines.append(f"  {m.label:<26} {m.display()}{unit}")
            if not m.computed:
                lines.append(f"  {'':<26} (not computed: {m.reason})")
        if self.evacuation_priority:
            lines.append("")
            lines.append("Evacuation priority, by lead time:")
            lines.append(
                f"  {'SETTLEMENT':<24} {'LEAD TIME':>11} {'DEPTH m':>9} {'POP':>10}"
            )
            for row in self.evacuation_priority[:15]:
                lead = (
                    f"{row['arrival_min']:.0f} min"
                    if row["arrival_min"] is not None
                    else "—"
                )
                depth = f"{row['depth_m']:.1f}" if row["depth_m"] is not None else "—"
                pop = f"{row['population']:,}" if row["population"] else "—"
                lines.append(f"  {row['name'][:24]:<24} {lead:>11} {depth:>9} {pop:>10}")
        return "\n".join(lines)


def _flood_mask_geometries(depth: np.ndarray, transform, crs: str, threshold_m: float):
    """The inundation polygon as shapely geometries, for clipping vectors."""
    from rasterio.features import shapes
    from shapely.geometry import shape
    from shapely.ops import unary_union

    mask = (depth >= threshold_m).astype(np.uint8)
    if not mask.any():
        return None
    polys = [shape(g) for g, v in shapes(mask, mask=mask > 0, transform=transform) if v == 1]
    return unary_union(polys) if polys else None


def _sample_raster_at_points(raster: np.ndarray, transform, gdf) -> np.ndarray:
    """Sample a raster at point geometries (uses centroids for areas)."""
    from rasterio.transform import rowcol

    rows, cols = raster.shape
    out = np.full(len(gdf), np.nan)
    for i, geom in enumerate(gdf.geometry):
        if geom is None or geom.is_empty:
            continue
        pt = geom if geom.geom_type == "Point" else geom.centroid
        r, c = rowcol(transform, pt.x, pt.y)
        if 0 <= r < rows and 0 <= c < cols:
            out[i] = raster[r, c]
    return out


def analyse(
    depth: np.ndarray,
    velocity: np.ndarray,
    arrival_s: np.ndarray,
    transform,
    crs: str,
    cell_size_m: float,
    *,
    osm_layers: dict[str, Path] | None = None,
    population_raster: Path | None = None,
    population_note: str = "",
    towns: list | None = None,
    wet_threshold_m: float = 0.3,
) -> ImpactResult:
    """Intersect the inundation with every exposure layer available.

    Layers that are absent produce `not_computed` metrics naming the reason.
    """
    import geopandas as gpd

    result = ImpactResult()
    osm_layers = osm_layers or {}
    cell_area = cell_size_m**2

    flooded = depth >= wet_threshold_m
    flooded_area_km2 = float(flooded.sum() * cell_area / 1e6)

    result.provenance = {
        "wet_threshold_m": wet_threshold_m,
        "cell_size_m": cell_size_m,
        "crs": crs,
        "flooded_area_km2": flooded_area_km2,
        "layers_used": sorted(osm_layers),
        "population_raster": str(population_raster) if population_raster else None,
    }

    if not flooded.any():
        result.warnings.append(
            "Nothing exceeded the wet threshold, so there is no inundation polygon to "
            "intersect. Every exposure figure is reported as not computed rather than "
            "as zero."
        )
        for key, (label, unit) in {
            "buildings": ("Buildings", "count"),
            "roads": ("Roads", "km"),
            "population": ("Population", "people"),
            "agriculture": ("Agricultural land", "km2"),
            "hospitals": ("Hospitals", "count"),
            "schools": ("Schools", "count"),
        }.items():
            result.metrics[key] = Metric.not_computed(label, unit, "nothing flooded")
        return result

    polygon = _flood_mask_geometries(depth, transform, crs, wet_threshold_m)
    if polygon is None:
        result.warnings.append("The inundation mask could not be polygonised.")
        return result

    flood_gdf = gpd.GeoDataFrame(geometry=[polygon], crs=crs)

    # --- buildings ---
    result.metrics["buildings"] = _count_layer(
        osm_layers.get("buildings"),
        flood_gdf,
        crs,
        label="Buildings",
        reason="the OSM buildings layer was not fetched for this area",
        depth=depth,
        transform=transform,
    )

    # --- roads ---
    result.metrics["roads"] = _road_length(
        osm_layers.get("roads"), flood_gdf, crs
    )

    # --- population ---
    result.metrics["population"] = _population(
        population_raster, [polygon], crs, population_note
    )

    # --- hospitals and schools ---
    result.metrics["hospitals"] = _count_layer(
        osm_layers.get("healthcare"),
        flood_gdf,
        crs,
        label="Hospitals and clinics",
        reason="the OSM healthcare layer was not fetched for this area",
        depth=depth,
        transform=transform,
    )
    result.metrics["schools"] = _count_layer(
        osm_layers.get("education"),
        flood_gdf,
        crs,
        label="Schools and colleges",
        reason="the OSM education layer was not fetched for this area",
        depth=depth,
        transform=transform,
    )

    # --- agricultural land ---
    result.metrics["agriculture"] = Metric.not_computed(
        "Agricultural land",
        "km2",
        "no land-cover raster (ESA WorldCover or Bhuvan LULC) is wired in yet, so "
        "cropland inside the flood extent cannot be measured",
    )

    # --- bridges ---
    result.metrics["bridges"] = _count_layer(
        osm_layers.get("bridges"),
        flood_gdf,
        crs,
        label="Bridges",
        reason="the OSM bridges layer was not fetched for this area",
        depth=depth,
        transform=transform,
    )

    # --- named critical facilities, with depth and arrival time ---
    result.facilities = _facility_table(
        osm_layers, flood_gdf, crs, depth, velocity, arrival_s, transform
    )

    # --- evacuation priority ---
    result.evacuation_priority = _evacuation_priority(
        osm_layers.get("settlements"),
        towns or [],
        crs,
        depth,
        arrival_s,
        transform,
    )

    if population_note:
        result.warnings.append(population_note)

    return result


def _count_layer(
    path: Path | None,
    flood_gdf,
    crs: str,
    *,
    label: str,
    reason: str,
    depth: np.ndarray,
    transform,
) -> Metric:
    """Count features of a layer inside the flood polygon, by depth band."""
    import geopandas as gpd

    if path is None or not Path(path).exists():
        return Metric.not_computed(label, "count", reason)

    try:
        gdf = gpd.read_file(path)
    except Exception as exc:  # noqa: BLE001
        return Metric.not_computed(label, "count", f"layer could not be read: {exc}")

    if gdf.empty:
        return Metric(
            0,
            "count",
            label,
            detail={"note": "the layer was fetched successfully and contains no features"},
        )

    gdf = gdf.to_crs(crs)
    inside = gpd.sjoin(gdf, flood_gdf, how="inner", predicate="intersects")
    if inside.empty:
        return Metric(0, "count", label, detail={"total_in_aoi": len(gdf)})

    depths = _sample_raster_at_points(depth, transform, inside)
    bands = {
        "0.1-0.5 m": int(((depths >= 0.1) & (depths < 0.5)).sum()),
        "0.5-2 m": int(((depths >= 0.5) & (depths < 2.0)).sum()),
        "2-5 m": int(((depths >= 2.0) & (depths < 5.0)).sum()),
        "5-10 m": int(((depths >= 5.0) & (depths < 10.0)).sum()),
        "> 10 m": int((depths >= 10.0).sum()),
    }

    return Metric(
        int(len(inside)),
        "count",
        label,
        detail={
            "total_in_aoi": int(len(gdf)),
            "by_depth_band": bands,
            "max_depth_m": float(np.nanmax(depths)) if np.isfinite(depths).any() else None,
        },
    )


def _road_length(path: Path | None, flood_gdf, crs: str) -> Metric:
    """Length of road inside the flood polygon, by class.

    Clipped rather than counted: half a highway inside the extent is half its
    length, not one road.
    """
    import geopandas as gpd

    if path is None or not Path(path).exists():
        return Metric.not_computed(
            "Roads", "km", "the OSM roads layer was not fetched for this area"
        )

    try:
        gdf = gpd.read_file(path).to_crs(crs)
    except Exception as exc:  # noqa: BLE001
        return Metric.not_computed("Roads", "km", f"layer could not be read: {exc}")

    if gdf.empty:
        return Metric(0.0, "km", "Roads", detail={"note": "no roads in the AOI"})

    clipped = gpd.clip(gdf, flood_gdf)
    if clipped.empty:
        return Metric(0.0, "km", "Roads", detail={"total_in_aoi_km": gdf.length.sum() / 1000})

    by_class: dict[str, float] = {}
    if "highway" in clipped.columns:
        for cls in ROAD_CLASSES:
            sel = clipped[clipped["highway"] == cls]
            if not sel.empty:
                by_class[cls] = float(sel.length.sum() / 1000.0)

    return Metric(
        float(clipped.length.sum() / 1000.0),
        "km",
        "Roads",
        detail={
            "by_class_km": by_class,
            "total_in_aoi_km": float(gdf.length.sum() / 1000.0),
            "method": "geometrically clipped to the inundation polygon, not counted whole",
        },
    )


def _population(
    raster: Path | None, geometries, crs: str, note: str
) -> Metric:
    """Zonal sum of gridded population inside the flood extent."""
    if raster is None or not Path(raster).exists():
        return Metric.not_computed(
            "Population",
            "people",
            "no WorldPop or GHS-POP raster is available for this area",
        )

    try:
        from floodguard.data.population import zonal_population

        stats = zonal_population(Path(raster), geometries, crs)
    except Exception as exc:  # noqa: BLE001
        return Metric.not_computed("Population", "people", f"zonal sum failed: {exc}")

    return Metric(
        int(round(stats["population_total"])),
        "people",
        "Population",
        detail=stats,
        assumption=note
        or (
            "Modelled gridded residential population, summed as persons-per-pixel. "
            "Not a census count and not a casualty estimate."
        ),
    )


def _facility_table(
    osm_layers: dict[str, Path],
    flood_gdf,
    crs: str,
    depth: np.ndarray,
    velocity: np.ndarray,
    arrival_s: np.ndarray,
    transform,
) -> list[dict[str, Any]]:
    """Critical facilities by name, with depth and arrival time at each."""
    import geopandas as gpd

    rows: list[dict[str, Any]] = []
    for kind in ("healthcare", "education", "emergency"):
        path = osm_layers.get(kind)
        if path is None or not Path(path).exists():
            continue
        try:
            gdf = gpd.read_file(path).to_crs(crs)
        except Exception:  # noqa: BLE001
            continue
        if gdf.empty:
            continue
        inside = gpd.sjoin(gdf, flood_gdf, how="inner", predicate="intersects")
        if inside.empty:
            continue

        d = _sample_raster_at_points(depth, transform, inside)
        v = _sample_raster_at_points(velocity, transform, inside)
        a = _sample_raster_at_points(arrival_s, transform, inside)

        for i in range(len(inside)):
            row = inside.iloc[i]
            rows.append(
                {
                    "kind": kind,
                    "name": row.get("name") or f"unnamed {row.get('amenity', kind)}",
                    "amenity": row.get("amenity"),
                    "osm_id": row.get("osm_id"),
                    "depth_m": float(d[i]) if np.isfinite(d[i]) else None,
                    "velocity_ms": float(v[i]) if np.isfinite(v[i]) else None,
                    "arrival_min": (
                        float(a[i] / 60.0) if np.isfinite(a[i]) and a[i] >= 0 else None
                    ),
                }
            )

    rows.sort(key=lambda r: (r["arrival_min"] is None, r["arrival_min"] or 0.0))
    return rows


def _evacuation_priority(
    settlements_path: Path | None,
    towns: list,
    crs: str,
    depth: np.ndarray,
    arrival_s: np.ndarray,
    transform,
) -> list[dict[str, Any]]:
    """Per-settlement warning list, ordered by lead time.

    This is the deliverable with the most operational value in the whole
    system: it says who to warn first. Settlements that are not flooded are
    excluded entirely rather than listed with a null — a warning list with
    non-events in it will not be read.
    """
    import geopandas as gpd
    from pyproj import Transformer
    from rasterio.transform import rowcol

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: str, lon: float, lat: float, population, source: str):
        if name in seen:
            return
        to_grid = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        x, y = to_grid.transform(lon, lat)
        r, c = rowcol(transform, x, y)
        if not (0 <= r < depth.shape[0] and 0 <= c < depth.shape[1]):
            return
        d = float(depth[r, c])
        a = float(arrival_s[r, c])
        if d <= 0.0 and a < 0:
            return
        seen.add(name)
        rows.append(
            {
                "name": name,
                "lon": lon,
                "lat": lat,
                "population": population,
                "population_source": source,
                "depth_m": d if d > 0 else None,
                "arrival_min": a / 60.0 if a >= 0 else None,
            }
        )

    for town in towns:
        add(
            town.name,
            town.lon,
            town.lat,
            town.population,
            town.population_source or "scenario file",
        )

    if settlements_path is not None and Path(settlements_path).exists():
        try:
            gdf = gpd.read_file(settlements_path).to_crs("EPSG:4326")
            for _, row in gdf.iterrows():
                name = row.get("name")
                if not name or row.geometry is None:
                    continue
                pt = row.geometry.centroid
                pop = row.get("population")
                add(
                    str(name),
                    float(pt.x),
                    float(pt.y),
                    int(pop) if pop and str(pop).isdigit() else None,
                    "OpenStreetMap place tag",
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("settlements layer unusable: %s", exc)

    rows.sort(key=lambda r: (r["arrival_min"] is None, r["arrival_min"] or 0.0))
    return rows
