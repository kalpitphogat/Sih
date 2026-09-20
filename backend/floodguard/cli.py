"""FloodGuard command line interface.

    floodguard engines
    floodguard data      --scenario data/scenarios/tehri_bhagirathi.yaml
    floodguard preprocess --scenario ...
    floodguard breach    --scenario ...
    floodguard validate  --out docs/validation
    floodguard simulate  --scenario ...

Every subcommand that is not yet implemented says so explicitly and exits
non-zero. It never prints a plausible-looking fake result.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data"

_PHASE_OF = {
    "breach": ("Phase 3", "floodguard.breach"),
    "simulate": ("Phase 4/5", "floodguard.engines + postprocess"),
    "impact": ("Phase 6", "floodguard.impact"),
    "report": ("Phase 10", "PDF report generator"),
    "demo": ("Phase 10", "precomputed demo bundle"),
}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_scenario(path: str | None):
    from floodguard.scenario import Scenario

    if not path:
        raise SystemExit("--scenario is required for this command")
    return Scenario.from_yaml(path)


# --- commands ---------------------------------------------------------------------


def cmd_engines(_args) -> int:
    """Print the truthful engine availability table for this machine."""
    from floodguard.engines.availability import probe_all

    rows = probe_all()
    width = max(len(r.id) for r in rows)
    print(f"{'ENGINE'.ljust(width)}  {'AVAILABLE':<10} DISPLAY NAME / DETAIL")
    print("-" * 100)
    for r in rows:
        mark = "yes" if r.available else "NO"
        version = f" v{r.version}" if r.version else ""
        print(f"{r.id.ljust(width)}  {mark:<10} {r.display_name}{version}")
        print(f"{' ' * width}  {'':<10} {r.detail}")
        if r.substitute_id:
            print(f"{' ' * width}  {'':<10} -> would substitute: {r.substitute_id}")
        print()
    return 0


def cmd_data(args) -> int:
    """Phase 1: fetch and cache every input layer for a scenario."""
    from floodguard.data.acquire import acquire, derive_aoi, layer_table

    scenario = _load_scenario(args.scenario)
    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)

    aoi = derive_aoi(scenario)
    print(f"Scenario : {scenario.id} — {scenario.name}")
    print(f"Dam      : {scenario.dam.name} ({scenario.dam.lat:.4f}N, {scenario.dam.lon:.4f}E)")
    print(f"River    : {scenario.dam.river}")
    print(f"AOI      : W{aoi[0]:.3f} S{aoi[1]:.3f} E{aoi[2]:.3f} N{aoi[3]:.3f}")
    print(f"Target   : {scenario.utm_crs} @ {scenario.domain.resolution_m:.0f} m")
    print()

    result = acquire(
        scenario,
        data_dir,
        skip_population=args.skip_population,
        skip_osm=args.skip_osm,
        mosaic=not args.no_mosaic,
    )

    print()
    print(layer_table(data_dir))

    if result.skipped:
        print("\nNOT FETCHED — these will be reported as 'not computed', never as zero:")
        for name, why in result.skipped.items():
            print(f"  {name}: {why}")

    if result.dem:
        print(f"\nDEM source used: {result.dem.source}")
        for failure in result.dem.failures:
            print(f"  tried first, failed: {failure}")
    if result.dem_mosaic:
        print(f"DEM mosaic     : {result.dem_mosaic}")
    if result.population:
        print(f"\nPopulation assumption carried into every report:\n  {result.population.assumption_note()}")

    return 0


def cmd_verify(args) -> int:
    """Re-hash every cached input and report any that drifted."""
    from floodguard.data.acquire import verify

    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)
    problems = verify(data_dir)
    if not problems:
        print("MANIFEST verified: every recorded file is present and hashes match.")
        return 0
    print("MANIFEST verification FAILED:", file=sys.stderr)
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    return 1


def cmd_preprocess(args) -> int:
    """Phase 2: condition the DEM, trace the corridor, derive the reservoir curve."""
    from floodguard.preprocess.pipeline import run_preprocess

    scenario = _load_scenario(args.scenario)
    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)
    result = run_preprocess(scenario, data_dir)
    print(result.summary())
    return 0


def cmd_validate(args) -> int:
    """Phase 4.5: analytical and benchmark verification of the solver."""
    from floodguard.validation.run import run_validation

    out = Path(args.out or (REPO_ROOT / "docs" / "validation"))
    return run_validation(out, quick=args.quick)


def _not_yet(name: str) -> int:
    phase, module = _PHASE_OF[name]
    print(
        f"`floodguard {name}` is not implemented yet.\n"
        f"It arrives in {phase} ({module}). See SPEC.md section 15 for the execution order.",
        file=sys.stderr,
    )
    return 2


# --- parser -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="floodguard", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("engines", help="show which hydrodynamic engines are runnable here")

    p_data = sub.add_parser("data", help="[Phase 1] fetch and cache all input layers")
    p_data.add_argument("--scenario", required=True)
    p_data.add_argument("--data-dir")
    p_data.add_argument("--skip-population", action="store_true")
    p_data.add_argument("--skip-osm", action="store_true")
    p_data.add_argument("--no-mosaic", action="store_true")

    p_verify = sub.add_parser("verify", help="re-hash every cached input file")
    p_verify.add_argument("--data-dir")

    p_pre = sub.add_parser("preprocess", help="[Phase 2] DEM conditioning, corridor, reservoir")
    p_pre.add_argument("--scenario", required=True)
    p_pre.add_argument("--data-dir")

    p_val = sub.add_parser("validate", help="[Phase 4.5] Ritter/Stoker/lake-at-rest/mass balance")
    p_val.add_argument("--out")
    p_val.add_argument("--quick", action="store_true", help="coarser grids, for a fast check")

    for name, (phase, _module) in _PHASE_OF.items():
        p = sub.add_parser(name, help=f"[{phase}] not implemented yet")
        p.add_argument("--scenario")
        p.add_argument("--out")
        p.add_argument("--data-dir")

    return parser


DISPATCH = {
    "engines": cmd_engines,
    "data": cmd_data,
    "verify": cmd_verify,
    "preprocess": cmd_preprocess,
    "validate": cmd_validate,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    handler = DISPATCH.get(args.command)
    if handler:
        return handler(args)
    return _not_yet(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
