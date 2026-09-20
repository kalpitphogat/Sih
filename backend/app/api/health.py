"""Health and capability endpoints.

`/api/health/engines` is not decoration: it is what drives the honest engine
badges in the UI. If it says an engine is unavailable, the UI must not offer it
as if it were real.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.provenance import Provenance, package_version
from floodguard.engines.availability import probe_all

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Liveness plus a truthful description of what this deployment can do."""
    settings = get_settings()
    prov = Provenance()
    engines = probe_all()

    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.version,
        "git_commit": prov.git_commit,
        "generated_utc": prov.generated_utc,
        "python": prov.python,
        "platform": prov.platform,
        "engines_available": sorted(e.id for e in engines if e.available),
        "engines_unavailable": sorted(e.id for e in engines if not e.available),
        "credentials": {
            # Booleans only. Never echo the values.
            "opentopo_api_key": bool(settings.opentopo_api_key),
            "google_application_credentials": bool(settings.google_application_credentials),
        },
        "data_dir": str(settings.floodguard_data_dir),
    }


@router.get("/api/health/engines")
def health_engines() -> dict:
    """Which hydrodynamic engines are genuinely runnable on this machine."""
    engines = probe_all()
    return {
        "engines": [e.to_dict() for e in engines],
        "honesty_statement": (
            "An engine is reported available only when its runtime was detected on this "
            "machine. Where a requested engine is unavailable, FloodGuard substitutes the "
            "named native solver and labels the substitution in the API response, the UI "
            "badge and the PDF report."
        ),
    }


@router.get("/api/health/packages")
def health_packages() -> dict:
    """Versions of the scientific stack, for reproducing a result exactly."""
    names = [
        "numpy", "scipy", "numba", "pandas",
        "rasterio", "rioxarray", "xarray", "geopandas", "shapely", "pyproj", "fiona",
        "netCDF4", "zarr", "pysheds", "whitebox",
        "fastapi", "pydantic", "uvicorn",
        "matplotlib", "reportlab", "earthengine-api",
    ]
    return {name: package_version(name) for name in names}


@router.get("/api/validation")
def validation_results() -> dict:
    """The solver verification results, as computed by `make validate`.

    Served from the file the verification run wrote, so the About page cannot
    display a passing result that was not actually produced. When the file is
    absent the page says the suite has not been run rather than showing
    reassuring defaults.
    """
    import json

    from app.core.config import REPO_ROOT

    path = REPO_ROOT / "docs" / "validation" / "results.json"
    if not path.exists():
        return {
            "available": False,
            "detail": (
                "No verification results at docs/validation/results.json. Run "
                "`make validate` to produce them. Until then this deployment makes no "
                "claim about solver accuracy."
            ),
            "checks": [],
        }

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"available": False, "detail": f"results.json is unreadable: {exc}", "checks": []}

    return {
        "available": True,
        "passed": report.get("passed", False),
        "runtime_s": report.get("runtime_s"),
        "generated_from": str(path),
        "checks": [
            {
                "name": c["name"],
                "passed": c["passed"],
                "threshold": c["threshold"],
                "note": c.get("note", ""),
                "plot": c.get("plot"),
                "metrics": c.get("metrics", {}),
            }
            for c in report.get("checks", [])
        ],
        "caveat": (
            "Verification is not validation. These are comparisons against exact "
            "analytical solutions, so passing them means the numerics are right. It "
            "says nothing about whether the DEM, the breach parameters, the roughness "
            "field or the reconstructed reservoir bathymetry describe the real river."
        ),
    }
