"""Population exposure rasters.

WorldPop 100 m constrained is the primary source because it is the finest
open gridded population product covering India, and because "constrained"
means population is only allocated to cells where built settlement was
actually detected — which matters when we sum inside a river valley.

GHSL GHS-POP is the fallback: coarser (100 m / 1 km, Mollweide) but stable and
mirrored widely.

Neither product is a census. Whatever we report carries the assumption note
returned by `assumption_note()`, and that note must reach the PDF report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from floodguard.data.cache import DownloadCache, with_fallbacks

log = logging.getLogger(__name__)

#: WorldPop 2020 constrained, 100 m, UN-adjusted, India.
WORLDPOP_IND_100M = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/BSGM/IND/"
    "ind_ppp_2020_UNadj_constrained.tif"
)
WORLDPOP_LICENCE = (
    "WorldPop (www.worldpop.org), University of Southampton. CC BY 4.0. "
    "Cite: Bondarenko M. et al., Global Constrained Population 2020."
)

#: GHSL GHS-POP R2023A, 100 m, Mollweide, 2020 epoch. Tiled globally.
GHSL_POP_BASE = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/"
    "GHS_POP_E2020_GLOBE_R2023A_54009_100/V1-0/tiles"
)
GHSL_LICENCE = (
    "GHSL GHS-POP R2023A, European Commission Joint Research Centre. CC BY 4.0. "
    "Cite: Schiavina M., Freire S., MacManus K. (2023)."
)


@dataclass
class PopulationRaster:
    path: Path
    source: str
    licence: str
    resolution_m: float
    crs: str
    epoch: int
    failures: list[str]

    def assumption_note(self) -> str:
        """The caveat that must accompany every population number we report."""
        return (
            f"Population figures are a zonal sum of {self.source} ({self.epoch}, "
            f"{self.resolution_m:.0f} m gridded estimates), not a census count. They "
            f"represent modelled residential population and take no account of time of "
            f"day, seasonal movement, or evacuation already under way. Treat them as an "
            f"order-of-magnitude exposure estimate for planning, not as a casualty figure."
        )


def _fetch_worldpop(cache: DownloadCache) -> Path | None:
    """Fetch the national WorldPop raster once; it is clipped per scenario later.

    The file is roughly 1-2 GB for India, which is why it is fetched once and
    cached rather than per-scenario.
    """
    return cache.fetch(
        "population/worldpop_ind_2020_constrained",
        WORLDPOP_IND_100M,
        "population/worldpop/ind_ppp_2020_UNadj_constrained.tif",
        source="WorldPop 2020 constrained UN-adjusted (India)",
        licence=WORLDPOP_LICENCE,
        attributes={
            "resolution_m": 100,
            "crs": "EPSG:4326",
            "epoch": 2020,
            "product": "Global_2000_2020_Constrained BSGM",
            "units": "persons per pixel",
        },
        timeout=1800.0,
    )


def _fetch_ghsl_tile(cache: DownloadCache, tile: str) -> Path | None:
    """Fetch one GHS-POP tile, e.g. 'R7_C26' covering northern India."""
    name = f"GHS_POP_E2020_GLOBE_R2023A_54009_100_V1_0_{tile}"
    url = f"{GHSL_POP_BASE}/{name}.zip"
    return cache.fetch(
        f"population/ghsl/{tile}",
        url,
        f"population/ghsl/{name}.zip",
        source="GHSL GHS-POP R2023A",
        licence=GHSL_LICENCE,
        attributes={
            "resolution_m": 100,
            "crs": "ESRI:54009 (World Mollweide)",
            "epoch": 2020,
            "tile": tile,
            "units": "persons per pixel",
        },
        timeout=900.0,
    )


def fetch_population(
    cache: DownloadCache,
    bbox: tuple[float, float, float, float],
    *,
    ghsl_tile: str | None = None,
) -> PopulationRaster:
    """Acquire a population raster covering `bbox`, WorldPop first."""
    attempts: list[tuple[str, object]] = [("worldpop", lambda: _fetch_worldpop(cache))]
    if ghsl_tile:
        attempts.append(("ghsl", lambda: _fetch_ghsl_tile(cache, ghsl_tile)))

    path, used, failures = with_fallbacks(attempts, what="population")

    if used == "worldpop":
        return PopulationRaster(
            path=path,
            source="WorldPop constrained UN-adjusted",
            licence=WORLDPOP_LICENCE,
            resolution_m=100.0,
            crs="EPSG:4326",
            epoch=2020,
            failures=failures,
        )
    return PopulationRaster(
        path=path,
        source="GHSL GHS-POP R2023A",
        licence=GHSL_LICENCE,
        resolution_m=100.0,
        crs="ESRI:54009",
        epoch=2020,
        failures=failures,
    )


def zonal_population(
    population_path: Path,
    mask_geometries,
    mask_crs: str,
) -> dict[str, float]:
    """Sum population inside a set of geometries.

    WorldPop and GHS-POP store *persons per pixel*, not a density, so the
    correct zonal statistic is a plain sum — no area weighting. Getting this
    wrong by treating it as density is the classic error in flood exposure
    reporting, and it inflates the number by orders of magnitude.

    Partial pixels at the polygon edge are included when their centre falls
    inside (`all_touched=False`), which slightly under-counts rather than
    over-counts. That direction is deliberate.
    """
    import numpy as np
    import rasterio
    from rasterio.mask import mask as rio_mask
    from rasterio.warp import transform_geom

    with rasterio.open(population_path) as src:
        geoms = [
            transform_geom(mask_crs, src.crs.to_string(), geom.__geo_interface__)
            if mask_crs != src.crs.to_string()
            else geom.__geo_interface__
            for geom in mask_geometries
        ]
        clipped, _ = rio_mask(src, geoms, crop=True, all_touched=False, filled=True, nodata=0)
        nodata = src.nodata

    values = clipped[0].astype("float64")
    if nodata is not None:
        values[values == nodata] = 0.0
    # WorldPop uses large negative sentinels in places; anything negative is not
    # a population count.
    values[values < 0] = 0.0

    return {
        "population_total": float(np.nansum(values)),
        "populated_cells": int(np.count_nonzero(values > 0)),
        "max_cell_population": float(np.nanmax(values)) if values.size else 0.0,
        "statistic": "sum of persons-per-pixel (not density-weighted)",
    }
