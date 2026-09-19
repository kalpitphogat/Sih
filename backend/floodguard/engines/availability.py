"""Runtime detection of which hydrodynamic engines actually exist on this machine.

This module is the backbone of engineering rule 2 (label every engine honestly).
It probes the environment and reports the truth; nothing downstream is allowed to
claim an engine that does not appear here as available.

It performs no imports of heavy optional packages at module import time -- only
inside the probe functions -- so importing it is always safe and fast.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EngineKind(str, Enum):
    """What sort of thing is actually going to run."""

    NATIVE = "native"        # our own solver, shipped in this repo
    EXTERNAL = "external"    # a third-party validated solver / binary
    UNAVAILABLE = "unavailable"


class EngineUnavailable(RuntimeError):
    """Raised when an engine was requested but its runtime is genuinely absent.

    The orchestrator catches this, substitutes a native solver, and is required
    to surface the substitution in the API response, the UI badge and the PDF.
    """

    def __init__(self, engine_id: str, reason: str) -> None:
        self.engine_id = engine_id
        self.reason = reason
        super().__init__(f"{engine_id} unavailable: {reason}")


@dataclass
class EngineStatus:
    """One row of GET /api/health/engines."""

    id: str
    # The exact string the UI badge, the API and the PDF must display.
    display_name: str
    kind: EngineKind
    available: bool
    is_real_solver: bool
    version: str | None = None
    detail: str = ""
    # If this engine is requested but unavailable, what runs instead (or None).
    substitute_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "kind": self.kind.value,
            "available": self.available,
            "is_real_solver": self.is_real_solver,
            "version": self.version,
            "detail": self.detail,
            "substitute_id": self.substitute_id,
            "evidence": self.evidence,
        }


def _module_version(module_name: str, dist_name: str | None = None) -> str | None:
    """Version of an installed module, without importing it."""
    if importlib.util.find_spec(module_name) is None:
        return None

    try:
        return importlib.metadata.version(dist_name or module_name)
    except importlib.metadata.PackageNotFoundError:
        return "installed (version unknown)"


def _find_binary(env_var: str, *candidates: str) -> str | None:
    """Locate an external binary: explicit env override first, then PATH."""
    override = os.environ.get(env_var, "").strip()
    if override:
        return override if os.path.isfile(override) else None
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return None


def probe_swe_fv() -> EngineStatus:
    """Our own HLLC finite-volume 2D shallow-water solver."""
    numba_version = _module_version("numba")
    numpy_version = _module_version("numpy")
    available = numpy_version is not None
    if not available:
        detail = "numpy is not installed; the native solver cannot run."
    elif numba_version:
        detail = "Numba JIT active."
    else:
        detail = "Numba not installed -- solver will run the slower pure-NumPy path."
    return EngineStatus(
        id="swe_fv",
        display_name="FloodGuard-SWE (Delft3D-class FV solver)",
        kind=EngineKind.NATIVE if available else EngineKind.UNAVAILABLE,
        available=available,
        is_real_solver=True,
        version="0.1.0",
        detail=detail,
        evidence={"numpy": numpy_version, "numba": numba_version},
    )


def probe_anuga() -> EngineStatus:
    """ANUGA -- independent, published, validated 2D SWE solver (conda-forge)."""
    version = _module_version("anuga")
    return EngineStatus(
        id="anuga",
        display_name=(
            "ANUGA (2D shallow-water, Geoscience Australia)" if version else "ANUGA -- not installed"
        ),
        kind=EngineKind.EXTERNAL if version else EngineKind.UNAVAILABLE,
        available=version is not None,
        is_real_solver=True,
        version=version,
        detail=(
            "Used as an independent cross-check of FloodGuard-SWE."
            if version
            else "ANUGA ships on conda-forge only. Install with "
                 "`conda install -c conda-forge anuga`."
        ),
        substitute_id=None if version else "swe_fv",
        evidence={"module": "anuga"},
    )


def probe_delft3d() -> EngineStatus:
    """Real D-Flow FM binaries. Absent binaries must never be labelled Delft3D."""
    binary = _find_binary("DFLOWFM_BIN", "dflowfm", "dflowfm-cli", "dflowfm.exe")
    dimr = _find_binary("DIMR_BIN", "dimr", "dimr.exe")
    available = binary is not None
    return EngineStatus(
        id="delft3d",
        display_name=(
            "Delft3D Flexible Mesh (D-Flow FM)"
            if available
            else "FloodGuard-SWE (Delft3D-class FV solver)"
        ),
        kind=EngineKind.EXTERNAL if available else EngineKind.UNAVAILABLE,
        available=available,
        is_real_solver=available,
        version=None,
        detail=(
            f"dflowfm found at {binary}."
            if available
            else "No dflowfm/dimr binary on PATH. FloodGuard still auto-generates a complete "
                 "D-Flow FM input deck (_net.nc, .mdu, .bc/.pli, DIMR config) as a downloadable "
                 "artefact, and runs FloodGuard-SWE for the actual solve. The substitution is "
                 "labelled in the API response, the UI badge and the PDF report."
        ),
        substitute_id=None if available else "swe_fv",
        evidence={"dflowfm": binary, "dimr": dimr},
    )


def probe_pysph() -> EngineStatus:
    """PySPH -- weakly-compressible SPH, used for the near-field breach collapse."""
    version = _module_version("pysph")
    return EngineStatus(
        id="sph_pysph",
        display_name="PySPH (WCSPH / delta-SPH)" if version else "PySPH -- not installed",
        kind=EngineKind.EXTERNAL if version else EngineKind.UNAVAILABLE,
        available=version is not None,
        is_real_solver=True,
        version=version,
        detail=(
            "Near-field 3D breach collapse; coupled to the 2D engine at a transfer section."
            if version
            else "Install with `pip install pysph` (needs a C compiler)."
        ),
        substitute_id=None if version else "swe_fv",
        evidence={"module": "pysph"},
    )


def probe_dualsphysics() -> EngineStatus:
    """DualSPHysics -- GPU SPH. Needs both GenCase and DualSPHysics binaries."""
    dsph = _find_binary(
        "DUALSPHYSICS_BIN", "DualSPHysics5.0CPU", "DualSPHysics5.0", "dualsphysics"
    )
    gencase = _find_binary("GENCASE_BIN", "GenCase", "GenCase5.0", "gencase")
    available = dsph is not None and gencase is not None
    return EngineStatus(
        id="dualsphysics",
        display_name="DualSPHysics (GPU SPH)" if available else "DualSPHysics -- not installed",
        kind=EngineKind.EXTERNAL if available else EngineKind.UNAVAILABLE,
        available=available,
        is_real_solver=available,
        version=None,
        detail=(
            f"GenCase at {gencase}, solver at {dsph}."
            if available
            else "GenCase and/or DualSPHysics binaries not found. FloodGuard still writes a "
                 "valid CaseDef.xml as a downloadable artefact."
        ),
        substitute_id=None if available else "sph_pysph",
        evidence={"DualSPHysics": dsph, "GenCase": gencase},
    )


_PROBES = (
    probe_swe_fv,
    probe_anuga,
    probe_delft3d,
    probe_pysph,
    probe_dualsphysics,
)


def probe_all() -> list[EngineStatus]:
    """Probe every engine. Used by GET /api/health/engines and the CLI."""
    return [probe() for probe in _PROBES]


def resolve(engine_id: str) -> EngineStatus:
    """Status of one engine by id.

    Raises KeyError for an unknown id so a typo never silently becomes a fallback.
    """
    for status in probe_all():
        if status.id == engine_id:
            return status
    raise KeyError(f"unknown engine id: {engine_id!r}")
