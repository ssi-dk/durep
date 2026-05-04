from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from durep.analytics import (
    DrilldownNode,
    GlobalMetrics,
    PathDelta,
    ProjectSample,
    ProjectTimeSeries,
)
from durep.metadata import ProjectMetadata, ProjectName
from durep.ncdu import NcduDir, NcduRun, path_str

_PKG_DIR = Path(__file__).parent
D3_JS = (_PKG_DIR / "d3.v7.min.js").read_text()
SUNBURST_JS = (_PKG_DIR / "sunburst.js").read_text()
STACKED_AREA_JS = (_PKG_DIR / "stacked_area.js").read_text()


def format_bytes(n: int) -> str:
    sign = "-" if n < 0 else ""
    magnitude = abs(n)
    if magnitude < 1000:
        return f"{sign}{magnitude} B"

    m = float(magnitude)
    units = list("KMGTPE")
    unit_index = -1
    while m >= 1000 and unit_index < len(units) - 1:
        m /= 1000
        unit_index += 1

    while True:
        # Always show 4 significant digits with trailing zeros.
        if m >= 100:
            decimals = 1
        elif m >= 10:
            decimals = 2
        else:
            decimals = 3

        rounded = round(m, decimals)
        if rounded < 1000 or unit_index >= len(units) - 1:
            return f"{sign}{rounded:.{decimals}f} {units[unit_index]}B"

        m = rounded / 1000
        unit_index += 1

    assert False


