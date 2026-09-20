"""Near-real-time flood monitoring from Sentinel-1 (Phase 8).

Without Earth Engine credentials this returns a clearly-labelled
"not configured" state. It never serves a cached result dressed up as a live
feed, which is why `GET /api/monitoring/status` exists as a separate endpoint:
the UI asks whether a live run is even possible before offering the button.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from floodguard.data import gee

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


class MonitoringRequest(BaseModel):
    """POST /api/monitoring/analyze."""

    bbox: tuple[float, float, float, float] = Field(
        description="AOI as (west, south, east, north) in EPSG:4326."
    )
    post_start: str = Field(description="Post-event window start, YYYY-MM-DD.")
    post_end: str = Field(description="Post-event window end, YYYY-MM-DD.")
    pre_start: str | None = None
    pre_end: str | None = None
    polarisation: str = "VV"
    threshold_db: float | None = Field(
        default=None,
        description=(
            "Pin the backscatter threshold instead of computing Otsu per scene. "
            "Usually leave unset: the land/water split moves with incidence angle, "
            "season and land cover."
        ),
    )
    scale_m: int = 30
    #: Compare against this completed run's simulated extent.
    run_id: str | None = None


class MonitoringStatus(BaseModel):
    configured: bool
    package_installed: bool
    credentials_present: bool
    detail: str
    collections: dict[str, str]


@router.get("/status", response_model=MonitoringStatus)
def monitoring_status() -> MonitoringStatus:
    """Whether a live detection can be attempted on this deployment."""
    return MonitoringStatus(**gee.status())


@router.post("/analyze")
def analyze(request: MonitoringRequest) -> dict[str, Any]:
    """Run Sentinel-1 flood detection, optionally scored against a simulation."""
    try:
        detection = gee.detect_flood(
            request.bbox,
            request.post_start,
            request.post_end,
            pre_start=request.pre_start,
            pre_end=request.pre_end,
            polarisation=request.polarisation,
            threshold_db=request.threshold_db,
            scale_m=request.scale_m,
        )
    except gee.GEEUnavailable as exc:
        # 503, not 500: the service is fine, the capability is absent, and the
        # UI needs to tell those apart.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "earth_engine_unavailable",
                "message": str(exc),
                "status": gee.status(),
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("flood detection failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    payload: dict[str, Any] = detection.to_dict()
    payload["geojson"] = detection.geojson

    if request.run_id:
        depth_path = (
            get_settings().floodguard_data_dir / "runs" / request.run_id / "max_depth.tif"
        )
        if not depth_path.exists():
            payload["agreement"] = {
                "computed": False,
                "reason": (
                    f"run {request.run_id!r} has no max_depth.tif, so there is no "
                    f"simulated extent to compare against"
                ),
            }
        else:
            payload["agreement"] = gee.agreement_with_simulation(detection, depth_path)

    return payload
