"""Clone the reference repositories listed in SPEC.md section 0.2 into reference/.

These are READ-ONLY. We read them, audit them in docs/AUDIT.md, and port
specific modules deliberately with attribution. We never vendor them wholesale
and reference/ is gitignored.

A repo that 404s is recorded as missing, not silently replaced with a guess.

    python scripts/clone_references.py            # shallow clone everything
    python scripts/clone_references.py --report   # just print current state
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = REPO_ROOT / "reference"
STATE_FILE = REFERENCE_DIR / "clone_state.json"


@dataclass(frozen=True)
class Ref:
    name: str
    url: str
    group: str
    why: str


REFERENCES: tuple[Ref, ...] = (
    # --- Our own team's prior work. Reuse freely, but verify every claim. ---
    Ref(
        "dam-break-prototype",
        "https://github.com/ashutosh9544/dam-break-prototype",
        "teammate",
        "Teammate prototype. Check what the solver actually integrates.",
    ),
    Ref(
        "hydrobreach",
        "https://github.com/maharishia07/hydrobreach",
        "teammate",
        "Teammate breach modelling. Check the Delft3D integration for real binary use.",
    ),
    Ref(
        "NSUT-SIH-REPO",
        "https://github.com/PixelPilot5/NSUT-SIH-REPO",
        "teammate",
        "Teammate SIH repo. Check the UI and API surface against our spec.",
    ),
    # --- Established engines and references. ---
    Ref(
        "DualSPHysics",
        "https://github.com/DualSPHysics/DualSPHysics",
        "engine",
        "Real GPU SPH solver with dam-break cases and a Dockerfile. Adapter target.",
    ),
    Ref(
        "Delft3D",
        "https://github.com/Deltares/Delft3D",
        "engine",
        "Official Delft3D / D-Flow FM. Source of truth for the .mdu / DIMR input deck format.",
    ),
    Ref(
        "anuga_core",
        "https://github.com/anuga-community/anuga_core",
        "engine",
        "Validated Python 2D SWE solver. Independent cross-check for swe_fv.",
    ),
    Ref(
        "pysph",
        "https://github.com/pypr/pysph",
        "engine",
        "Python SPH framework. dam_break_2d / dam_break_3d are our SPH setup patterns.",
    ),
    Ref(
        "SimpleDambrk",
        "https://github.com/FloodRiskGroup/SimpleDambrk",
        "reference",
        "Breach hydrograph to downstream propagation to DEM inundation. Closest in scope.",
    ),
)


def _run(args: list[str], cwd: Path | None = None, timeout: int = 900):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def remote_exists(url: str) -> tuple[bool, str]:
    """Check a remote without cloning it. Returns (exists, detail)."""
    try:
        proc = _run(["git", "ls-remote", "--exit-code", "-h", url], timeout=120)
    except subprocess.TimeoutExpired:
        return False, "timed out contacting remote"
    if proc.returncode == 0:
        return True, "reachable"
    detail = (proc.stderr or proc.stdout).strip().splitlines()
    return False, detail[-1] if detail else f"git exit {proc.returncode}"


def clone(ref: Ref) -> dict:
    """Shallow-clone one reference repo. Returns a state record."""
    target = REFERENCE_DIR / ref.name
    record: dict = {
        **asdict(ref),
        "path": str(target.relative_to(REPO_ROOT)),
        "checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if target.exists():
        head = _run(["git", "rev-parse", "--short", "HEAD"], cwd=target)
        record |= {
            "status": "present",
            "commit": head.stdout.strip() or None,
            "detail": "already cloned; left untouched",
        }
        return record

    ok, detail = remote_exists(ref.url)
    if not ok:
        record |= {"status": "unavailable", "commit": None, "detail": detail}
        print(f"  UNAVAILABLE {ref.name}: {detail}")
        return record

    print(f"  cloning {ref.name} ...")
    proc = _run(["git", "clone", "--depth", "1", ref.url, str(target)])
    if proc.returncode != 0:
        record |= {
            "status": "clone_failed",
            "commit": None,
            "detail": (proc.stderr or "").strip().splitlines()[-1:] or ["unknown error"],
        }
        return record

    head = _run(["git", "rev-parse", "--short", "HEAD"], cwd=target)
    size_mb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1e6
    record |= {
        "status": "cloned",
        "commit": head.stdout.strip() or None,
        "detail": f"shallow clone, {size_mb:.1f} MB on disk",
    }
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="print saved state and exit")
    args = parser.parse_args(argv)

    REFERENCE_DIR.mkdir(exist_ok=True)

    if args.report:
        if not STATE_FILE.exists():
            print("No clone state yet. Run without --report first.", file=sys.stderr)
            return 1
        print(STATE_FILE.read_text(encoding="utf-8"))
        return 0

    records = []
    for group in ("teammate", "engine", "reference"):
        print(f"\n{group.upper()}")
        for ref in REFERENCES:
            if ref.group == group:
                records.append(clone(ref))

    STATE_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")

    unavailable = [r for r in records if r["status"] != "cloned" and r["status"] != "present"]
    print(f"\n{len(records) - len(unavailable)}/{len(records)} available.")
    if unavailable:
        print("Record these as missing in docs/AUDIT.md (do NOT invent substitutes):")
        for r in unavailable:
            print(f"  - {r['name']}: {r['detail']}")
    print(f"\nState written to {STATE_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