def format_timestamp(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def format_overview_growth_pct(earliest: int, latest: int) -> str:
    growth = latest - earliest
    if earliest > 0:
        pct = (growth / earliest) * 100
        if pct > 1000:
            return "> 1000%"
        return f"{pct:+.1f}%"
    if latest > 0:
        return "new"
    return "0.0%"


def render_text_report(
    current_run: NcduRun,
    previous_run: NcduRun | None,
    metrics: GlobalMetrics,
    deltas: dict[str, PathDelta] | None,
    top_n: int,
) -> str:
    root = current_run.root
    lines: list[str] = []

    # Header
    lines.append(f"Disk usage report: {path_str(root)}")
    lines.append(f"Current scan:  {format_timestamp(current_run.timestamp)}")
    if previous_run is not None:
        lines.append(f"Previous scan: {format_timestamp(previous_run.timestamp)}")
    lines.append(f"Generated:     {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Summary
    lines.append("Summary")
    lines.append(f"  Total disk usage:  {format_bytes(metrics.total_usage_bytes)}")
    lines.append(f"  Total files:       {metrics.total_files}")
    lines.append(f"  Total directories: {root.total_directories}")
    lines.append("")

    # Uncompressed data breakdown
    u = metrics.total_uncompressed
    if u.total_size:
        lines.append(f"Uncompressed data: {format_bytes(u.total_size)}")
        if u.fasta:
            lines.append(f"  FASTA: {format_bytes(u.fasta)}")
        if u.fastq:
            lines.append(f"  FASTQ: {format_bytes(u.fastq)}")
        if u.sam:
            lines.append(f"  SAM:   {format_bytes(u.sam)}")
        if u.vcf:
            lines.append(f"  VCF:   {format_bytes(u.vcf)}")
        if u.other:
            lines.append(f"  Other: {format_bytes(u.other)}")
        lines.append("")

    # Top N directories by direct file size (excludes subdirectory contributions)
    ranked = _collect_dirs_by_direct_bytes(root)
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    top_dirs = ranked[:top_n]
    lines.append(f"Top {len(top_dirs)} directories by direct file size")
    lines.append(f"  {'Self':>12s}  {'Total':>12s}  Path")
    for node, direct in top_dirs:
        lines.append(
            f"  {format_bytes(direct):>12s}  {format_bytes(node.total_bytes):>12s}  {path_str(node)}"
        )
    lines.append("")

    # Changes
    lines.append("Changes since previous scan")
    if deltas is None:
        lines.append("  Previous scan not available.")
    else:
        direct_deltas = _compute_direct_deltas(root, deltas)
        net = sum(d.delta_bytes for d in deltas.values() if d.path == path_str(root))
        lines.append(f"  Net change: {format_bytes(net)}")
        lines.append("")

        growing = sorted(
            [(d, dd) for d, dd in direct_deltas if dd > 0],
            key=lambda pair: pair[1],
            reverse=True,
        )
        shrinking = sorted(
            [(d, dd) for d, dd in direct_deltas if dd < 0],
            key=lambda pair: pair[1],
        )

        if growing:
            lines.append(
                f"  Top {min(top_n, len(growing))} growing directories (by direct file size)"
            )
            for d, direct in growing[:top_n]:
                lines.append(
                    f"    {'+' + format_bytes(direct):>13s}"
                    f"  {'+' + format_bytes(d.delta_bytes):>13s} total"
                    f"  {d.path}"
                )
            lines.append("")

        if shrinking:
            lines.append(
                f"  Top {min(top_n, len(shrinking))} shrinking directories (by direct file size)"
            )
            for d, direct in shrinking[:top_n]:
                lines.append(
                    f"    {format_bytes(direct):>13s}"
                    f"  {format_bytes(d.delta_bytes):>13s} total"
                    f"  {d.path}"
                )
            lines.append("")

    return "\n".join(lines)


def _compute_direct_deltas(
    root: NcduDir, deltas: dict[str, PathDelta]
) -> list[tuple[PathDelta, int]]:
    """Compute the direct (self) delta for each directory.

    For each directory, subtract child directory delta contributions
    to isolate the change from its own direct files.
    """
    result: list[tuple[PathDelta, int]] = []
    stack: list[NcduDir] = [root]
    while stack:
        node = stack.pop()
        delta = deltas.get(path_str(node))
        if delta is not None:
            child_dir_delta = sum(
                deltas[path_str(c)].delta_bytes
                for c in node.children
                if isinstance(c, NcduDir) and path_str(c) in deltas
            )
            direct = delta.delta_bytes - child_dir_delta
            if direct != 0:
                result.append((delta, direct))
        for c in node.children:
            if isinstance(c, NcduDir):
                stack.append(c)
    return result


def _collect_dirs_by_direct_bytes(root: NcduDir) -> list[tuple[NcduDir, int]]:
    result: list[tuple[NcduDir, int]] = []
    stack: list[NcduDir] = [root]
    while stack:
        node = stack.pop()
        child_dir_bytes = sum(c.total_bytes for c in node.children if isinstance(c, NcduDir))
        direct = node.total_bytes - child_dir_bytes
        result.append((node, direct))
        for c in node.children:
            if isinstance(c, NcduDir):
                stack.append(c)
    return result


def drilldown_to_d3(node: DrilldownNode) -> dict[str, Any]:
    result: dict[str, Any] = {"name": node.path.name or str(node.path)}
    if node.previous_bytes is not None:
        result["previousBytes"] = node.previous_bytes
    if node.uncompressed.total_size > 0 and node.total_bytes > 0:
        result["compressibleRatio"] = node.uncompressed.total_size / node.total_bytes
    if node.children:
        children = [drilldown_to_d3(c) for c in node.children]
        # D3's .sum() only totals leaf values. If the directory's own disk
        # usage (dsize) isn't fully covered by its children, emit the
        # remainder as an invisible leaf so the D3 total matches total_bytes.
        child_sum = sum(c.total_bytes for c in node.children)
        remainder = node.total_bytes - child_sum
        if remainder > 0:
            children.append({"name": "", "value": remainder})
        result["children"] = children
    else:
        result["value"] = node.total_bytes
    return result


def render_html_report(
    current_run: NcduRun,
    previous_run: NcduRun | None,
    drilldown: DrilldownNode,
    metrics: GlobalMetrics,
    text_report: str,
) -> str:
    usage_data = json.dumps(drilldown_to_d3(drilldown))

    uncompressed_bytes = format_bytes(metrics.total_uncompressed.total_size)

    scan_lines = f"<p>Current scan: {html.escape(format_timestamp(current_run.timestamp))}</p>"
    if previous_run is not None:
        scan_lines += (
            f"\n  <p>Previous scan: {html.escape(format_timestamp(previous_run.timestamp))}</p>"
        )

    return f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>durep report — {html.escape(str(drilldown.path))}</title>
  <script>{D3_JS}</script>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2em; color: #333; }}
    .cards {{ display: flex; gap: 1.5em; flex-wrap: wrap; margin-bottom: 2em; }}
    .card {{ background: #f5f5f5; border-radius: 8px; padding: 1em 1.5em; min-width: 150px; }}
    .card .label {{ font-size: 0.85em; color: #666; }}
    .card .value {{ font-size: 1.4em; font-weight: bold; }}
    .overview-layout {{ display: flex; gap: 1.5em; align-items: flex-start; flex-wrap: wrap; }}
    .overview-plot {{ flex: 1 1 900px; min-width: 0; }}
    .overview-legend {{ flex: 0 0 240px; max-height: 400px; overflow-y: auto; padding-right: 0.25em; }}
    .overview-legend h3 {{ margin: 0 0 0.75em; font-size: 1em; }}
    .legend-item {{
      display: flex; align-items: center; gap: 0.6em; width: 100%;
      padding: 0.3em 0.4em; border: 0; background: transparent; text-align: left;
      cursor: pointer; border-radius: 6px; font: inherit; color: inherit;
    }}
    .legend-item:hover {{ background: #f0f0f0; }}
    .legend-item.is-hidden {{ color: #999; text-decoration: line-through; }}
    .legend-swatch {{ width: 14px; height: 14px; border-radius: 2px; flex: 0 0 14px; }}
    .legend-item.is-hidden .legend-swatch {{ opacity: 0.2; }}
    .legend-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    h1 {{ margin-bottom: 0.3em; }}
    h2 {{ margin-top: 2em; }}
    pre {{ background: #f5f5f5; padding: 1.5em; border-radius: 8px; overflow-x: auto;
           font-size: 0.85em; line-height: 1.5; }}
    svg {{ display: block; margin: 0 auto; }}
  </style>
</head>
<body>
  <h1>Disk usage report</h1>
  <p>From directory: {html.escape(str(drilldown.path))}</p>
  {scan_lines}

  <div class="cards">
    <div class="card">
      <div class="label">Total usage</div>
      <div class="value">{html.escape(format_bytes(metrics.total_usage_bytes))}</div>
    </div>
    <div class="card">
      <div class="label">Files</div>
      <div class="value">{metrics.total_files:,}</div>
    </div>
    <div class="card">
      <div class="label">Compressable files</div>
      <div class="value">{html.escape(uncompressed_bytes)}</div>
    </div>
  </div>

  <h2>Disk usage</h2>
  <div id="usage-sunburst"></div>
  <h2>Text report</h2>
  <pre>{html.escape(text_report)}</pre>

  <script>
{SUNBURST_JS}
  const usageData = {usage_data};
  renderSunburst('usage-sunburst', usageData, formatBytes);
  </script>
</body>
</html>
"""


def render_overview_text_report(
    series: list[ProjectTimeSeries],
    samples: list[ProjectSample],
    metadata: dict[ProjectName, ProjectMetadata] | None,
) -> str:
    lines: list[str] = []
    lines.append("Disk usage overview")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    if not series:
        lines.append("No data.")
        return "\n".join(lines)

    # Build a table: one row per project
    rows: list[tuple[str, str | None, str, int, int, int, str, int]] = []
    for ts in series:
        project_metadata = metadata.get(ProjectName(ts.project)) if metadata else None
        owner = project_metadata.legal_owner if project_metadata else None
        leads = ", ".join(project_metadata.project_leads) if project_metadata else ""

        # Find earliest and latest non-zero values
        earliest = 0
        latest = ts.bytes_values[-1] if ts.bytes_values else 0
        for v in ts.bytes_values:
            if v > 0:
                earliest = v
                break

        growth = latest - earliest
        pct = format_overview_growth_pct(earliest, latest)

        compressible = ts.uncompressed_values[-1].total_size if ts.uncompressed_values else 0

        rows.append((ts.project, owner, leads, latest, earliest, growth, pct, compressible))

    # Sort by legal owner then latest size descending (if metadata), else by latest size descending.
    if metadata:
        rows.sort(key=lambda r: (r[1] or "", -r[3]))
    else:
        rows.sort(key=lambda r: -r[3])

    if metadata:
        # Table header with metadata columns
        lines.append(
            f"  {'Project':<35s} {'Legal owner':<15s} {'Project lead':<20s}"
            f" {'Latest':>10s} {'Earliest':>10s} {'Growth':>9s} {'%':>8s}"
            f" {'Compressible':>10s}"
        )
        for project, owner, leads, latest, earliest, growth, pct, compressible in rows:
            proj_display = project if len(project) <= 35 else "..." + project[-(35 - 3) :]
            owner_display = (
                (owner or "") if len(owner or "") <= 15 else "..." + (owner or "")[-(15 - 3) :]
            )
            leads_display = leads if len(leads) <= 20 else "..." + leads[-(20 - 3) :]
            lines.append(
                f"  {proj_display:<35s} {owner_display:<15s} {leads_display:<20s}"
                f" {format_bytes(latest):>10s} {format_bytes(earliest):>10s}"
                f" {format_bytes(growth):>9s} {pct:>8s} {format_bytes(compressible):>10s}"
            )
    else:
        # Table header without metadata columns
        lines.append(
            f"  {'Project':<40s} {'Latest':>10s} {'Earliest':>10s}"
            f" {'Growth':>9s} {'%':>8s} {'Compressible':>10s}"
        )
        for project, _owner, _leads, latest, earliest, growth, pct, compressible in rows:
            proj_display = project if len(project) <= 40 else "..." + project[-(40 - 3) :]
            lines.append(
                f"  {proj_display:<40s}"
                f" {format_bytes(latest):>10s} {format_bytes(earliest):>10s}"
                f" {format_bytes(growth):>9s} {pct:>8s} {format_bytes(compressible):>10s}"
            )
    lines.append("")

    return "\n".join(lines)


def downsample_indices(series: list[ProjectTimeSeries]) -> list[int]:
    """Return indices where any series has a real measurement."""
    if not series:
        return []
    keep: set[int] = set()
    for s in series:
        for i, m in enumerate(s.measured):
            if m:
                keep.add(i)
    return sorted(keep)


def render_overview_html_report(
    series: list[ProjectTimeSeries],
    text_report: str,
    samples: list[ProjectSample],
    metadata: dict[ProjectName, ProjectMetadata] | None,
) -> str:
    if not series:
        dates: list[str] = []
        projects: list[str] = []
        values: list[list[int]] = []
        measured: list[list[bool]] = []
    else:
        indices = downsample_indices(series)
        dates = [series[0].dates[i].isoformat() for i in indices]
        projects = [s.project for s in series]
        values = [[s.bytes_values[i] for i in indices] for s in series]
        measured = [[s.measured[i] for i in indices] for s in series]

    # Per-project stats for dynamic summary cards
    latest_files: dict[str, int] = {}
    for sample in sorted(samples, key=lambda s: s.timestamp):
        latest_files[sample.project] = sample.total_files

    per_project_files = [latest_files.get(s.project, 0) for s in series]
    per_project_latest = [s.bytes_values[-1] if s.bytes_values else 0 for s in series]
    per_project_earliest: list[int] = []
    for s in series:
        earliest = 0
        for v in s.bytes_values:
            if v > 0:
                earliest = v
                break
        per_project_earliest.append(earliest)
    per_project_compressible = [
        s.uncompressed_values[-1].total_size if s.uncompressed_values else 0 for s in series
    ]

    chart_data = json.dumps(
        {
            "dates": dates,
            "projects": projects,
            "values": values,
            "measured": measured,
            "files": per_project_files,
            "latestBytes": per_project_latest,
            "earliestBytes": per_project_earliest,
            "compressible": per_project_compressible,
            "legalOwners": {
                s.project: project_metadata.legal_owner
                for s in series
                if (project_metadata := metadata.get(ProjectName(s.project))) is not None
                and project_metadata.legal_owner is not None
            }
            if metadata
            else None,
            "projectLeads": {
                s.project: project_metadata.project_leads
                for s in series
                if (project_metadata := metadata.get(ProjectName(s.project))) is not None
                and project_metadata.project_leads
            }
            if metadata
            else None,
        }
    )

    return f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>durep overview</title>
  <script>{D3_JS}</script>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2em; color: #333; }}
    .cards {{ display: flex; gap: 1.5em; flex-wrap: wrap; margin-bottom: 2em; }}
    .card {{ background: #f5f5f5; border-radius: 8px; padding: 1em 1.5em; min-width: 150px; }}
    .card .label {{ font-size: 0.85em; color: #666; }}
    .card .value {{ font-size: 1.4em; font-weight: bold; }}
    .overview-filters {{ display: flex; gap: 1em; align-items: stretch; flex-wrap: wrap; margin-bottom: 1.5em; }}
    .overview-filters:empty {{ display: none; }}
    .filter-panel {{ flex: 1 1 280px; min-width: 240px; max-height: 11rem; overflow-y: auto; padding: 0.75em; border: 1px solid #ddd; border-radius: 8px; }}
    .filter-panel h3 {{ margin: 0 0 0.5em; font-size: 1em; }}
    .overview-layout {{ display: flex; gap: 1.5em; align-items: flex-start; flex-wrap: wrap; }}
    .overview-plot {{ flex: 1 1 900px; min-width: 0; }}
    .overview-legend {{ flex: 0 0 300px; max-height: 400px; overflow-y: auto; padding-right: 0.25em; }}
    .overview-legend h3 {{ margin: 0 0 0.5em; font-size: 1em; }}
    .filter-list {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.15em 0.5em; }}
    .filter-option {{
      display: flex; align-items: center; justify-content: space-between; gap: 0.5em;
      width: 100%; padding: 0.35em 0.45em; border: 0; background: transparent;
      border-radius: 6px; cursor: pointer; font: inherit; color: inherit; text-align: left;
    }}
    .filter-option:hover {{ background: #f0f0f0; }}
    .filter-option.is-active {{ background: #e8e8e8; font-weight: bold; }}
    .filter-count {{ color: #666; font-size: 0.85em; font-weight: normal; }}
    .legend-item {{
      display: flex; align-items: center; gap: 0.6em; width: 100%;
      padding: 0.25em 0.4em; border: 0; background: transparent; text-align: left;
      border-radius: 6px; font: inherit; color: inherit;
    }}
    .legend-swatch {{ width: 14px; height: 14px; border-radius: 2px; flex: 0 0 14px; }}
    .legend-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .project-list {{ margin-top: 0.25em; }}
    h1 {{ margin-bottom: 0.3em; }}
    h2 {{ margin-top: 2em; }}
    pre {{ background: #f5f5f5; padding: 1.5em; border-radius: 8px; overflow-x: auto;
           font-size: 0.85em; line-height: 1.5; }}
    svg {{ display: block; margin: 0 auto; }}
    #overview-chart {{ position: relative; }}
  </style>
</head>
<body>
  <h1>Disk usage overview</h1>

  <div class="cards">
    <div class="card">
      <div class="label">Total size</div>
      <div class="value" id="stat-size"></div>
    </div>
    <div class="card">
      <div class="label">Total growth</div>
      <div class="value" id="stat-growth"></div>
    </div>
    <div class="card">
      <div class="label">Total files</div>
      <div class="value" id="stat-files"></div>
    </div>
    <div class="card">
      <div class="label">Compressible files</div>
      <div class="value" id="stat-compressible"></div>
    </div>
  </div>

  <h2>Size over time</h2>
  <div class="overview-filters" id="overview-filters"></div>
  <div class="overview-layout">
    <div class="overview-plot">
      <div id="overview-chart"></div>
    </div>
    <aside class="overview-legend">
      <div id="overview-legend"></div>
    </aside>
  </div>

  <h2>Text report</h2>
  <pre>{html.escape(text_report)}</pre>

  <script>
{STACKED_AREA_JS}
  const chartData = {chart_data};
  renderStackedArea('overview-chart', 'overview-legend', 'overview-filters', chartData, formatBytes);
  </script>
</body>
</html>
"""
