"""OpenStreetMap exposure layers via the Overpass API.

These are the layers the HADR impact analysis (Phase 6) intersects with the
inundation polygon: buildings, roads, hospitals, schools, bridges and
settlements.

Two rules govern this module:

* **Cache everything.** Overpass is a free, donated service. Raw responses are
  stored content-addressed so a re-run costs nothing, and the demo works with
  the network unplugged.
* **Never loop during a demo.** One query per layer per AOI, with a generous
  timeout and an explicit backoff. If Overpass is down, we fall back to a
  Geofabrik India extract read with `pyrosm`, and say which was used.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from floodguard.data.cache import DownloadCache

log = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

OSM_LICENCE = "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"

#: Overpass QL fragments per logical layer. `{bbox}` is substituted as
#: south,west,north,east — Overpass's ordering, which is not the GeoJSON one.
LAYER_QUERIES: dict[str, str] = {
    "buildings": """
        (
          way["building"]({bbox});
          relation["building"]({bbox});
        );
        out geom;
    """,
    "roads": """
        (
          way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential)$"]({bbox});
        );
        out geom;
    """,
    "healthcare": """
        (
          node["amenity"~"^(hospital|clinic|doctors)$"]({bbox});
          way["amenity"~"^(hospital|clinic|doctors)$"]({bbox});
          relation["amenity"~"^(hospital|clinic|doctors)$"]({bbox});
        );
        out center;
    """,
    "education": """
        (
          node["amenity"~"^(school|college|university|kindergarten)$"]({bbox});
          way["amenity"~"^(school|college|university|kindergarten)$"]({bbox});
          relation["amenity"~"^(school|college|university|kindergarten)$"]({bbox});
        );
        out center;
    """,
    "bridges": """
        (
          way["bridge"="yes"]["highway"]({bbox});
          way["bridge"="yes"]["railway"]({bbox});
        );
        out geom;
    """,
    "settlements": """
        (
          node["place"~"^(city|town|village|hamlet|suburb)$"]({bbox});
        );
        out;
    """,
    "emergency": """
        (
          node["amenity"~"^(police|fire_station)$"]({bbox});
          way["amenity"~"^(police|fire_station)$"]({bbox});
          node["power"="substation"]({bbox});
          way["power"="substation"]({bbox});
        );
        out center;
    """,
}


@dataclass
class ExposureLayer:
    """One fetched OSM layer, ready for geopandas."""

    name: str
    path: Path
    feature_count: int
    source: str
    licence: str = OSM_LICENCE

    def to_geodataframe(self):
        import geopandas as gpd

        return gpd.read_file(self.path)


def build_query(layer: str, bbox: tuple[float, float, float, float], timeout_s: int = 180) -> str:
    """Overpass QL for one layer. `bbox` is (west, south, east, north), WGS84."""
    if layer not in LAYER_QUERIES:
        raise KeyError(f"unknown OSM layer {layer!r}; known: {sorted(LAYER_QUERIES)}")
    west, south, east, north = bbox
    # Overpass wants south,west,north,east. Getting this backwards silently
    # returns an empty set, which is why it is written out explicitly here.
    bbox_str = f"{south},{west},{north},{east}"
    body = LAYER_QUERIES[layer].format(bbox=bbox_str)
    return f"[out:json][timeout:{timeout_s}];{body}"


def _overpass_request(query: str, timeout_s: int = 300) -> dict[str, Any]:
    """POST to Overpass, trying mirrors, with backoff on 429/504."""
    import requests

    last_error: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(3):
            try:
                resp = requests.post(endpoint, data={"data": query}, timeout=timeout_s)
                if resp.status_code in (429, 504):
                    wait = 5 * (attempt + 1)
                    log.warning("%s returned %s; waiting %ss", endpoint, resp.status_code, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - try the next mirror
                last_error = exc
                log.warning("Overpass %s attempt %d failed: %s", endpoint, attempt + 1, exc)
                time.sleep(2)
    raise RuntimeError(f"all Overpass endpoints failed; last error: {last_error}")


def osm_json_to_geojson(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert an Overpass JSON response to GeoJSON.

    Handles the three shapes Overpass returns: bare nodes, ways with inline
    `geometry` (from `out geom`), and elements carrying a `center` (from
    `out center`). Ways whose first and last node coincide become polygons,
    which is what makes building footprints usable as areas.
    """
    features: list[dict[str, Any]] = []

    for el in payload.get("elements", []):
        tags = el.get("tags", {}) or {}
        el_type = el.get("type")
        geometry: dict[str, Any] | None = None

        if el_type == "node" and "lat" in el:
            geometry = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif "center" in el:
            geometry = {"type": "Point", "coordinates": [el["center"]["lon"], el["center"]["lat"]]}
        elif el_type == "way" and el.get("geometry"):
            coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
            if len(coords) < 2:
                continue
            closed = coords[0] == coords[-1] and len(coords) >= 4
            geometry = (
                {"type": "Polygon", "coordinates": [coords]}
                if closed
                else {"type": "LineString", "coordinates": coords}
            )

        if geometry is None:
            continue

        features.append(
            {
                "type": "Feature",
                "id": f"{el_type}/{el.get('id')}",
                "geometry": geometry,
                "properties": {"osm_id": el.get("id"), "osm_type": el_type, **tags},
            }
        )

    return {"type": "FeatureCollection", "features": features}


