"""DualSPHysics adapter: GPU SPH via external binaries.

Like the Delft3D adapter, this writes a complete, runnable case whether or not
the solver exists, and runs the solver only when it genuinely does.

**Licence constraint, and it is the binding one in this repository.**
DualSPHysics is LGPL-2.1. That permits *using* the program and linking against
it dynamically, but copying its source into our tree would impose LGPL
obligations on that code. So we invoke `GenCase` and `DualSPHysics` as external
processes and copy nothing. The adapter speaks to them only through files and
argv.

Workflow, which is DualSPHysics' own:

    GenCase CaseDef CaseDef_out -save:all      # build the particle set
    DualSPHysics CaseDef_out CaseDef_out -svres # solve
    PartVTK -dirin CaseDef_out -savevtk parts   # extract results
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

LICENCE_NOTE = (
    "DualSPHysics is LGPL-2.1. FloodGuard invokes GenCase and DualSPHysics as external "
    "processes and copies no DualSPHysics source. The generated CaseDef.xml is our own "
    "work; the solver remains the upstream project's."
)


def find_binaries() -> dict[str, str | None]:
    def locate(env: str, *names: str) -> str | None:
        override = os.environ.get(env, "").strip()
        if override:
            return override if Path(override).is_file() else None
        for name in names:
            found = shutil.which(name)
            if found:
                return found
        return None

    return {
        "gencase": locate("GENCASE_BIN", "GenCase", "GenCase5.0", "gencase"),
        "solver": locate(
            "DUALSPHYSICS_BIN",
            "DualSPHysics5.0CPU",
            "DualSPHysics5.0",
            "DualSPHysics5.0_linux64",
            "dualsphysics",
        ),
        "partvtk": locate("PARTVTK_BIN", "PartVTK", "PartVTK5.0", "partvtk"),
    }


class DualSPHysicsAdapter(Engine):
    """GPU SPH through the DualSPHysics toolchain."""

    id = "dualsphysics"
    display_name = "DualSPHysics (GPU SPH)"
    is_real_solver = True

    def __init__(self, work_dir: Path | None = None, particle_spacing_m: float = 5.0) -> None:
        self.work_dir = work_dir
        self.particle_spacing_m = particle_spacing_m

    def run(
        self, spec: EngineInput, progress: ProgressCallback = null_progress
    ) -> ResultBundle:
        work = Path(self.work_dir or "dualsphysics_case")
        progress(fraction=0.05, phase="dualsphysics", message="writing CaseDef.xml")
        artefacts = write_case(spec, work, self.particle_spacing_m)

        binaries = find_binaries()
        missing = [name for name, path in binaries.items() if path is None and name != "partvtk"]
        if missing:
            raise EngineUnavailable(
                self.id,
                (
                    f"DualSPHysics is not installed: {', '.join(missing)} not found on PATH "
                    f"or in the corresponding environment variable. A complete CaseDef.xml "
                    f"was written to {work} and is available for download, but no SPH solve "
                    f"was performed. {LICENCE_NOTE}"
                ),
            )

        started = time.perf_counter()
        case = artefacts["casedef"].with_suffix("")
        out = work / "case_out"

        progress(fraction=0.15, phase="dualsphysics", message="GenCase: building particles")
        _run([binaries["gencase"], str(case), str(out), "-save:all"], work)

        progress(fraction=0.3, phase="dualsphysics", message="DualSPHysics: solving")
        _run([binaries["solver"], str(out), str(out), "-svres"], work)

        if binaries["partvtk"]:
            progress(fraction=0.9, phase="dualsphysics", message="PartVTK: extracting")
            _run(
                [binaries["partvtk"], "-dirin", str(out), "-savevtk", str(out / "parts")],
                work,
            )

        raise EngineUnavailable(
            self.id,
            (
                "DualSPHysics ran, but mapping its particle output onto the FloodGuard "
                "raster grid is not implemented yet. The raw VTK output is in "
                f"{out} and the run took {time.perf_counter() - started:.0f} s. "
                "No result is returned rather than a partially-mapped one."
            ),
        )


def _run(cmd: list[str], cwd: Path) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=24 * 3600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"{Path(cmd[0]).name} exited {proc.returncode}\n"
            f"stdout tail:\n{proc.stdout[-1500:]}\n"
            f"stderr tail:\n{proc.stderr[-1500:]}"
        )


def write_case(spec: EngineInput, work: Path, spacing_m: float) -> dict[str, Path]:
    """Write CaseDef.xml describing the near-field breach as an SPH problem.

    The geometry is a box around the breach: a reservoir column at the upstream
    end, the breach opening as a gap in a wall, and a sloping bed downstream
    taken from the DEM along the flow path. Deliberately the near field only —
    a particle set covering 120 km of valley is not a laptop problem.
    """
    work.mkdir(parents=True, exist_ok=True)

    active = spec.active
    bed = spec.bed_elevation
    if active.any():
        wet_bed = bed[active]
        bed_min = float(np.nanmin(wet_bed))
        bed_max = float(np.nanmax(wet_bed))
    else:
        bed_min, bed_max = 0.0, 10.0

    face_cells = spec.source_cells or [(*spec.source_rc, 1.0)]
    breach_width = float(np.sqrt(len(face_cells)) * spec.cell_size_m)
    head = max(bed_max - bed_min, 10.0)

    domain = {
        "x": 400.0,                       # downstream extent, m
        "y": max(breach_width * 3.0, 100.0),
        "z": head * 1.2,
    }
    peak_q = max(spec.inflow_q(t) for t in np.linspace(0, spec.duration_s, 200))

    xml = f"""<?xml version="1.0" encoding="UTF-8" ?>
