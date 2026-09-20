"""`floodguard demo` — bring up a working dashboard with no internet.

The venue Wi-Fi will fail. That is not pessimism, it is the base rate. So the
demo path must depend on nothing but files already on disk.

What this does:

1. Verifies the repository is in a demonstrable state and says precisely what
   is missing if it is not — rather than starting a server that will show empty
   panels and leave you debugging in front of judges.
2. Starts the API and the frontend dev server.
3. Opens a browser on the Simulation page.

What it deliberately does not do: fetch anything, or generate a "demo result"
that was not produced by a real run. If no completed run exists, it says so and
tells you the one command that produces one.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def preflight(data_dir: Path) -> list[Check]:
    """Everything the demo needs, checked before anything is started."""
    checks: list[Check] = []

    catalog = data_dir / "catalog" / "dams.geojson"
    if catalog.exists():
        count = len(json.loads(catalog.read_text(encoding="utf-8"))["features"])
        checks.append(Check("Dam catalog", True, f"{count} dams with per-field citations"))
    else:
        checks.append(
            Check(
                "Dam catalog", False, "data/catalog/dams.geojson is missing",
                "python scripts/build_dam_catalog.py",
            )
        )

    scenarios = sorted((data_dir / "scenarios").glob("*.yaml"))
    checks.append(
        Check(
            "Scenarios",
            bool(scenarios),
            ", ".join(s.stem for s in scenarios) or "none found",
            "" if scenarios else "add a YAML file to data/scenarios/",
        )
    )

    validation = REPO_ROOT / "docs" / "validation" / "results.json"
    if validation.exists():
        report = json.loads(validation.read_text(encoding="utf-8"))
        passed = sum(1 for c in report.get("checks", []) if c["passed"])
        total = len(report.get("checks", []))
        checks.append(
            Check(
                "Solver verification",
                report.get("passed", False),
                f"{passed}/{total} checks passed",
                "" if report.get("passed") else "make validate",
            )
        )
    else:
        checks.append(
            Check(
                "Solver verification", False,
                "no results at docs/validation/results.json",
                "make validate",
            )
        )

    runs = sorted(
        (d for d in (data_dir / "runs").glob("*") if (d / "result.json").exists()),
        key=lambda d: d.stat().st_mtime,
    )
    if runs:
        newest = runs[-1]
        result = json.loads((newest / "result.json").read_text(encoding="utf-8"))
        engine = next((e for e in result["engines"] if e.get("summary")), {})
        area = (engine.get("summary") or {}).get("flooded_area_km2")
        checks.append(
            Check(
                "Completed run",
                True,
                f"{len(runs)} run(s); newest {newest.name} "
                f"({area:.1f} km2 flooded)" if area is not None else f"{len(runs)} run(s)",
            )
        )
    else:
        checks.append(
            Check(
                "Completed run", False,
                "no run with a result.json under data/runs/",
                "floodguard simulate --scenario data/scenarios/tehri_bhagirathi.yaml "
                "--resolution 90",
            )
        )

    frontend_deps = (REPO_ROOT / "frontend" / "node_modules").exists()
    checks.append(
        Check(
            "Frontend dependencies", frontend_deps,
            "node_modules present" if frontend_deps else "node_modules is missing",
            "" if frontend_deps else "cd frontend && npm install",
        )
    )

    npm = shutil.which("npm") is not None
    checks.append(
        Check("npm", npm, "found on PATH" if npm else "not found", "" if npm else "install Node.js")
    )

    return checks


def run_demo(
    data_dir: Path,
    *,
    api_port: int = 8000,
    web_port: int = 5173,
    open_browser: bool = True,
    check_only: bool = False,
) -> int:
    """Preflight, then start the stack."""
    print("FloodGuard India — demo preflight")
    print("=" * 66)

    checks = preflight(data_dir)
    width = max(len(c.name) for c in checks)
    blocking: list[Check] = []

    for c in checks:
        mark = "ok " if c.ok else "MISSING"
        print(f"  [{mark:>7}] {c.name.ljust(width)}  {c.detail}")
        if not c.ok:
            if c.fix:
                print(f"  {'':>9} {'':<{width}}  fix: {c.fix}")
            blocking.append(c)

    print()

    if blocking:
        names = ", ".join(c.name for c in blocking)
        print(
            f"{len(blocking)} item(s) missing: {names}.\n"
            f"The dashboard will start, but those panels will be empty. Nothing is "
            f"substituted for them."
        )
        print()

    if check_only:
        return 0 if not blocking else 1

    backend_cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(api_port),
    ]
    frontend_cmd = ["npm", "run", "dev", "--", "--port", str(web_port)]

    print(f"Starting the API on http://127.0.0.1:{api_port} …")
    backend = subprocess.Popen(backend_cmd, cwd=REPO_ROOT / "backend")

    print(f"Starting the dashboard on http://localhost:{web_port} …")
    frontend = subprocess.Popen(
        frontend_cmd, cwd=REPO_ROOT / "frontend", shell=(sys.platform == "win32")
    )

    url = f"http://localhost:{web_port}/simulation"
    try:
        # Give Vite a moment before opening, so the first paint is the app and
        # not a connection error.
        time.sleep(4)
        if open_browser:
            webbrowser.open(url)

        print()
        print(f"Dashboard : {url}")
        print(f"API docs  : http://127.0.0.1:{api_port}/docs")
        print()
        print("Ctrl-C to stop both.")
        backend.wait()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        for proc in (frontend, backend):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    return 0
