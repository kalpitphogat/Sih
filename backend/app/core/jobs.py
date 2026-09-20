"""Job store and background runner for simulations.

A simulation takes minutes to hours, so the API cannot run one inside a request.
Jobs are submitted, run in a background thread, and streamed over a WebSocket.

SQLite rather than Redis/Celery deliberately: the spec says upgrade only if
needed, and a single-process deployment with an on-disk job store has one fewer
moving part to fail during a demo. The store is written so the upgrade path is
a matter of replacing this class, not of touching the API.

Jobs survive a restart as records; a job that was running when the process died
is marked `interrupted` on startup rather than left claiming to be running.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass
class JobProgress:
    """The payload streamed to the WebSocket and returned by GET /api/jobs/{id}."""

    fraction: float = 0.0
    phase: str = "queued"
    message: str = ""
    updated_utc: str = ""
    #: Rolling tail of log lines, so a client joining late has context.
    log: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def eta_seconds(self, started_at: float) -> float | None:
        """Linear ETA from elapsed time and fraction complete.

        Deliberately linear and deliberately labelled an estimate. A
        dam-break run is not linear in wall time — the timestep shrinks as the
        wave accelerates — so this will be optimistic early on. Showing an
        honest rough number beats showing a spinner with no information.
        """
        if self.fraction <= 0.01:
            return None
        elapsed = time.time() - started_at
        return max(elapsed / self.fraction - elapsed, 0.0)


@dataclass
class Job:
    id: str
    scenario_id: str
    status: JobStatus
    created_utc: str
    started_at: float | None = None
    finished_at: float | None = None
    progress: JobProgress = field(default_factory=JobProgress)
    result_path: str | None = None
    error: str | None = None
    request: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["status"] = self.status.value
        out["eta_seconds"] = (
            self.progress.eta_seconds(self.started_at) if self.started_at else None
        )
        out["elapsed_seconds"] = (
            (self.finished_at or time.time()) - self.started_at if self.started_at else None
        )
        return out


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    started_at REAL,
    finished_at REAL,
    progress TEXT NOT NULL,
    result_path TEXT,
    error TEXT,
    request TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_utc DESC);
"""