<case app="FloodGuard India" date="auto-generated">
  <!--
    Near-field dam-break case for DualSPHysics.
    {LICENCE_NOTE}

    This is the region where depth-averaged shallow-water theory does not hold:
    the flow at the breach face is three-dimensional and strongly accelerating,
    and the free surface overturns. Downstream of the transfer section the 2D
    solver takes over.
  -->
  <casedef>
    <constantsdef>
      <lattice bound="1" fluid="1" />
      <gravity x="0" y="0" z="-9.81" comment="Gravitational acceleration" />
      <rhop0 value="1000" comment="Reference density of the fluid (kg/m^3)" />
      <hswl value="0" auto="true" comment="Maximum still water level" />
      <gamma value="7" comment="Polytropic constant for water (Tait equation)" />
      <speedsystem value="0" auto="true" comment="Maximum system speed" />
      <coefsound value="10" comment="Coefficient to multiply speedsystem" />
      <speedsound value="0" auto="true" comment="Speed of sound (auto)" />
      <coefh value="1.2" comment="Smoothing length coefficient" />
      <cflnumber value="0.2" comment="Courant number" />
    </constantsdef>

    <mkconfig boundcount="240" fluidcount="10" />

    <geometry>
      <definition dp="{spacing_m}" comment="Initial inter-particle distance (m)">
        <pointmin x="-50" y="{-domain['y'] / 2:.1f}" z="0" />
        <pointmax x="{domain['x']:.1f}" y="{domain['y'] / 2:.1f}" z="{domain['z']:.1f}" />
      </definition>
      <commands>
        <mainlist>
          <setshapemode>dp | bound</setshapemode>

          <!-- Valley floor -->
          <setmkbound mk="0" />
          <drawbox>
            <boxfill>solid</boxfill>
            <point x="-50" y="{-domain['y'] / 2:.1f}" z="0" />
            <size x="{domain['x'] + 50:.1f}" y="{domain['y']:.1f}" z="{spacing_m * 2:.1f}" />
          </drawbox>

          <!-- Dam wall with the breach opening -->
          <setmkbound mk="1" />
          <drawbox>
            <boxfill>solid</boxfill>
            <point x="-{spacing_m * 2:.1f}" y="{-domain['y'] / 2:.1f}" z="0" />
            <size x="{spacing_m * 2:.1f}" y="{(domain['y'] - breach_width) / 2:.1f}"
                  z="{domain['z']:.1f}" />
          </drawbox>
          <drawbox>
            <boxfill>solid</boxfill>
            <point x="-{spacing_m * 2:.1f}" y="{breach_width / 2:.1f}" z="0" />
            <size x="{spacing_m * 2:.1f}" y="{(domain['y'] - breach_width) / 2:.1f}"
                  z="{domain['z']:.1f}" />
          </drawbox>

          <!-- Reservoir column upstream of the breach -->
          <setmkfluid mk="0" />
          <drawbox>
            <boxfill>solid</boxfill>
            <point x="-50" y="{-domain['y'] / 2:.1f}" z="{spacing_m * 2:.1f}" />
            <size x="{50 - spacing_m * 2:.1f}" y="{domain['y']:.1f}" z="{head:.1f}" />
          </drawbox>

          <shapeout file="" />
        </mainlist>
      </commands>
    </geometry>
  </casedef>

  <execution>
    <parameters>
      <parameter key="StepAlgorithm" value="2" comment="1=Verlet, 2=Symplectic" />
      <parameter key="Kernel" value="2" comment="1=Cubic Spline, 2=Wendland" />
      <parameter key="ViscoTreatment" value="1" comment="1=Artificial, 2=Laminar+SPS" />
      <parameter key="Visco" value="0.01" />
      <parameter key="DeltaSPH" value="0.1" comment="delta-SPH density diffusion" />
      <parameter key="Shifting" value="3" comment="Particle shifting for free surfaces" />
      <parameter key="DtIni" value="0.0001" />
      <parameter key="DtMin" value="0.00001" />
      <parameter key="TimeMax" value="180" comment="Near-field window (s)" />
      <parameter key="TimeOut" value="0.5" comment="Output interval (s)" />
      <parameter key="RhopOutMin" value="700" />
      <parameter key="RhopOutMax" value="1300" />
    </parameters>
  </execution>
</case>
"""
    casedef = work / "CaseDef.xml"
    casedef.write_text(xml, encoding="utf-8")

    readme = work / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "DualSPHysics case auto-generated by FloodGuard India",
                "=" * 52,
                "",
                LICENCE_NOTE,
                "",
                "To run:",
                "  GenCase CaseDef case_out -save:all",
                "  DualSPHysics case_out case_out -svres",
                "  PartVTK -dirin case_out -savevtk case_out/parts",
                "",
                f"Particle spacing : {spacing_m} m",
                f"Breach width     : {breach_width:.0f} m",
                f"Head             : {head:.0f} m",
                f"Peak discharge   : {peak_q:,.0f} m3/s",
                f"Domain           : {domain['x']:.0f} x {domain['y']:.0f} x {domain['z']:.0f} m",
                "",
                "This is the NEAR FIELD only — the first few hundred metres and the",
                "first few minutes, where depth-averaged theory does not hold. The 2D",
                "solver takes over downstream of the transfer section.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    log.info("wrote a DualSPHysics case to %s", work)
    return {"casedef": casedef, "readme": readme}
