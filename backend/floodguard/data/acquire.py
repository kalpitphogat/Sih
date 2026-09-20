"""Phase 1 orchestration: `make data SCENARIO=...`.

Acquires every input layer a scenario needs, caches it, records it in
`data/MANIFEST.json`, and prints the layer table. Re-running downloads nothing.

The area of interest is derived before any download, because every source is
either billed by area (OpenTopography) or donated (Overpass), and requesting a
whole state when a corridor would do is both rude and slow.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from floodguard.data import dem as dem_mod
from floodguard.data import osm as osm_mod
from floodguard.data import population as pop_mod
from floodguard.data.cache import DownloadCache
from floodguard.scenario import Scenario

log = logging.getLogger(__name__)

KM_PER_DEG_LAT = 110.574


def km_per_deg_lon(lat_deg: float) -> float:
    return 111.320 * math.cos(math.radians(lat_deg))


def derive_aoi(scenario: Scenario) -> tuple[float, float, float, float]:
    """Area of interest as (west, south, east, north) in EPSG:4326.

    The AOI is the union of two boxes, because a dam-break domain has two
    halves with different requirements:

    * **Downstream**, we need every town we will report arrival times for, plus
      the lateral corridor the flood can spread across.
    * **Upstream**, we need the whole reservoir. This is easy to forget, and
      getting it wrong is silent: the pipeline runs, the map looks plausible,
      and the reservoir storage comes out wrong by orders of magnitude because
      most of the pool was never in the raster. `reservoir_buffer_km` is what
      guarantees the impoundment is inside the domain.

    An explicit `domain.bbox` in the scenario overrides all of this.
    """
    if scenario.domain.bbox:
        return scenario.domain.bbox

    def box(lats, lons, buffer_km):
        dlat = buffer_km / KM_PER_DEG_LAT
        dlon = buffer_km / km_per_deg_lon(scenario.dam.lat)
        return (min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat)

    # Upstream half: a square around the dam big enough to hold the pool.
    reservoir_box = box(
        [scenario.dam.lat], [scenario.dam.lon], scenario.domain.reservoir_buffer_km
    )

    # Downstream half: dam plus every named town, plus the lateral corridor.
    lats = [scenario.dam.lat] + [t.lat for t in scenario.towns]
    lons = [scenario.dam.lon] + [t.lon for t in scenario.towns]
    buffer_km = (
        scenario.domain.corridor_buffer_km
        if scenario.towns
        else scenario.domain.reach_length_km
    )
    corridor_box = box(lats, lons, buffer_km)

    return (
        round(min(reservoir_box[0], corridor_box[0]), 4),
        round(min(reservoir_box[1], corridor_box[1]), 4),
        round(max(reservoir_box[2], corridor_box[2]), 4),
        round(max(reservoir_box[3], corridor_box[3]), 4),
    )


@dataclass
class AcquisitionResult:
    """Everything Phase 1 produced, and what it failed to produce."""

    scenario_id: str
    aoi: tuple[float, float, float, float]
    aoi_area_km2: float
    dem: dem_mod.DemResult | None = None
    dem_mosaic: Path | None = None
    population: pop_mod.PopulationRaster | None = None
    osm_layers: dict[str, osm_mod.ExposureLayer] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    def provenance(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "aoi_wgs84": list(self.aoi),
            "aoi_area_km2": round(self.aoi_area_km2, 1),
            "osm_layers": {k: v.feature_count for k, v in self.osm_layers.items()},
            "skipped": self.skipped,
        }
        if self.dem:
            out |= self.dem.provenance()
        if self.dem_mosaic:
            out["dem_mosaic"] = str(self.dem_mosaic)
        if self.population:
            out["population_source"] = self.population.source
            out["population_assumption"] = self.population.assumption_note()
        return out


def acquire(
    scenario: Scenario,
    data_dir: Path,
    *,
    skip_population: bool = False,
    skip_osm: bool = False,
    mosaic: bool = True,
) -> AcquisitionResult:
    """Fetch every layer this scenario needs.

    A layer that cannot be fetched is recorded in `skipped` with the reason.
    The DEM is the one hard requirement: without it there is nothing to model,
    so a DEM failure raises rather than degrading.
    """
    cache = DownloadCache(data_dir)
    aoi = derive_aoi(scenario)
    area = dem_mod.bbox_area_km2(aoi)

    log.info(
        "scenario %s: AOI %s (%.0f km2)",
        scenario.id,
        ", ".join(f"{v:.3f}" for v in aoi),
        area,
    )

    result = AcquisitionResult(scenario_id=scenario.id, aoi=aoi, aoi_area_km2=area)

    # --- DEM: required ---
    result.dem = dem_mod.fetch_dem(
        cache,
        aoi,
        sources=scenario.dem.sources,
        local_path=scenario.dem.local_path,
    )
    log.info("DEM: %d tile(s) from %s", len(result.dem.tiles), result.dem.source)

    if mosaic:
        tile_paths = [
            cache.path_for(k.key)
            for k in cache.manifest.entries()
            if k.key.startswith("dem/") and k.path.endswith(".tif")
        ]
        tile_paths = [p for p in tile_paths if p and p.exists()]
        out = data_dir / "processed" / scenario.id / "dem_utm.tif"
        result.dem_mosaic = dem_mod.mosaic_and_clip(
            tile_paths,
            aoi,
            out,
            dst_crs=scenario.utm_crs,
            resolution_m=scenario.domain.resolution_m,
        )
        cache.register_local(
            f"processed/{scenario.id}/dem_utm",
            result.dem_mosaic,
            source=f"derived: mosaic+reproject+clip of {result.dem.source}",
            licence=result.dem.licence,
            attributes={
                "crs": scenario.utm_crs,
                "resolution_m": scenario.domain.resolution_m,
                "source_tiles": result.dem.tiles,
                "vertical_datum": "EGM2008 (orthometric)",
            },
        )
        log.info("DEM mosaic: %s", result.dem_mosaic)

    # --- Population: optional, degrades ---
    if skip_population:
        result.skipped["population"] = "skipped by request"
    else:
        try:
            result.population = pop_mod.fetch_population(cache, aoi)
            log.info("population: %s", result.population.source)
        except Exception as exc:  # noqa: BLE001
            result.skipped["population"] = f"{type(exc).__name__}: {exc}"
            log.error("population unavailable; impact will report it as not-computed: %s", exc)

    # --- OSM exposure: optional, degrades per layer ---
    if skip_osm:
        result.skipped["osm"] = "skipped by request"
    else:
        result.osm_layers = osm_mod.fetch_all(cache, aoi, scenario.id)
        for name in osm_mod.LAYER_QUERIES:
            if name not in result.osm_layers:
                result.skipped[f"osm/{name}"] = "fetch failed; see log"

    return result


def layer_table(data_dir: Path) -> str:
    """The manifest table printed at the end of `make data`."""
    return DownloadCache(data_dir).manifest.as_table()


def verify(data_dir: Path) -> list[str]:
    """Re-hash every cached file. Returns problems found; empty means clean."""
    cache = DownloadCache(data_dir)
    return cache.manifest.verify(data_dir)