class JobStore:
    """Thread-safe SQLite-backed job store with in-memory progress."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Live progress is kept in memory: writing every solver tick to disk
        # would dominate the runtime of a fast job.
        self._live: dict[str, JobProgress] = {}
        self._subscribers: dict[str, list[Callable[[dict], None]]] = {}
        self._cancelled: set[str] = set()
        self._init_db()
        self._mark_orphans()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _mark_orphans(self) -> None:
        """A job recorded as running after a restart did not survive.

        Leaving it as 'running' would have the UI wait forever for a process
        that no longer exists.
        """
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status = ?, error = ? WHERE status IN (?, ?)",
                (
                    JobStatus.INTERRUPTED.value,
                    "the server restarted while this job was running",
                    JobStatus.RUNNING.value,
                    JobStatus.QUEUED.value,
                ),
            )
            if cur.rowcount:
                log.warning("marked %d orphaned job(s) as interrupted", cur.rowcount)

    # --- lifecycle ----------------------------------------------------------------

    def create(self, scenario_id: str, request: dict[str, Any]) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:16],
            scenario_id=scenario_id,
            status=JobStatus.QUEUED,
            created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            request=request,
        )
        with self._lock:
            self._live[job.id] = job.progress
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO jobs (id, scenario_id, status, created_utc, progress, request)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        job.id,
                        job.scenario_id,
                        job.status.value,
                        job.created_utc,
                        json.dumps(asdict(job.progress)),
                        json.dumps(request),
                    ),
                )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        progress = self._live.get(job_id)
        if progress is None:
            progress = JobProgress(**json.loads(row["progress"]))
        return Job(
            id=row["id"],
            scenario_id=row["scenario_id"],
            status=JobStatus(row["status"]),
            created_utc=row["created_utc"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            progress=progress,
            result_path=row["result_path"],
            error=row["error"],
            request=json.loads(row["request"]),
        )

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs ORDER BY created_utc DESC LIMIT ?", (limit,)
            ).fetchall()
        return [j for j in (self.get(r["id"]) for r in rows) if j]

    def mark_running(self, job_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, started_at = ? WHERE id = ?",
                (JobStatus.RUNNING.value, time.time(), job_id),
            )

    def finish(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result_path: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            progress = self._live.get(job_id, JobProgress())
            with self._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status = ?, finished_at = ?, result_path = ?, "
                    "error = ?, progress = ? WHERE id = ?",
                    (
                        status.value,
                        time.time(),
                        result_path,
                        error,
                        json.dumps(asdict(progress)),
                        job_id,
                    ),
                )
        self._publish(job_id, {"type": "status", "status": status.value, "error": error})

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. The runner checks this between solver steps."""
        job = self.get(job_id)
        if job is None or job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            return False
        with self._lock:
            self._cancelled.add(job_id)
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    # --- progress -----------------------------------------------------------------

    def update_progress(
        self, job_id: str, *, fraction: float, phase: str, message: str, **extra: Any
    ) -> None:
        with self._lock:
            progress = self._live.setdefault(job_id, JobProgress())
            progress.fraction = float(fraction)
            progress.phase = phase
            progress.message = message
            progress.updated_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
            progress.extra = {k: v for k, v in extra.items() if _jsonable(v)}
            if message:
                progress.log.append(f"[{progress.phase}] {message}")
                # Keep only a tail: a long solve emits thousands of lines and
                # the UI only ever shows the last few.
                if len(progress.log) > 200:
                    del progress.log[:-200]
        self._publish(
            job_id,
            {
                "type": "progress",
                "fraction": fraction,
                "phase": phase,
                "message": message,
                **{k: v for k, v in extra.items() if _jsonable(v)},
            },
        )

    def subscribe(self, job_id: str, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(callback)

    def unsubscribe(self, job_id: str, callback: Callable[[dict], None]) -> None:
        with self._lock:
            subs = self._subscribers.get(job_id, [])
            if callback in subs:
                subs.remove(callback)

    def _publish(self, job_id: str, payload: dict) -> None:
        with self._lock:
            subs = list(self._subscribers.get(job_id, []))
        for cb in subs:
            try:
                cb(payload)
            except Exception:  # noqa: BLE001 - a dead socket must not kill the solver
                log.debug("progress subscriber raised", exc_info=True)


def _jsonable(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, type(None), list, dict))


class JobRunner:
    """Runs simulation jobs on a background thread pool of one.

    One worker by design: the solver already saturates every core through
    numba's threads, so running two simulations concurrently makes both slower
    and risks exhausting memory on a large grid.
    """

    def __init__(self, store: JobStore, data_dir: Path) -> None:
        self.store = store
        self.data_dir = Path(data_dir)
        self._thread: threading.Thread | None = None
        self._queue: list[tuple[str, Any]] = []
        self._queue_lock = threading.Lock()
        self._wake = threading.Event()
        self._shutdown = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="floodguard-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._shutdown = True
        self._wake.set()

    def submit(self, job_id: str, scenario) -> None:
        with self._queue_lock:
            self._queue.append((job_id, scenario))
        self._wake.set()
        self.start()

    def _loop(self) -> None:
        while not self._shutdown:
            with self._queue_lock:
                item = self._queue.pop(0) if self._queue else None
            if item is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            self._run_one(*item)

    def _run_one(self, job_id: str, scenario) -> None:
        from floodguard.pipeline import simulate

        if self.store.is_cancelled(job_id):
            self.store.finish(job_id, JobStatus.CANCELLED)
            return

        self.store.mark_running(job_id)

        def progress(*, fraction, phase, message, **extra):
            if self.store.is_cancelled(job_id):
                raise _Cancelled()
            self.store.update_progress(
                job_id, fraction=fraction, phase=phase, message=message, **extra
            )

        try:
            result = simulate(scenario, self.data_dir, progress=progress, run_id=job_id)
            self.store.finish(
                job_id, JobStatus.SUCCEEDED, result_path=str(result.out_dir / "result.json")
            )
        except _Cancelled:
            log.info("job %s cancelled", job_id)
            self.store.finish(job_id, JobStatus.CANCELLED)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", job_id)
            self.store.finish(
                job_id,
                JobStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}",
            )


class _Cancelled(Exception):
    """Raised inside the progress callback to unwind a cancelled job."""
