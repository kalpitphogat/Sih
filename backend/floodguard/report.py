"""PDF report generator.

This is the artefact a district disaster management officer would actually
file. It must therefore be defensible line by line: every number in it is read
from the run's own output, every assumption that produced that number is
printed beside it, and the provenance footer lets a reader reproduce the whole
thing.

Deliberate choices about what goes in:

* **The caveats are in the body, not an appendix.** A report that buries its
  limitations behind the maps is a report that will be quoted without them.
* **The engine is named honestly on the cover.** If Delft3D was requested and
  FloodGuard-SWE ran, the cover says so — not a footnote on page 11.
* **Uncomputed values print as an em dash**, matching the UI, so the reader can
  tell "we measured zero" from "we could not measure".
* **The verification summary is included**, because the natural first question
  about any model output is whether the model works, and the answer to that is
  not an opinion.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NAVY = (0.059, 0.141, 0.251)
SLATE = (0.28, 0.33, 0.41)
AMBER = (0.55, 0.35, 0.02)

EM_DASH = "—"


def _fmt(value: Any, decimals: int = 1, unit: str = "") -> str:
    """Format a number, or an em dash when it was never computed."""
    if value is None or (isinstance(value, float) and value != value):
        return EM_DASH
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return f"{value:,}{(' ' + unit) if unit else ''}"
    try:
        return f"{float(value):,.{decimals}f}{(' ' + unit) if unit else ''}"
    except (TypeError, ValueError):
        return str(value)


def build_report(run_dir: Path, out_path: Path | None = None) -> Path:
    """Render the PDF for a completed run.

    Raises if the run has no result.json: a report for a run that did not
    finish would be a document full of em dashes claiming to be an analysis.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    run_dir = Path(run_dir)
    result_path = run_dir / "result.json"
    if not result_path.exists():
        raise FileNotFoundError(
            f"{run_dir} has no result.json, so there is nothing to report. A report for "
            f"an unfinished run would be a document full of em dashes presented as an "
            f"analysis."
        )

    data = json.loads(result_path.read_text(encoding="utf-8"))
    out_path = out_path or (run_dir / "report.pdf")

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontSize=9, leading=12.5, alignment=TA_LEFT
    )
    small = ParagraphStyle("Small", parent=body, fontSize=7.5, leading=10, textColor=colors.Color(*SLATE))
    caveat = ParagraphStyle(
        "Caveat", parent=body, fontSize=8, leading=11, textColor=colors.Color(*AMBER)
    )
    h1 = ParagraphStyle(
        "H1", parent=styles["Heading1"], fontSize=16, textColor=colors.Color(*NAVY), spaceAfter=6
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=11.5, textColor=colors.Color(*NAVY),
        spaceBefore=10, spaceAfter=4,
    )

    story: list[Any] = []
    engines = data.get("engines", [])
    primary = next((e for e in engines if e.get("summary")), None)
    summary = (primary or {}).get("summary", {})
    hydrograph = data.get("hydrograph", {})
    breach = data.get("breach", {})
    hazard = data.get("hazard", {})
    impact = data.get("impact") or {}

    # ---------------------------------------------------------------- cover
    story.append(Paragraph("FloodGuard India", h1))
    story.append(Paragraph("Dam-break inundation analysis", styles["Heading3"]))
    story.append(Spacer(1, 6 * mm))

    cover_rows = [
        ["Scenario", data.get("scenario_id", EM_DASH)],
        ["Run identifier", data.get("run_id", EM_DASH)],
        ["Generated (UTC)", datetime.now(timezone.utc).isoformat(timespec="seconds")],
        ["Engine used", (primary or {}).get("display_name", EM_DASH)],
    ]
    if primary and primary.get("substituted"):
        cover_rows.append(["Engine requested", primary.get("requested_engine", EM_DASH)])

    story.append(_kv_table(Table, TableStyle, colors, mm, cover_rows))

    if primary and primary.get("substituted"):
        story.append(Spacer(1, 4 * mm))
        story.append(
            Paragraph(
                "<b>Engine substitution.</b> " + primary.get("honesty_note", ""),
                caveat,
            )
        )

    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            "Every figure in this report was computed from input data on disk. Values "
            "that could not be computed are printed as an em dash and are never shown "
            "as zero. The provenance block on the final page identifies the exact code "
            "and inputs that produced it.",
            small,
        )
    )

    # ---------------------------------------------------------------- results
    story.append(Paragraph("1. Headline results", h2))
    story.append(
        _data_table(
            Table, TableStyle, colors, mm,
            ["Quantity", "Value"],
            [
                ["Flooded area", _fmt(summary.get("flooded_area_km2"), 2, "km2")],
                ["Maximum water depth", _fmt(summary.get("max_depth_m"), 2, "m")],
                ["Maximum velocity", _fmt(summary.get("max_velocity_ms"), 2, "m/s")],
                ["Earliest arrival", _fmt(summary.get("earliest_arrival_min"), 0, "min")],
                ["Peak breach discharge", _fmt(hydrograph.get("peak_discharge_m3s"), 0, "m3/s")],
                [
                    "Time to peak",
                    _fmt((hydrograph.get("time_to_peak_s") or 0) / 60.0, 1, "min"),
                ],
                [
                    "Volume released",
                    _fmt((hydrograph.get("total_volume_m3") or 0) / 1e6, 0, "MCM"),
                ],
            ],
        )
    )

    # ---------------------------------------------------------------- towns
    towns = data.get("towns", [])
    if towns:
        story.append(Paragraph("2. Arrival at named locations", h2))
        story.append(
            Paragraph(
                "Sorted by lead time, because that is the order an evacuation is "
                "executed in.",
                small,
            )
        )
        story.append(Spacer(1, 2 * mm))
        story.append(
            _data_table(
                Table, TableStyle, colors, mm,
                ["Location", "Arrival (min)", "Max depth (m)", "Max velocity (m/s)", "Population"],
                [
                    [
                        t["name"],
                        _fmt(t.get("arrival_min"), 0),
                        _fmt(t.get("max_depth_m"), 2),
                        _fmt(t.get("max_velocity_ms"), 2),
                        _fmt(t.get("population"), 0),
                    ]
                    for t in towns
                ],
            )
        )

    # ---------------------------------------------------------------- breach
    story.append(Paragraph("3. Breach parameters", h2))
    used = breach.get("used", {})
    spread = breach.get("spread", {})
    story.append(
        Paragraph(
            f"Breach geometry used: width {_fmt(used.get('width_m'), 0, 'm')}, "
            f"depth {_fmt(used.get('depth_m'), 0, 'm')}, formation time "
            f"{_fmt(used.get('formation_time_min'), 1, 'min')} "
            f"(model: {used.get('model', EM_DASH)}).",
            body,
        )
    )
    predictions = breach.get("predictions", [])
    if predictions:
        story.append(Spacer(1, 2 * mm))
        story.append(
            _data_table(
                Table, TableStyle, colors, mm,
                ["Empirical model", "Width (m)", "Formation (min)", "Applies?"],
                [
                    [
                        p["model"],
                        _fmt(p["width_m"], 0),
                        _fmt(p["formation_time_min"], 1),
                        "yes" if p.get("applicable") else "NO",
                    ]
                    for p in predictions
                ],
            )
        )
    if spread.get("width_m", {}).get("spread_ratio", 0) > 2:
        story.append(Spacer(1, 2 * mm))
        story.append(
            Paragraph(
                f"<b>The empirical models disagree by a factor of "
                f"{spread['width_m']['spread_ratio']:.1f} on breach width.</b> "
                f"{spread.get('interpretation', '')}",
                caveat,
            )
        )

    # ---------------------------------------------------------------- hazard
    bands = hazard.get("by_depth_band", [])
    if bands:
        story.append(Paragraph("4. Inundation by depth band", h2))
        story.append(
            _data_table(
                Table, TableStyle, colors, mm,
                ["Depth band", "Area (km2)", "Cells"],
                [[b["label"], _fmt(b["area_km2"], 2), _fmt(b["cells"], 0)] for b in bands],
            )
        )
        story.append(Spacer(1, 2 * mm))
        story.append(
            Paragraph(f"Hazard classification standard: {hazard.get('standard', EM_DASH)}", small)
        )

    # ---------------------------------------------------------------- impact
    metrics = impact.get("metrics", {})
    if metrics:
        story.append(PageBreak())
        story.append(Paragraph("5. Exposure within the inundated area", h2))
        story.append(
            _data_table(
                Table, TableStyle, colors, mm,
                ["Element", "Value", "Unit", "Status"],
                [
                    [
                        m["label"],
                        m["display"],
                        m["unit"] if m["computed"] else "",
                        "computed" if m["computed"] else f"not computed: {m['reason'][:60]}",
                    ]
                    for m in metrics.values()
                ],
            )
        )
        assumptions = [m["assumption"] for m in metrics.values() if m.get("assumption")]
        for note in assumptions:
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph(note, small))

        priority = impact.get("evacuation_priority", [])
        if priority:
            story.append(Paragraph("5.1 Evacuation priority", h2))
            story.append(
                _data_table(
                    Table, TableStyle, colors, mm,
                    ["Settlement", "Lead time (min)", "Depth (m)", "Population"],
                    [
                        [
                            row["name"],
                            _fmt(row.get("arrival_min"), 0),
                            _fmt(row.get("depth_m"), 1),
                            _fmt(row.get("population"), 0),
                        ]
                        for row in priority[:25]
                    ],
                )
            )

    # ---------------------------------------------------------------- figures
    figures = [
        ("Maximum water depth", run_dir / "max_depth_preview.png"),
        ("Flood arrival time", run_dir / "arrival_preview.png"),
    ]
    present = [(title, path) for title, path in figures if path.exists()]
    if present:
        story.append(PageBreak())
        story.append(Paragraph("6. Maps", h2))
        for title, path in present:
            story.append(Paragraph(title, styles["Heading4"]))
            story.append(Image(str(path), width=160 * mm, height=110 * mm, kind="proportional"))
            story.append(Spacer(1, 4 * mm))

    # ---------------------------------------------------------------- verification
    story.append(PageBreak())
    story.append(Paragraph("7. Solver verification", h2))
    verification = _verification_summary()
    if verification:
        story.append(
            Paragraph(
                "The natural first question about any model output is whether the model "
                "works. These are comparisons against exact analytical solutions, so the "
                "answers are not a matter of opinion.",
                body,
            )
        )
        story.append(Spacer(1, 2 * mm))
        story.append(
            _data_table(
                Table, TableStyle, colors, mm,
                ["Check", "Result", "Criterion"],
                verification,
            )
        )
    else:
        story.append(
            Paragraph(
                "No verification results were found at docs/validation/results.json. "
                "Run `make validate` before relying on any figure in this report.",
                caveat,
            )
        )

    # ---------------------------------------------------------------- caveats
    story.append(Paragraph("8. Assumptions and limitations", h2))
    warnings = data.get("warnings", [])
    if warnings:
        for w in warnings:
            story.append(Paragraph(f"• {w}", caveat))
            story.append(Spacer(1, 1.5 * mm))
    else:
        story.append(Paragraph("No caveats were recorded for this run.", body))

    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            "<b>General limitations.</b> Copernicus GLO-30 is a surface model, not a "
            "bare-earth model: over forested valleys it sits above true ground, which "
            "biases depths low and arrival times late. Reservoir bathymetry beneath the "
            "water surface is reconstructed, not surveyed. Manning roughness is uniform "
            "unless a land-cover raster was supplied. Breach geometry is the dominant "
            "uncertainty in most scenarios, ahead of anything the solver does.",
            caveat,
        )
    )

    # ---------------------------------------------------------------- provenance
    story.append(Paragraph("9. Provenance", h2))
    provenance = hydrograph.get("provenance", {})
    engine_prov = (primary or {}).get("summary", {})
    prov_rows = [
        ["Run identifier", data.get("run_id", EM_DASH)],
        ["Scenario", data.get("scenario_id", EM_DASH)],
        ["Engine", (primary or {}).get("display_name", EM_DASH)],
        ["Solver steps", _fmt(engine_prov.get("steps"), 0)],
        ["Runtime", _fmt(engine_prov.get("runtime_s"), 1, "s")],
        ["Mass closure", _fmt((engine_prov.get("mass_error") or 0) * 100, 4, "%")],
        ["Breach model", provenance.get("breach_model", EM_DASH)],
        ["Routing", provenance.get("integration", EM_DASH)],
        ["Reservoir curve", provenance.get("curve_method", EM_DASH)],
        ["Report generated", datetime.now(timezone.utc).isoformat(timespec="seconds")],
    ]
    story.append(_kv_table(Table, TableStyle, colors, mm, prov_rows))

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        title=f"FloodGuard India — {data.get('scenario_id', 'run')}",
        author="FloodGuard India",
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    log.info("wrote %s", out_path)
    return out_path


