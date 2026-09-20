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

_PHASE_OF: dict[str, tuple[str, str]] = {}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_scenario(
    path: str | None,
    resolution_m: float | None = None,
    duration_hours: float | None = None,
):
    """Load a scenario, optionally overriding the compute resolution.

    Resolution is the single biggest lever on runtime AND on the answer. Cost
    scales roughly as 1/res^3 — 1/res^2 in cells and another 1/res in timesteps,
    since the CFL limit shrinks with the cell size. Going from 90 m to 30 m is
    about 27x the work. It is also not merely a quality knob: dam-break peak
    depths are genuinely resolution-sensitive, because a coarse cell averages
    the channel together with its banks and under-predicts the peak. That is
    why the spec insists it be exposed rather than hidden, and why every output
    records the resolution it was computed at.
    """
    from floodguard.scenario import Scenario

    if not path:
        raise SystemExit("--scenario is required for this command")
    scenario = Scenario.from_yaml(path)
    if resolution_m or duration_hours:
        scenario = scenario.model_copy(deep=True)
    if resolution_m:
        scenario.domain.resolution_m = resolution_m
    if duration_hours:
        scenario.solver.duration_hours = duration_hours
    return scenario


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

    scenario = _load_scenario(args.scenario, args.resolution)
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

    scenario = _load_scenario(args.scenario, args.resolution)
    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)
    result = run_preprocess(scenario, data_dir)
    print(result.summary())
    return 0


def cmd_breach(args) -> int:
    """Phase 3: breach parameters, outflow hydrograph, or historical validation."""
    import json

    from floodguard.breach import parameters as bp
    from floodguard.breach import routing
    from floodguard.breach import validation as bv

    if args.validate:
        print(bv.report())
        return 0

    scenario = _load_scenario(args.scenario)
    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)

    used, predictions, stats = bp.resolve(scenario)

    print(f"Breach parameter predictions for {scenario.dam.name}")
    print(f"  head over invert {scenario.water_head_m:.1f} m, "
          f"storage {scenario.dam.gross_storage_mcm:,.0f} MCM, "
          f"type {scenario.dam.dam_type.value}\n")
    print(f"  {'MODEL':<38} {'WIDTH m':>10} {'t_f min':>10} {'SLOPE':>7}  APPLIES")
    print("  " + "-" * 78)
    for p in predictions:
        print(f"  {p.model:<38} {p.width_m:>10.0f} {p.formation_time_min:>10.1f} "
              f"{p.side_slope:>7.1f}  {'yes' if p.applicable else 'NO'}")
    print()
    print(f"  Spread: width x{stats['width_m']['spread_ratio']}, "
          f"formation time x{stats['formation_time_min']['spread_ratio']}")
    print(f"  {stats['interpretation']}")
    print()
    print(f"  USING: {used.model} — width {used.width_m:.0f} m, "
          f"depth {used.depth_m:.0f} m, t_f {used.formation_time_min:.1f} min")
    for c in used.caveats:
        print(f"    - {c}")

    # Routing needs the reservoir curve from Phase 2.
    pre_path = data_dir / "processed" / scenario.id / "preprocess.json"
    if not pre_path.exists():
        print(f"\nNo preprocess.json at {pre_path}; run `floodguard preprocess` to route "
              f"the hydrograph.", file=sys.stderr)
        return 0

    from floodguard.preprocess.reservoir import ElevationAreaCapacity
    import numpy as np

    pre = json.loads(pre_path.read_text(encoding="utf-8"))
    cd = pre["reservoir"]["curve"]
    curve = ElevationAreaCapacity(
        levels_m=np.array(cd["levels_m"]),
        areas_m2=np.array(cd["areas_km2"]) * 1e6,
        volumes_m3=np.array(cd["volumes_mcm"]) * 1e6,
        cell_area_m2=cd["cell_area_m2"],
        dam_elevation_m=cd["dam_elevation_m"],
        method=cd["method"],
    )

    crest = scenario.dam.crest_elevation_m or scenario.initial_level_m
    hydrograph = routing.route(
        curve,
        used,
        initial_level_m=scenario.initial_level_m,
        crest_elevation_m=crest,
        scenario_type=scenario.scenario_type,
        shape=scenario.breach.shape,
        growth=scenario.breach.growth,
        duration_s=scenario.solver.duration_hours * 3600.0,
        inflow_m3s=scenario.reservoir.inflow_m3s,
    )

    print()
    print(hydrograph.summary())

    out = data_dir / "processed" / scenario.id / "hydrograph.json"
    out.write_text(json.dumps(hydrograph.to_dict(), indent=2), encoding="utf-8")
    print(f"\nWritten: {out}")
    return 0


def cmd_simulate(args) -> int:
    """Phases 2-5: the full headless pipeline for one scenario."""
    from floodguard.pipeline import simulate

    scenario = _load_scenario(args.scenario, args.resolution, args.duration)
    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)

    print(f"Resolution: {scenario.domain.resolution_m:.0f} m "
          f"(cost scales as ~1/res^3; every output records this value)")

    last = {"phase": None}

    def progress(*, fraction, phase, message, **extra):
        if phase != last["phase"]:
            print(f"[{fraction * 100:5.1f}%] {phase}")
            last["phase"] = phase
        if extra.get("step") or phase == "done":
            print(f"          {message}")

    result = simulate(
        scenario,
        data_dir,
        progress=progress,
        engines=args.engines.split(",") if args.engines else None,
        export=not args.no_export,
    )
    print()
    print(result.summary())
    return 0


