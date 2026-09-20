"""Rivers, dams and reservoir curves — what populates the dropdowns.

Every attribute served here carries its per-field citation, so a judge asking
"where does that FRL come from?" gets an answer from the API rather than from
a slide.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.config import get_settings
from app.schemas.models import DamSummary, ReservoirCurve, ReservoirCurvePoint, RiverSummary

router = APIRouter(prefix="/api", tags=["catalog"])


@lru_cache
def _catalog() -> dict[str, Any]:
    path = get_settings().catalog_dir / "dams.geojson"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                f"The dam catalog is missing at {path}. Build it with "
                f"`python scripts/build_dam_catalog.py`."
            ),
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _dams() -> list[dict[str, Any]]:
    return [f["properties"] for f in _catalog()["features"]]


@router.get("/rivers", response_model=list[RiverSummary])
def list_rivers() -> list[RiverSummary]:
    """Rivers we have at least one dam for, with their states."""
    by_river: dict[str, list[dict[str, Any]]] = {}
    for dam in _dams():
        by_river.setdefault(dam["river"], []).append(dam)

    return sorted(
        (
            RiverSummary(
                name=river,
                dam_count=len(dams),
                states=sorted({d["state"] for d in dams}),
            )
            for river, dams in by_river.items()
        ),
        key=lambda r: r.name,
    )


@router.get("/dams", response_model=list[DamSummary])
def list_dams(
    river: str | None = Query(default=None, description="Filter by river name."),
    state: str | None = Query(default=None, description="Filter by state."),
) -> list[DamSummary]:
    """Dams, optionally filtered. Defaults come straight from the CWC NRLD."""
    dams = _dams()
    if river:
        dams = [d for d in dams if d["river"].lower() == river.lower()]
    if state:
        dams = [d for d in dams if d["state"].lower() == state.lower()]
    return [DamSummary(**{k: d.get(k) for k in DamSummary.model_fields}) for d in dams]


@router.get("/dams/{dam_id}", response_model=DamSummary)
def get_dam(dam_id: str) -> DamSummary:
    for dam in _dams():
        if dam["id"] == dam_id:
            return DamSummary(**{k: dam.get(k) for k in DamSummary.model_fields})
    raise HTTPException(status_code=404, detail=f"no dam with id {dam_id!r} in the catalog")


@router.get("/dams/{dam_id}/reservoir-curve", response_model=ReservoirCurve)
def reservoir_curve(dam_id: str) -> ReservoirCurve:
    """The DEM-derived elevation-area-capacity curve, if it has been computed.

    Returns 404 rather than a synthetic curve when preprocessing has not run.
    A plausible-looking invented curve is exactly the failure mode this project
    exists to avoid.
    """
    settings = get_settings()
    for scenario_dir in sorted(settings.processed_dir.glob("*")):
        pre_path = scenario_dir / "preprocess.json"
        if not pre_path.exists():
            continue
        pre = json.loads(pre_path.read_text(encoding="utf-8"))
        if pre.get("scenario_id", "").split("_")[0] not in dam_id and dam_id not in pre.get(
            "scenario_id", ""
        ):
            continue
        reservoir = pre.get("reservoir")
        if not reservoir:
            continue
        curve = reservoir["curve"]
        return ReservoirCurve(
            dam_id=dam_id,
            method=curve["method"],
            points=[
                ReservoirCurvePoint(level_m=lv, area_km2=a, volume_mcm=v)
                for lv, a, v in zip(
                    curve["levels_m"], curve["areas_km2"], curve["volumes_mcm"]
                )
            ],
            derived_gross_storage_mcm=reservoir.get("derived_gross_storage_mcm"),
            catalog_gross_storage_mcm=reservoir.get("catalog_gross_storage_mcm"),
            storage_difference_pct=reservoir.get("storage_difference_pct"),
            bathymetry_reconstructed=bool(
                reservoir.get("bathymetry", {}).get("applied")
            ),
            warnings=reservoir.get("warnings", []),
        )

    raise HTTPException(
        status_code=404,
        detail=(
            f"No reservoir curve has been derived for {dam_id!r}. It is computed from the "
            f"DEM during preprocessing; run `floodguard preprocess` for a scenario using "
            f"this dam. No synthetic curve is served in its place."
        ),
    )


@router.get("/scenarios")
def list_scenarios() -> list[dict[str, Any]]:
    """Bundled scenario files, for the one-click demo launches."""
    from floodguard.scenario import Scenario

    settings = get_settings()
    out = []
    for path in sorted(settings.scenarios_dir.glob("*.yaml")):
        try:
            scenario = Scenario.from_yaml(path)
        except Exception as exc:  # noqa: BLE001 - a bad file must not hide the good ones
            out.append({"id": path.stem, "error": str(exc), "valid": False})
            continue
        out.append(
            {
                "id": scenario.id,
                "name": scenario.name,
                "description": scenario.description,
                "dam": scenario.dam.name,
                "river": scenario.dam.river,
                "state": scenario.dam.state,
                "lon": scenario.dam.lon,
                "lat": scenario.dam.lat,
                "scenario_type": scenario.scenario_type.value,
                "resolution_m": scenario.domain.resolution_m,
                "duration_hours": scenario.solver.duration_hours,
                "reach_length_km": scenario.domain.reach_length_km,
                "towns": [t.name for t in scenario.towns],
                "valid": True,
            }
        )
    return out