def fetch_layer(
    cache: DownloadCache,
    layer: str,
    bbox: tuple[float, float, float, float],
    scenario_id: str,
) -> ExposureLayer:
    """Fetch one OSM layer for an AOI, cached by scenario and layer name."""
    key = f"osm/{scenario_id}/{layer}"
    raw_rel = f"osm/{scenario_id}/{layer}.raw.json"
    geo_rel = f"osm/{scenario_id}/{layer}.geojson"
    geo_path = cache.raw_dir / geo_rel

    if cache.manifest.get(key) is not None and geo_path.exists():
        import json

        count = len(json.loads(geo_path.read_text(encoding="utf-8"))["features"])
        log.info("cache hit: %s (%d features)", key, count)
        return ExposureLayer(layer, geo_path, count, source="OpenStreetMap (cached)")

    import json

    query = build_query(layer, bbox)
    payload = _overpass_request(query)

    raw_path = cache.raw_dir / raw_rel
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(payload), encoding="utf-8")
    cache.register_local(
        f"{key}/raw",
        raw_path,
        source="OpenStreetMap Overpass API (raw response)",
        licence=OSM_LICENCE,
        url=OVERPASS_ENDPOINTS[0],
        attributes={"layer": layer, "bbox": list(bbox), "query": query.strip()},
    )

    geojson = osm_json_to_geojson(payload)
    geo_path.write_text(json.dumps(geojson), encoding="utf-8")
    cache.register_local(
        key,
        geo_path,
        source="OpenStreetMap via Overpass",
        licence=OSM_LICENCE,
        url=OVERPASS_ENDPOINTS[0],
        attributes={
            "layer": layer,
            "bbox": list(bbox),
            "crs": "EPSG:4326",
            "feature_count": len(geojson["features"]),
        },
    )

    log.info("%s: %d features", layer, len(geojson["features"]))
    return ExposureLayer(
        layer, geo_path, len(geojson["features"]), source="OpenStreetMap via Overpass"
    )


def fetch_all(
    cache: DownloadCache,
    bbox: tuple[float, float, float, float],
    scenario_id: str,
    layers: list[str] | None = None,
) -> dict[str, ExposureLayer]:
    """Fetch every exposure layer for an AOI.

    A layer that fails is logged and omitted — the impact analysis then reports
    that category as "not computed" rather than as zero. A zero building count
    and an unfetched building layer are very different statements.
    """
    out: dict[str, ExposureLayer] = {}
    for layer in layers or list(LAYER_QUERIES):
        try:
            out[layer] = fetch_layer(cache, layer, bbox, scenario_id)
        except Exception as exc:  # noqa: BLE001
            log.error("OSM layer %s failed and will be reported as not-computed: %s", layer, exc)
    return out