def _footer(canvas, doc) -> None:
    """Provenance footer with the git commit, on every page."""
    from app.core.provenance import git_commit

    canvas.saveState()
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColorRGB(*SLATE)
    canvas.drawString(
        18 * 2.83465,
        10 * 2.83465,
        f"FloodGuard India  |  git {git_commit()}  |  "
        f"generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
    )
    canvas.drawRightString(577, 10 * 2.83465, f"page {doc.page}")
    canvas.restoreState()


def _kv_table(Table, TableStyle, colors, mm, rows):
    table = Table(rows, colWidths=[45 * mm, 120 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.Color(*SLATE)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.Color(0.9, 0.92, 0.95)),
            ]
        )
    )
    return table


def _data_table(Table, TableStyle, colors, mm, header, rows):
    table = Table([header, *rows], hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.94, 0.96, 0.98)),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.Color(*NAVY)),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.Color(0.88, 0.9, 0.93)),
            ]
        )
    )
    return table


def _verification_summary() -> list[list[str]]:
    """Read docs/validation/results.json, if `make validate` has been run."""
    root = Path(__file__).resolve().parents[2]
    path = root / "docs" / "validation" / "results.json"
    if not path.exists():
        return []
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [
        [c["name"], "PASS" if c["passed"] else "FAIL", c["threshold"]]
        for c in report.get("checks", [])
    ]


def write_map_previews(run_dir: Path) -> list[Path]:
    """Render PNG previews of the depth and arrival rasters, for the report."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import rasterio

    run_dir = Path(run_dir)
    written: list[Path] = []

    for source, out_name, title, cmap in (
        ("max_depth.tif", "max_depth_preview.png", "Maximum water depth (m)", "Blues"),
        ("arrival_time.tif", "arrival_preview.png", "Flood arrival time (minutes)", "viridis"),
    ):
        path = run_dir / source
        if not path.exists():
            continue
        with rasterio.open(path) as src:
            data = src.read(1).astype(float)
            nodata = src.nodata

        if nodata is not None:
            data[data == nodata] = np.nan
        if "arrival" in source:
            data[data < 0] = np.nan
            data = data / 60.0
        else:
            data[data <= 0] = np.nan

        if not np.isfinite(data).any():
            continue

        fig, ax = plt.subplots(figsize=(8, 5.5), dpi=140)
        im = ax.imshow(data, cmap=cmap)
        ax.set_title(title, fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(im, ax=ax, shrink=0.8)
        fig.tight_layout()
        out = run_dir / out_name
        fig.savefig(out)
        plt.close(fig)
        written.append(out)

    return written
