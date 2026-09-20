"""Build `data/catalog/dams.geojson` from the CWC NRLD-2019 register.

Source of record: the National Register of Large Dams, Central Water Commission.
    https://cwc.gov.in/sites/default/files/nrld-2019.pdf   (attributes, coordinates)
    https://cwc.gov.in/sites/default/files/nrld06042018.pdf (dam-type legend)

The transcription used here — including the per-dam page citation in the NRLD
PDF for every attribute — was done in the team's `hydrobreach` repository. We
carry the citations across verbatim rather than re-keying them, so that every
number in the dropdown can be traced to a page of a government PDF.

What this script deliberately does NOT do: invent Full Reservoir Level. The
NRLD tables transcribed here carry dam height and storage but not FRL/MDDL, so
those fields are written as null with an explicit note. The reservoir
elevation-area-capacity curve derived from the DEM in Phase 2 is what supplies
the operating levels, and the derived gross storage is cross-checked against
the NRLD value recorded here.

    python scripts/build_dam_catalog.py
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CSV = REPO_ROOT / "reference" / "hydrobreach" / "hydrobreach" / "data" / "dams" / "india_dams.csv"
OUT_PATH = REPO_ROOT / "data" / "catalog" / "dams.geojson"

#: NRLD dam-type strings mapped onto floodguard.scenario.DamType.
#: NRLD's "embankment" covers earth and rockfill; where the register's 2018 and
#: 2019 editions disagree the source CSV's `notes` column records the reasoning.
DAM_TYPE_MAP = {
    "embankment": "earthfill",
    "concrete_gravity": "concrete_gravity",
    "concrete_arch": "concrete_arch",
    "masonry": "masonry",
    "rockfill": "rockfill",
}

#: Dams SPEC.md names that the NRLD transcription does not yet cover.
#: Recorded here rather than silently omitted.
KNOWN_GAPS = {
    "Idukki": (
        "Kerala, Periyar river, double-curvature concrete arch. Not present in the "
        "NRLD-2019 transcription we inherited. To add it, transcribe the Kerala "
        "state sheet of nrld-2019.pdf with the same per-field citations."
    ),
}


def slugify(name: str) -> str:
    """Stable, URL-safe dam id used by the API and scenario files."""
    keep = [c.lower() if c.isalnum() else "_" for c in name]
    out = "".join(keep)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def build_feature(row: dict[str, str]) -> dict:
    """One GeoJSON feature, with per-field provenance attached."""
    nrld_url = row["src_attrs_url"]
    locator = row["src_attrs_locator"]
    attr_citation = f"{row['src_attrs_edition']} — {locator} — {nrld_url}"
    coord_citation = f"{row['src_coords_name']} — {row['src_coords_url']}"
    type_citation = f"{row['src_type_name']} — {row['src_type_url']}"

    def num(key: str) -> float | None:
        raw = (row.get(key) or "").strip()
        return float(raw) if raw else None

    gross_m3 = num("gross_storage_m3")
    live_m3 = num("live_storage_m3")
    area_m2 = num("reservoir_area_m2")

    notes = [n for n in (row.get("notes") or "").split(";") if n]
    # The source CSV states this assumption explicitly; we carry it forward
    # rather than letting a 0.9*height figure look like a published stage.
    if row.get("water_depth_basis") == "assumed_0.9x_height_no_published_stage":
        notes.append(
            "No published FRL/MDDL in the NRLD transcription: frl_m and mddl_m are null. "
            "Operating levels come from the DEM-derived elevation-area-capacity curve "
            "(Phase 2) and are cross-checked against gross_storage_mcm recorded here."
        )

    props = {
        "id": slugify(row["name"]),
        "nrld_id": row["id"],
        "name": row["name"].strip(),
        "river": row["river"].strip(),
        "state": row["state"].strip(),
        "lon": float(row["lon"]),
        "lat": float(row["lat"]),
        "dam_type": DAM_TYPE_MAP.get(row["dam_type"], row["dam_type"]),
        "structural_height_m": num("height_m"),
        "crest_length_m": num("crest_length_m"),
        # NRLD does not publish crest elevation in these tables.
        "crest_elevation_m": None,
        "frl_m": None,
        "mddl_m": None,
        "gross_storage_mcm": gross_m3 / 1e6 if gross_m3 else None,
        "live_storage_mcm": live_m3 / 1e6 if live_m3 else None,
        "reservoir_area_km2": area_m2 / 1e6 if area_m2 else None,
        "spillway_capacity_m3s": None,
        "commissioned_year": int(row["year"]) if row.get("year") else None,
        "height_basis": row.get("height_basis"),
        "volume_basis": row.get("volume_basis"),
        "coord_precision_m": float(row["coord_precision_m"]) if row.get("coord_precision_m") else None,
        "notes": notes,
        # Engineering rule 3: a source per field, not per record.
        "sources": {
            "structural_height_m": attr_citation,
            "crest_length_m": attr_citation,
            "gross_storage_mcm": attr_citation,
            "live_storage_mcm": attr_citation,
            "reservoir_area_km2": attr_citation,
            "commissioned_year": attr_citation,
            "lon": coord_citation,
            "lat": coord_citation,
            "dam_type": type_citation,
            "river": attr_citation,
            "state": attr_citation,
        },
    }

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [props["lon"], props["lat"]]},
        "properties": props,
    }


def main() -> int:
    if not SOURCE_CSV.exists():
        raise SystemExit(
            f"Source register not found at {SOURCE_CSV}.\n"
            f"Run `python scripts/clone_references.py` first."
        )

    with SOURCE_CSV.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    features = [build_feature(r) for r in rows]
    features.sort(key=lambda f: f["properties"]["name"].lower())

    ids = [f["properties"]["id"] for f in features]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise SystemExit(f"duplicate dam ids generated: {sorted(duplicates)}")

    collection = {
        "type": "FeatureCollection",
        "name": "FloodGuard India dam catalog",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "metadata": {
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "generator": "scripts/build_dam_catalog.py",
            "record_count": len(features),
            "primary_source": "CWC National Register of Large Dams (NRLD) 2019",
            "primary_source_url": "https://cwc.gov.in/sites/default/files/nrld-2019.pdf",
            "transcription_credit": (
                "Attribute transcription with per-dam NRLD page citations originates in the "
                "team's hydrobreach repository; carried across verbatim, not re-keyed."
            ),
            "licence": (
                "NRLD is a Government of India publication. Reuse under the National Data "
                "Sharing and Accessibility Policy (NDSAP); see docs/DATA_SOURCES.md."
            ),
            "known_gaps": KNOWN_GAPS,
            "caveats": [
                "frl_m, mddl_m, crest_elevation_m and spillway_capacity_m3s are null for every "
                "record: they are not in the NRLD tables transcribed here. They are never "
                "guessed. Scenario files may supply them explicitly with their own citation.",
                "Coordinates are single points of unstated convention at ~30 m precision. The "
                "dam point is snapped to the DEM-derived stream in Phase 2 before use.",
            ],
        },
        "features": features,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(collection, indent=2, ensure_ascii=False), encoding="utf-8")

    rivers = sorted({f["properties"]["river"] for f in features})
    print(f"Wrote {len(features)} dams across {len(rivers)} rivers to {OUT_PATH.relative_to(REPO_ROOT)}")
    print(f"Rivers: {', '.join(rivers)}")
    if KNOWN_GAPS:
        print("\nKnown gaps recorded in the catalog metadata:")
        for name, why in KNOWN_GAPS.items():
            print(f"  - {name}: {why.splitlines()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
