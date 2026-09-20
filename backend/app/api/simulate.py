"""Simulation submission, job status and live progress.

POST /api/simulate returns a job id immediately; the solve runs on a background
thread and streams progress over /ws/jobs/{id}. A dam-break run takes minutes
to hours, so anything else would time out the request.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.core.config import get_settings
from app.core.jobs import JobRunner, JobStore
from app.schemas.models import (
    BreachComparison,
    BreachPrediction,
    JobCreated,
    JobState,
    SimulationRequest,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["simulate"])

_store: JobStore | None = None
_runner: JobRunner | None = None


def get_store() -> JobStore:
    global _store
    if _store is None:
        settings = get_settings()
        _store = JobStore(settings.floodguard_data_dir / "jobs.sqlite")
    return _store


def get_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner(get_store(), get_settings().floodguard_data_dir)
        _runner.start()
    return _runner


def build_scenario(request: SimulationRequest):
    """Turn an API request into a validated Scenario.

    Two routes in: name a bundled scenario, or name a dam from the catalog and
    let the defaults come from the CWC NRLD record. Either way the result is an
    ordinary Scenario, so the API and the CLI run identical code.
    """
    from floodguard.scenario import Scenario

    settings = get_settings()

    if request.scenario_id:
        path = settings.scenarios_dir / f"{request.scenario_id}.yaml"
        if not path.exists():
            raise HTTPException(
                status_code=404, detail=f"no bundled scenario {request.scenario_id!r}"
            )
        scenario = Scenario.from_yaml(path)
    elif request.dam_id:
        scenario = _scenario_from_dam(request.dam_id)
    else:
        raise HTTPException(
            status_code=422, detail="supply either scenario_id or dam_id"
        )

    scenario = scenario.model_copy(deep=True)
    scenario.scenario_type = request.scenario_type

    if request.reservoir_level_m is not None:
        scenario.reservoir.initial_level_m = request.reservoir_level_m
    for field in ("shape", "growth", "width_m", "depth_m", "side_slope",
                  "formation_time_min", "parameter_model"):
        value = getattr(request.breach, field)
        if value is not None:
            setattr(scenario.breach, field, value)

    if request.resolution_m:
        scenario.domain.resolution_m = request.resolution_m
    if request.duration_hours:
        scenario.solver.duration_hours = request.duration_hours
    if request.cfl:
        scenario.solver.cfl = request.cfl
    if request.wet_threshold_m:
        scenario.solver.wet_threshold_m = request.wet_threshold_m
    if request.manning_n_overrides:
        scenario.solver.manning_n_overrides.update(request.manning_n_overrides)
    if request.engines:
        scenario.engines = request.engines

    # Re-validate: the overrides above can make a physically impossible request.
    try:
        scenario = Scenario.model_validate(scenario.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"invalid scenario: {exc}") from exc

    return scenario


def _scenario_from_dam(dam_id: str):
    """Build a minimal scenario from a catalog record."""
    from app.api.catalog import _dams
    from floodguard.scenario import DamSpec, Scenario

    record = next((d for d in _dams() if d["id"] == dam_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no dam with id {dam_id!r}")

    if not record.get("frl_m") and not record.get("crest_elevation_m"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{record['name']} has no published full reservoir level in the CWC NRLD "
                f"transcription, so the initial water level cannot be determined. Supply "
                f"reservoir_level_m explicitly, or use a bundled scenario which carries a "
                f"cited value."
            ),
        )

    dam = DamSpec(
        id=record["id"],
        name=record["name"],
        river=record["river"],
        state=record["state"],
        lon=record["lon"],
        lat=record["lat"],
        dam_type=record["dam_type"],
        structural_height_m=record["structural_height_m"],
        crest_length_m=record.get("crest_length_m"),
        crest_elevation_m=record.get("crest_elevation_m"),
        frl_m=record.get("frl_m"),
        mddl_m=record.get("mddl_m"),
        gross_storage_mcm=record.get("gross_storage_mcm"),
        live_storage_mcm=record.get("live_storage_mcm"),
        commissioned_year=record.get("commissioned_year"),
        sources=record.get("sources", {}),
    )
    return Scenario(
        id=f"adhoc_{dam_id}",
        name=f"{record['name']} dam break",
        description=f"Ad-hoc scenario built from the CWC NRLD record for {record['name']}.",
        scenario_type="complete_dam_break",
        dam=dam,
    )


@router.post("/api/simulate", response_model=JobCreated)
def submit(request: SimulationRequest) -> JobCreated:
    """Queue a simulation. Returns immediately with a job id."""
    scenario = build_scenario(request)
    store = get_store()
    job = store.create(scenario.id, request.model_dump(mode="json"))
    get_runner().submit(job.id, scenario)
    return JobCreated(
        job_id=job.id,
        status=job.status.value,
        scenario_id=scenario.id,
        websocket=f"/ws/jobs/{job.id}",
    )


@router.post("/api/scenarios/validate", response_model=BreachComparison)
def validate_scenario(request: SimulationRequest) -> BreachComparison:
    """Validate a request and return the breach predictions, without running.

    This is what fills the ghost hints beside the breach inputs: the user sees
    what each empirical model would predict next to whatever they typed, before
    committing to a run that takes minutes.
    """
    from floodguard.breach import parameters as bp

    scenario = build_scenario(request)
    used, predictions, spread = bp.resolve(scenario)
    return BreachComparison(
        used=BreachPrediction(**used.to_dict()),
        predictions=[BreachPrediction(**p.to_dict()) for p in predictions],
        spread=spread,
    )


@router.get("/api/jobs", response_model=list[JobState])
def list_jobs(limit: int = 25) -> list[JobState]:
    return [_job_state(j) for j in get_store().list(limit)]


@router.get("/api/jobs/{job_id}", response_model=JobState)
def get_job(job_id: str) -> JobState:
    job = get_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
    return _job_state(job)


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    ok = get_store().cancel(job_id)
    if not ok:
        raise HTTPException(
            status_code=409, detail="this job is not queued or running, so it cannot be cancelled"
        )
    return {"job_id": job_id, "cancelling": True}


def _job_state(job) -> JobState:
    d = job.to_dict()
    return JobState(
        id=d["id"],
        scenario_id=d["scenario_id"],
        status=d["status"],
        created_utc=d["created_utc"],
        fraction=d["progress"]["fraction"],
        phase=d["progress"]["phase"],
        message=d["progress"]["message"],
        log=d["progress"]["log"][-40:],
        eta_seconds=d["eta_seconds"],
        elapsed_seconds=d["elapsed_seconds"],
        error=d["error"],
    )


@router.websocket("/ws/jobs/{job_id}")
async def job_socket(websocket: WebSocket, job_id: str) -> None:
    """Live progress for one job.

    The job runs on a plain thread, so its callbacks arrive off the event loop.
    They are pushed onto an asyncio queue via `call_soon_threadsafe` rather than
    awaited directly, which is the only safe way to cross that boundary.
    """
    await websocket.accept()
    store = get_store()

    job = store.get(job_id)
    if job is None:
        await websocket.send_json({"type": "error", "detail": f"no job {job_id!r}"})
        await websocket.close()
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    def on_event(payload: dict) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, payload)
        except RuntimeError:
            pass  # the loop is shutting down

    store.subscribe(job_id, on_event)

    try:
        # Send the current state immediately so a client joining late is not
        # staring at an empty panel until the next tick.
        await websocket.send_json({"type": "snapshot", **_job_state(job).model_dump()})

        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # Keep-alive, and a chance to notice the job finished while idle.
                current = store.get(job_id)
                if current and current.status.value in (
                    "succeeded", "failed", "cancelled", "interrupted"
                ):
                    await websocket.send_json(
                        {"type": "status", "status": current.status.value,
                         "error": current.error}
                    )
                    break
                await websocket.send_json({"type": "ping"})
                continue

            await websocket.send_json(payload)
            if payload.get("type") == "status":
                break
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("job socket error", exc_info=True)
    finally:
        store.unsubscribe(job_id, on_event)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
