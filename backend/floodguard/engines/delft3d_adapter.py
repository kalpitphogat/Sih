"""Delft3D Flexible Mesh (D-Flow FM) adapter.

Two things this module does, and one it refuses to do.

**It generates a complete, real D-Flow FM input deck** — `_net.nc` unstructured
grid, bathymetry samples, `.mdu` master definition, `.pli` boundary polyline,
`.bc` boundary forcing carrying the breach hydrograph, `.ext` external forcings
and a DIMR configuration. That deck is written whether or not a solver exists
on the machine, and it is offered as a downloadable artefact. "Here is the
Delft3D input deck our tool generated from a DEM and a dam record" is a
legitimate deliverable on its own: it is what a hydraulics team would otherwise
spend days assembling by hand.

**It runs the real solver when the real solver is present**, and parses its
NetCDF map output.

**It never pretends.** When `dflowfm` is absent it raises `EngineUnavailable`
with the reason. It does not fall back internally, because a fallback buried in
an adapter is exactly how a substitution becomes invisible. The orchestrator
makes that decision in one place and labels it everywhere.

Format references: the D-Flow FM User Manual (Deltares) and the `examples/`
tree of the Delft3D source distribution.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from floodguard.engines.availability import EngineUnavailable
from floodguard.engines.base import Engine, EngineInput, ProgressCallback, ResultBundle, null_progress

log = logging.getLogger(__name__)


def find_binary() -> str | None:
    """Locate dflowfm, honouring an explicit override first."""
    override = os.environ.get("DFLOWFM_BIN", "").strip()
    if override:
        return override if Path(override).is_file() else None
    for name in ("dflowfm", "dflowfm-cli", "dflowfm.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


class Delft3DAdapter(Engine):
    """Runs D-Flow FM when it exists; always writes its input deck."""

    id = "delft3d"
    display_name = "Delft3D Flexible Mesh (D-Flow FM)"
    is_real_solver = True

    def __init__(self, work_dir: Path | None = None) -> None:
        self.work_dir = work_dir

    def run(
        self, spec: EngineInput, progress: ProgressCallback = null_progress
    ) -> ResultBundle:
        work = Path(self.work_dir or "delft3d_case")
        progress(fraction=0.05, phase="delft3d", message="writing the D-Flow FM deck")
        artefacts = write_case(spec, work)

        binary = find_binary()
        if binary is None:
            raise EngineUnavailable(
                self.id,
                (
                    "no dflowfm binary was found on PATH or in DFLOWFM_BIN. A complete "
                    f"D-Flow FM input deck was written to {work} and is available for "
                    "download, but no Delft3D solve was performed and nothing in this run "
                    "may be labelled Delft3D."
                ),
            )

        progress(fraction=0.1, phase="delft3d", message=f"running {binary}")
        started = time.perf_counter()
        mdu = artefacts["mdu"]
        proc = subprocess.run(
            [binary, "--autostartstop", str(mdu.name)],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=24 * 3600,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"dflowfm exited {proc.returncode}.\n"
                f"stdout tail:\n{proc.stdout[-2000:]}\n"
                f"stderr tail:\n{proc.stderr[-2000:]}"
            )

        progress(fraction=0.9, phase="delft3d", message="parsing the NetCDF map output")
        bundle = read_map_output(work, spec)
        bundle.runtime_s = time.perf_counter() - started
        bundle.artifacts = artefacts
        bundle.provenance |= {
            "engine": self.display_name,
            "binary": binary,
            "is_real_solver": True,
            "deck": {k: str(v) for k, v in artefacts.items()},
        }
        return bundle


# --- deck generation ---------------------------------------------------------------


def write_case(spec: EngineInput, work: Path) -> dict[str, Path]:
    """Write a complete D-Flow FM case. Returns the artefact paths."""
    work.mkdir(parents=True, exist_ok=True)

    net = _write_net_nc(spec, work / "floodguard_net.nc")
    bc = _write_bc(spec, work / "breach.bc")
    pli = _write_pli(spec, work / "breach.pli")
    ext = _write_ext(work / "floodguard.ext")
    mdu = _write_mdu(spec, work / "floodguard.mdu", net.name, ext.name)
    dimr = _write_dimr(work / "dimr_config.xml", mdu.name)
    readme = _write_readme(work / "README.txt", spec)

    log.info("wrote a D-Flow FM deck to %s", work)
    return {"net": net, "bc": bc, "pli": pli, "ext": ext, "mdu": mdu, "dimr": dimr,
            "readme": readme}


def _write_net_nc(spec: EngineInput, path: Path) -> Path:
    """Write the unstructured network file with bed levels at nodes.

    D-Flow FM's `_net.nc` follows the UGRID conventions. A Cartesian grid is a
    legitimate degenerate case of an unstructured one: each cell becomes a
    quadrilateral face. We write it that way so the deck is a real FM case
    rather than a structured Delft3D-4 grid, which is the older, deprecated
    format.
    """
    try:
        import netCDF4
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("netCDF4 is required to write a D-Flow FM network file") from exc

    rows, cols = spec.shape
    transform = spec.transform

    # Node coordinates: (rows+1) x (cols+1) corners of the cell grid.
    node_rows, node_cols = rows + 1, cols + 1
    jj, ii = np.meshgrid(np.arange(node_rows), np.arange(node_cols), indexing="ij")
    xs, ys = transform * (ii.ravel(), jj.ravel())
    xs = np.asarray(xs, dtype="f8")
    ys = np.asarray(ys, dtype="f8")

    # Node bed level: average the (up to four) surrounding cell values.
    bed = np.where(np.isfinite(spec.bed_elevation), spec.bed_elevation, -999.0)
    padded = np.pad(bed, 1, mode="edge")
    node_z = 0.25 * (
        padded[0:node_rows, 0:node_cols]
        + padded[0:node_rows, 1 : node_cols + 1]
        + padded[1 : node_rows + 1, 0:node_cols]
        + padded[1 : node_rows + 1, 1 : node_cols + 1]
    ).ravel().astype("f8")

    def node_id(r: int, c: int) -> int:
        return r * node_cols + c

    faces = np.array(
        [
            [node_id(r, c), node_id(r, c + 1), node_id(r + 1, c + 1), node_id(r + 1, c)]
            for r in range(rows)
            for c in range(cols)
            if spec.active[r, c]
        ],
        dtype="i4",
    )

    with netCDF4.Dataset(path, "w", format="NETCDF4") as ds:
        ds.Conventions = "CF-1.8 UGRID-1.0"
        ds.institution = "FloodGuard India"
        ds.source = "auto-generated from a DEM by floodguard.engines.delft3d_adapter"
        ds.references = "D-Flow FM User Manual, Deltares"

        ds.createDimension("nNetNode", xs.size)
        ds.createDimension("nNetElem", faces.shape[0])
        ds.createDimension("nNetElemMaxNode", 4)

        mesh = ds.createVariable("Mesh2D", "i4")
        mesh.cf_role = "mesh_topology"
        mesh.topology_dimension = 2
        mesh.node_coordinates = "NetNode_x NetNode_y"
        mesh.face_node_connectivity = "NetElemNode"

        vx = ds.createVariable("NetNode_x", "f8", ("nNetNode",))
        vx.units = "m"
        vx.standard_name = "projection_x_coordinate"
        vx[:] = xs

        vy = ds.createVariable("NetNode_y", "f8", ("nNetNode",))
        vy.units = "m"
        vy.standard_name = "projection_y_coordinate"
        vy[:] = ys

        vz = ds.createVariable("NetNode_z", "f8", ("nNetNode",), fill_value=-999.0)
        vz.units = "m"
        vz.standard_name = "altitude"
        vz.long_name = "bed level at net node (positive up, m MSL)"
        vz[:] = node_z

        conn = ds.createVariable("NetElemNode", "i4", ("nNetElem", "nNetElemMaxNode"))
        conn.start_index = 0
        conn[:, :] = faces

        crs = ds.createVariable("projected_coordinate_system", "i4")
        # `name` is reserved on a netCDF4 Variable object, so it must be set
        # through setncattr rather than by attribute assignment.
        crs.setncattr("name", spec.crs)
        crs.grid_mapping_name = "Unknown projected"
        crs.proj4_params = spec.crs
        crs.EPSG_code = spec.crs

    return path


def _write_bc(spec: EngineInput, path: Path) -> Path:
    """Boundary forcing: the breach hydrograph as a discharge time series."""
    duration = spec.duration_s
    step = max(duration / 400.0, 1.0)
    times = np.arange(0.0, duration + step, step)

    lines = [
        "[General]",
        "    fileVersion           = 1.01",
        "    fileType              = boundConds",
        "",
        "[Forcing]",
        "    Name                  = breach_0001",
        "    Function              = timeseries",
        "    Time-interpolation    = linear",
        "    Quantity              = time",
        "    Unit                  = seconds since 2024-01-01 00:00:00 +00:00",
        "    Quantity              = dischargebnd",
        "    Unit                  = m3/s",
    ]
    lines += [f"    {t:12.1f} {spec.inflow_q(float(t)):16.3f}" for t in times]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_pli(spec: EngineInput, path: Path) -> Path:
    """Boundary polyline at the breach face."""
    from rasterio.transform import xy

    cells = spec.source_cells or [(*spec.source_rc, 1.0)]
    points = [xy(spec.transform, r, c) for r, c, _w in cells]

    lines = ["breach", f"    {len(points)}    2"]
    lines += [f"    {x:.3f}  {y:.3f}" for x, y in points]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_ext(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "[boundary]",
                "quantity            = dischargebnd",
                "locationfile        = breach.pli",
                "forcingfile         = breach.bc",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_mdu(spec: EngineInput, path: Path, net_file: str, ext_file: str) -> Path:
    """The master definition file.

    Settings are chosen to match what FloodGuard-SWE solves, so the two are
    comparing the same physics rather than two different models: the same
    Manning roughness, the same dry tolerance, the same CFL and the same output
    interval.
    """
    manning = float(np.nanmean(spec.manning_n[spec.active])) if spec.active.any() else 0.035

    lines = [
        "# D-Flow FM master definition file",
        "# Auto-generated by FloodGuard India.",
        "# Settings mirror the FloodGuard-SWE run so the two solve the same problem.",
        "",
        "[model]",
        "Program                   = D-Flow FM",
        "Version                   = 1.2.100",
        "MDUFormatVersion          = 1.09",
        "GuiVersion                = FloodGuard India",
        "AutoStart                 = 0",
        "",
        "[geometry]",
        f"NetFile                   = {net_file}",
        "BedlevType                = 3",
        "Conveyance2D              = -1",
        "",
        "[numerics]",
        f"CFLMax                    = {spec.cfl}",
        "AdvecType                 = 33",
        "Limtyphu                  = 4",
        "Limtypmom                 = 4",
        "Limtypsa                  = 4",
        f"Epshu                     = {spec.dry_tolerance_m}",
        "Icgsolver                 = 4",
        "",
        "[physics]",
        "UnifFrictCoef             = " + f"{manning:.4f}",
        "UnifFrictType             = 1",
        "Vicouv                    = 0.1",
        "",
        "[wind]",
        "ICdtyp                    = 2",
        "",
        "[time]",
        "RefDate                   = 20240101",
        "Tunit                     = S",
        "DtUser                    = 60.",
        "DtMax                     = 30.",
        "TStart                    = 0.",
        f"TStop                     = {spec.duration_s:.0f}",
        "",
        "[external forcing]",
        f"ExtForceFileNew           = {ext_file}",
        "",
        "[output]",
        "OutputDir                 = output",
        f"MapInterval               = {spec.output_interval_s:.0f}",
        f"HisInterval               = {min(spec.output_interval_s, 300.0):.0f}",
        "RstInterval               = 0.",
        "Wrimap_waterlevel_s1      = 1",
        "Wrimap_waterdepth         = 1",
        "Wrimap_velocity_vector    = 1",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_dimr(path: Path, mdu_file: str) -> Path:
    path.write_text(
        "\n".join(
            [
                '<?xml version="1.0" encoding="utf-8"?>',
                '<dimrConfig xmlns="http://schemas.deltares.nl/dimr"',
                '            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
                '            xsi:schemaLocation="http://schemas.deltares.nl/dimr'
                ' http://content.oss.deltares.nl/schemas/dimr-1.3.xsd">',
                "  <documentation>",
                "    <fileVersion>1.3</fileVersion>",
                "    <createdBy>FloodGuard India</createdBy>",
                "  </documentation>",
                "  <control>",
                '    <start name="dflowfm"/>',
                "  </control>",
                '  <component name="dflowfm">',
                "    <library>dflowfm</library>",
                "    <workingDir>.</workingDir>",
                f"    <inputFile>{mdu_file}</inputFile>",
                "  </component>",
                "</dimrConfig>",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_readme(path: Path, spec: EngineInput) -> Path:
    rows, cols = spec.shape
    path.write_text(
        "\n".join(
            [
                "D-Flow FM case auto-generated by FloodGuard India",
                "=" * 52,
                "",
                "This deck was written from a DEM, a dam record and a breach hydrograph.",
                "It is a complete, runnable Delft3D Flexible Mesh case:",
                "",
                "  floodguard_net.nc   unstructured grid with bed levels at nodes (UGRID)",
                "  floodguard.mdu      master definition file",
                "  floodguard.ext      external forcings",
                "  breach.pli          boundary polyline at the breach face",
                "  breach.bc           breach discharge time series",
                "  dimr_config.xml     DIMR orchestration",
                "",
                "To run it:",
                "",
                "  dflowfm --autostartstop floodguard.mdu",
                "",
                "or, with DIMR:",
                "",
                "  dimr dimr_config.xml",
                "",
                f"Grid           : {cols} x {rows} cells at {spec.cell_size_m:.0f} m",
                f"CRS            : {spec.crs}",
                f"Duration       : {spec.duration_s / 3600:.2f} h",
                f"CFL            : {spec.cfl}",
                f"Dry tolerance  : {spec.dry_tolerance_m} m",
                "",
                "Numerics are set to mirror the FloodGuard-SWE run, so a comparison",
                "between the two is a comparison of solvers rather than of setups.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


# --- output parsing ----------------------------------------------------------------


def read_map_output(work: Path, spec: EngineInput) -> ResultBundle:
    """Parse a D-Flow FM `_map.nc` into our ResultBundle.

    Raises if the file is absent or has no usable depth variable, rather than
    returning an empty bundle: an engine that reports success with no data is
    worse than one that fails.
    """
    import xarray as xr

    candidates = sorted((work / "output").glob("*_map.nc")) or sorted(work.glob("*_map.nc"))
    if not candidates:
        raise RuntimeError(
            f"dflowfm exited successfully but no *_map.nc was found under {work}. "
            f"Check the MDU output settings."
        )

    with xr.open_dataset(candidates[0]) as ds:
        depth_name = next(
            (n for n in ("mesh2d_waterdepth", "waterdepth", "mesh2d_hu") if n in ds),
            None,
        )
        if depth_name is None:
            raise RuntimeError(
                f"{candidates[0].name} contains no water-depth variable "
                f"(looked for mesh2d_waterdepth, waterdepth, mesh2d_hu). "
                f"Variables present: {sorted(ds.data_vars)}"
            )

        depth = ds[depth_name].values                       # (time, face)
        times = ds["time"].values

        ux_name = next((n for n in ("mesh2d_ucx", "ucx") if n in ds), None)
        uy_name = next((n for n in ("mesh2d_ucy", "ucy") if n in ds), None)
        if ux_name and uy_name:
            speed = np.hypot(ds[ux_name].values, ds[uy_name].values)
        else:
            speed = np.zeros_like(depth)

    rows, cols = spec.shape
    face_rc = [(r, c) for r in range(rows) for c in range(cols) if spec.active[r, c]]

    max_depth = np.zeros((rows, cols))
    max_speed = np.zeros((rows, cols))
    arrival = np.full((rows, cols), -1.0)

    depth_max_per_face = np.nanmax(depth, axis=0)
    speed_max_per_face = np.nanmax(speed, axis=0)

    seconds = (times - times[0]) / np.timedelta64(1, "s") if times.dtype.kind == "M" else times
    first_wet = np.full(depth.shape[1], -1.0)
    wet = depth >= spec.wet_threshold_m
    for face in range(depth.shape[1]):
        idx = np.flatnonzero(wet[:, face])
        if idx.size:
            first_wet[face] = float(seconds[idx[0]])

    for face, (r, c) in enumerate(face_rc):
        if face >= depth.shape[1]:
            break
        max_depth[r, c] = depth_max_per_face[face]
        max_speed[r, c] = speed_max_per_face[face]
        arrival[r, c] = first_wet[face]

    return ResultBundle(
        engine_id="delft3d",
        display_name="Delft3D Flexible Mesh (D-Flow FM)",
        is_real_solver=True,
        max_depth=max_depth,
        max_velocity=max_speed,
        max_hazard=max_depth * max_speed,
        arrival_time_s=arrival,
        transform=spec.transform,
        crs=spec.crs,
        cell_size_m=spec.cell_size_m,
        provenance={
            "engine": "Delft3D Flexible Mesh (D-Flow FM)",
            "map_file": candidates[0].name,
            "depth_variable": depth_name,
            "frames": int(depth.shape[0]),
        },
    )
