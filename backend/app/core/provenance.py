"""Provenance stamping.

Engineering rule 3: every output we write — GeoTIFF, Shapefile, JSON, PDF —
carries enough metadata for a judge to trace the number on screen back to a file
on disk and the code that produced it.

This module is deliberately dependency-free at import time so it can be used
from the CLI, the API and the science package alike.
"""

from __future__ import annotations

import importlib.metadata
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


@lru_cache
def git_commit() -> str:
    """Short git hash of the working tree, or 'unknown' outside a repo.

    Suffixed '-dirty' when there are uncommitted changes, because a provenance
    record that silently claims a clean commit is worse than no record.
    """
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"

    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        dirty = ""

    return f"{rev}-dirty" if dirty else rev


def package_version(name: str) -> str | None:
    """Installed version of a package, or None if it is not installed."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Provenance:
    """The provenance block embedded in every FloodGuard output."""

    produced_by: str = "FloodGuard India"
    version: str = "0.1.0"
    git_commit: str = field(default_factory=git_commit)
    generated_utc: str = field(default_factory=utc_now_iso)
    python: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=lambda: f"{platform.system()} {platform.release()}")

    # Filled in by whichever stage produces the artefact.
    inputs: dict[str, Any] = field(default_factory=dict)
    engine: dict[str, Any] = field(default_factory=dict)
    solver_settings: dict[str, Any] = field(default_factory=dict)
    runtime_seconds: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_gdal_tags(self) -> dict[str, str]:
        """Flatten to string key/value pairs for GeoTIFF metadata tags."""
        import json

        out: dict[str, str] = {}
        for key, value in self.to_dict().items():
            if value is None:
                continue
            out[f"FG_{key.upper()}"] = (
                value if isinstance(value, str) else json.dumps(value, default=str)
            )
        return out