def cmd_report(args) -> int:
    """Phase 10: render the PDF report for a completed run."""
    from floodguard.report import build_report, write_map_previews

    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)
    runs = data_dir / "runs"
    run_dir = Path(args.run) if args.run else None

    if run_dir is None:
        candidates = sorted(
            (d for d in runs.glob("*") if (d / "result.json").exists()),
            key=lambda d: d.stat().st_mtime,
        )
        if not candidates:
            print(
                f"No completed run found under {runs}. Run `floodguard simulate` first.",
                file=sys.stderr,
            )
            return 2
        run_dir = candidates[-1]
    elif not run_dir.is_absolute():
        run_dir = runs / run_dir

    previews = write_map_previews(run_dir)
    for p in previews:
        print(f"  rendered {p.name}")

    out = build_report(run_dir)
    print(f"Report: {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


def cmd_impact(args) -> int:
    """Phase 6: print the exposure analysis for a completed run."""
    import json

    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)
    runs = data_dir / "runs"
    run_dir = Path(args.run) if args.run else None
    if run_dir is None:
        candidates = sorted(
            (d for d in runs.glob("*") if (d / "impact.json").exists()),
            key=lambda d: d.stat().st_mtime,
        )
        if not candidates:
            print(f"No run with impact.json under {runs}.", file=sys.stderr)
            return 2
        run_dir = candidates[-1]
    elif not run_dir.is_absolute():
        run_dir = runs / run_dir

    path = run_dir / "impact.json"
    if not path.exists():
        print(f"{path} does not exist.", file=sys.stderr)
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"Exposure within the inundated area ({run_dir.name})")
    for m in data["metrics"].values():
        unit = f" {m['unit']}" if m["computed"] and m["unit"] else ""
        print(f"  {m['label']:<26} {m['display']}{unit}")
        if not m["computed"]:
            print(f"  {'':<26} (not computed: {m['reason']})")
    for w in data.get("warnings", []):
        print(f"\n  WARNING: {w}")
    return 0


def cmd_demo(args) -> int:
    """Phase 10: preflight and start the full stack on precomputed results."""
    from floodguard.demo import run_demo

    return run_demo(
        Path(args.data_dir or DEFAULT_DATA_DIR),
        api_port=args.api_port,
        web_port=args.web_port,
        open_browser=not args.no_browser,
        check_only=args.check,
    )


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
    p_data.add_argument(
        "--resolution", type=float,
        help="compute grid resolution in metres, overriding the scenario",
    )
    p_data.add_argument("--skip-population", action="store_true")
    p_data.add_argument("--skip-osm", action="store_true")
    p_data.add_argument("--no-mosaic", action="store_true")

    p_verify = sub.add_parser("verify", help="re-hash every cached input file")
    p_verify.add_argument("--data-dir")

    p_pre = sub.add_parser("preprocess", help="[Phase 2] DEM conditioning, corridor, reservoir")
    p_pre.add_argument("--scenario", required=True)
    p_pre.add_argument("--data-dir")
    p_pre.add_argument(
        "--resolution", type=float,
        help="compute grid resolution in metres, overriding the scenario",
    )

    p_breach = sub.add_parser("breach", help="[Phase 3] breach parameters + outflow hydrograph")
    p_breach.add_argument("--scenario")
    p_breach.add_argument("--data-dir")
    p_breach.add_argument(
        "--validate", action="store_true",
        help="score the breach models against Teton 1976 and Banqiao 1975",
    )

    p_sim = sub.add_parser("simulate", help="[Phase 2-5] full headless pipeline")
    p_sim.add_argument("--scenario", required=True)
    p_sim.add_argument("--data-dir")
    p_sim.add_argument(
        "--resolution", type=float,
        help="compute grid resolution in metres, overriding the scenario",
    )
    p_sim.add_argument(
        "--duration", type=float, help="simulated duration in hours, overriding the scenario"
    )
    p_sim.add_argument("--engines", help="comma-separated engine ids, e.g. swe_fv,delft3d")
    p_sim.add_argument("--no-export", action="store_true")

    p_report = sub.add_parser("report", help="[Phase 10] render the PDF report for a run")
    p_report.add_argument("--run", help="run id or directory; defaults to the most recent")
    p_report.add_argument("--data-dir")

    p_impact = sub.add_parser("impact", help="[Phase 6] print the exposure analysis")
    p_impact.add_argument("--run")
    p_impact.add_argument("--data-dir")

    p_demo = sub.add_parser("demo", help="[Phase 10] preflight and start the full stack")
    p_demo.add_argument("--data-dir")
    p_demo.add_argument("--api-port", type=int, default=8000)
    p_demo.add_argument("--web-port", type=int, default=5173)
    p_demo.add_argument("--no-browser", action="store_true")
    p_demo.add_argument(
        "--check", action="store_true", help="run the preflight only and exit"
    )

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
    "breach": cmd_breach,
    "simulate": cmd_simulate,
    "report": cmd_report,
    "impact": cmd_impact,
    "demo": cmd_demo,
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
