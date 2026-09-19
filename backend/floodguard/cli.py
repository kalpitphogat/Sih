"""FloodGuard command line interface.

    python -m floodguard.cli engines
    python -m floodguard.cli data --scenario data/scenarios/tehri_bhagirathi.yaml

Every subcommand that is not yet implemented says so explicitly and exits
non-zero. It never prints a plausible-looking fake result.
"""

from __future__ import annotations

import argparse
import sys

_PHASE_OF = {
    "data": ("Phase 1", "floodguard.data fetchers"),
    "preprocess": ("Phase 2", "floodguard.preprocess"),
    "breach": ("Phase 3", "floodguard.breach"),
    "simulate": ("Phase 4/5", "floodguard.engines + postprocess"),
    "validate": ("Phase 4.5", "floodguard.validation"),
    "impact": ("Phase 6", "floodguard.impact"),
    "report": ("Phase 10", "PDF report generator"),
    "demo": ("Phase 10", "precomputed demo bundle"),
}


def cmd_engines(_args: argparse.Namespace) -> int:
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


def _not_yet(name: str) -> int:
    phase, module = _PHASE_OF[name]
    print(
        f"`floodguard {name}` is not implemented yet.\n"
        f"It arrives in {phase} ({module}). See SPEC.md section 15 for the execution order.",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="floodguard", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("engines", help="show which hydrodynamic engines are runnable here")

    for name, (phase, _module) in _PHASE_OF.items():
        p = sub.add_parser(name, help=f"[{phase}] not implemented yet")
        p.add_argument("--scenario", help="path to a scenario YAML")
        p.add_argument("--out", help="output directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "engines":
        return cmd_engines(args)
    return _not_yet(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
