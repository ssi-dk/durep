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
from durep.ncdu import NcduDir, NcduRun


def format_bytes(n: int) -> str:
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n < 1024:
        return f"{sign}{n} B"
    m = float(n)
    del n
    for si_prefix in "KMGTPE":
        m /= 1024
        if m < 1024:
            return f"{sign}{m:.4g} {si_prefix}B"

    assert False


def format_timestamp(ts: datetime | None) -> str:
    if ts is None:
        return "not available"
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def render_text_report(
    current_run: NcduRun,
    previous_run: NcduRun | None,
    metrics: GlobalMetrics,
    deltas: dict[Path, PathDelta] | None,
    top_n: int,
) -> str:
    root = current_run.root
    lines: list[str] = []

    # Header
    lines.append(f"Disk usage report: {root.path}")
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
        lines.append("")

    # Top N directories by direct file size (excludes subdirectory contributions)
    ranked = _collect_dirs_by_direct_bytes(root)
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    top_dirs = ranked[:top_n]
    lines.append(f"Top {len(top_dirs)} directories by direct file size")
    lines.append(f"  {'Self':>12s}  {'Total':>12s}  Path")
    for node, direct in top_dirs:
        lines.append(
            f"  {format_bytes(direct):>12s}  {format_bytes(node.total_bytes):>12s}  {node.path}"
        )
    lines.append("")

    # Changes
    lines.append("Changes since previous scan")
    if deltas is None:
        lines.append("  Previous scan not available.")
    else:
        direct_deltas = _compute_direct_deltas(root, deltas)
        net = sum(d.delta_bytes for d in deltas.values() if d.path == root.path)
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
    root: NcduDir, deltas: dict[Path, PathDelta]
) -> list[tuple[PathDelta, int]]:
    """Compute the direct (self) delta for each directory.

    For each directory, subtract child directory delta contributions
    to isolate the change from its own direct files.
    """
    result: list[tuple[PathDelta, int]] = []
    stack: list[NcduDir] = [root]
    while stack:
        node = stack.pop()
        delta = deltas.get(node.path)
        if delta is not None:
            child_dir_delta = sum(
                deltas[c.path].delta_bytes
                for c in node.children
                if isinstance(c, NcduDir) and c.path in deltas
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
        if node.uncompressed.total_size > 0 and node.total_bytes > 0:
            result["compressibleRatio"] = node.uncompressed.total_size / node.total_bytes
    return result


SUNBURST_JS = """\
function renderSunburst(containerId, data, formatBytes) {
  const width = 700;
  const radius = width / 2;

  const RED = "#d9534f";
  const BLUE = "#5bc0de";
  const DARK_RED = "#ab3a3a";
  const COMPRESSIBLE_THRESHOLD = 0.2;

  function isCompressible(d) {
    return d.data.compressibleRatio != null && d.data.compressibleRatio >= COMPRESSIBLE_THRESHOLD;
  }

  function deltaColor(d) {
    if (isCompressible(d)) return DARK_RED;
    if (d.data.previousBytes != null) {
      return d.value > d.data.previousBytes ? RED : BLUE;
    }
    // No previous data (new, or no --previous given) -> red
    return RED;
  }

  function deltaOpacity(d) {
    if (isCompressible(d)) return 1;
    if (d.data.previousBytes == null) return 0.5;
    const cur = d.value;
    const prev = d.data.previousBytes;
    if (cur === prev) return 0.25;
    if (cur > prev) {
      // Growth: ratio of old/new → 0 for huge growth, 1 for tiny growth
      return 0.25 + 0.75 * (1 - prev / cur);
    }
    // Shrinkage: ratio of new/old → 0 for huge shrinkage, 1 for tiny shrinkage
    return 0.25 + 0.75 * (1 - cur / prev);
  }

  const root = d3.hierarchy(data)
    .sum(d => d.value || 0)
    .sort((a, b) => b.value - a.value);

  // Only render maxVisibleRings rings; deeper data exists for drill-down.
  const maxVisibleRings = 4;
  d3.partition().size([2 * Math.PI, radius])(root);
  const defaultBand = radius / (maxVisibleRings + 1);
  const centerRadius = defaultBand * 0.67;
  const outerBand = (radius - centerRadius) / maxVisibleRings;
  root.each(d => {
    if (d.depth === 0) {
      d.y0 = 0;
      d.y1 = centerRadius;
    } else if (d.depth <= maxVisibleRings) {
      d.y0 = centerRadius + (d.depth - 1) * outerBand;
      d.y1 = centerRadius + d.depth * outerBand;
    } else {
      d.y0 = 0;
      d.y1 = 0;
    }
    d.current = d;
  });

  const arc = d3.arc()
    .startAngle(d => d.x0)
    .endAngle(d => d.x1)
    .padAngle(d => Math.min((d.x1 - d.x0) / 2, 0.005))
    .padRadius(radius / 2)
    .innerRadius(d => d.y0)
    .outerRadius(d => d.y1 - 1);

  const currentArc = d3.arc()
    .startAngle(d => d.current.x0)
    .endAngle(d => d.current.x1)
    .padAngle(d => Math.min((d.current.x1 - d.current.x0) / 2, 0.005))
    .padRadius(radius / 2)
    .innerRadius(d => d.current.y0)
    .outerRadius(d => d.current.y1 - 1);

  const container = d3.select("#" + containerId);

  // Breadcrumb showing current drilldown path
  const breadcrumb = container.append("p")
    .style("text-align", "center")
    .style("font-size", "0.9em")
    .style("color", "#666")
    .style("margin", "0.5em 0");
  breadcrumb.text(root.data.name + " (" + formatBytes(root.value) + ")");

  const deltaLine = container.append("p")
    .style("text-align", "center")
    .style("font-size", "0.85em")
    .style("color", "#888")
    .style("margin", "0 0 0.5em 0");
  deltaLine.text(formatDelta(root));

  const resetBtn = container.append("div")
    .style("text-align", "center")
    .style("margin", "0 0 0.5em 0")
    .append("button")
    .text("Reset to top level")
    .style("display", "none")
    .style("cursor", "pointer");

  const svg = container.append("svg")
    .attr("viewBox", [-radius, -radius, width, width])
    .style("max-width", width + "px")
    .style("font", "10px sans-serif");

  let focus = root;

  const pathGroup = svg.append("g");
  const labelGroup = svg.append("g")
    .attr("pointer-events", "none");

  const paths = pathGroup.selectAll("path")
    .data(root.descendants().filter(d => d.depth))
    .join("path")
      .attr("fill", deltaColor)
      .attr("fill-opacity", d => { const r = d.depth - focus.depth; if (r < 1 || r > maxVisibleRings) return 0; return deltaOpacity(d); })
      .attr("d", arc)
      .style("cursor", "pointer");

  paths.append("title")
    .text(d => { let t = d.ancestors().map(a => a.data.name).reverse().join("/") + "\\n" + formatBytes(d.value); const dt = formatDelta(d); if (dt) t += "\\n" + dt; return t; });

  function labelTransform(d) {
    const angle = (d.current.x0 + d.current.x1) / 2;
    const angleDeg = angle * 180 / Math.PI;
    const r = d.current.y0 + 4;
    const flip = angleDeg > 180;
    return "rotate(" + (angleDeg - 90) + ") translate(" + r + ",0) rotate(" + (flip ? 180 : 0) + ")";
  }

  function labelAnchor(d) {
    const angle = (d.current.x0 + d.current.x1) / 2;
    return angle * 180 / Math.PI > 180 ? "end" : "start";
  }

  function labelVisible(d) {
    const relDepth = d.depth - focus.depth;
    return relDepth > 0 && relDepth <= maxVisibleRings
      && d.current.y1 > 0 && d.current.y0 > 0
      && (d.current.y0 + d.current.y1) / 2 * (d.current.x1 - d.current.x0) > 10;
  }

  // Approximate character width at 10px sans-serif
  const charWidth = 6;

  function truncateLabel(d) {
    // Limit to ring thickness, but also to the space before the diagram edge
    const ringThickness = d.current.y1 - d.current.y0 - 8;
    const spaceToEdge = radius - d.current.y0 - 4;
    const maxChars = Math.floor(Math.min(ringThickness, spaceToEdge) / charWidth);
    if (maxChars < 1) return "";
    const name = d.data.name;
    if (name.length <= maxChars) return name;
    if (maxChars <= 3) return name.slice(0, maxChars);
    return name.slice(0, maxChars - 1) + "\\u2026";
  }

  function updateLabels() {
    const labels = labelGroup.selectAll("text")
      .data(root.descendants().filter(d => d.depth), d => d.data.name + d.depth);
    labels.exit().remove();
    const entered = labels.enter().append("text")
      .attr("dy", "0.35em");
    const merged = entered.merge(labels);
    merged
      .attr("transform", labelTransform)
      .attr("text-anchor", labelAnchor)
      .attr("fill-opacity", d => labelVisible(d) ? 1 : 0)
      .text(truncateLabel);
  }

  updateLabels();

  function fullPath(d) {
    return d.ancestors().map(a => a.data.name).reverse().join("/");
  }

  function zoomTo(p) {
    focus = p;

    breadcrumb.text(fullPath(p) + " (" + formatBytes(p.value) + ")");
    deltaLine.text(formatDelta(p));
    resetBtn.style("display", p === root ? "none" : "inline-block");

    // Remap y positions: show at most maxVisibleRings relative to the
    // clicked node, with the center circle shrunk to 2/3.
    const zoomCenter = centerRadius;
    const zoomOuter = outerBand;

    root.each(d => {
      const relDepth = d.depth - p.depth;
      let ty0, ty1;
      if (relDepth <= 0) {
        ty0 = 0;
        ty1 = relDepth === 0 ? zoomCenter : 0;
      } else if (relDepth <= maxVisibleRings) {
        ty0 = zoomCenter + (relDepth - 1) * zoomOuter;
        ty1 = zoomCenter + relDepth * zoomOuter;
      } else {
        ty0 = 0;
        ty1 = 0;
      }
      // Nodes outside the clicked subtree get collapsed to zero
      const inSubtree = d.x0 >= p.x0 && d.x1 <= p.x1;
      d.target = {
        x0: Math.max(0, Math.min(1, (d.x0 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
        x1: Math.max(0, Math.min(1, (d.x1 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
        y0: inSubtree || relDepth <= 0 ? ty0 : 0,
        y1: inSubtree || relDepth <= 0 ? ty1 : 0
      };
    });

    const t = svg.transition().duration(500);

    paths.transition(t)
      .tween("data", d => {
        const i = d3.interpolate(d.current, d.target);
        return t => { d.current = i(t); };
      })
      .attrTween("d", d => () => currentArc(d))
      .attr("fill-opacity", d => { const r = d.depth - p.depth; if (r < 1 || r > maxVisibleRings) return 0; return deltaOpacity(d); });

    // Fade labels out during transition, then rebuild after
    labelGroup.selectAll("text").transition(t).attr("fill-opacity", 0);
    t.end().then(() => updateLabels());
  }

  // Click to zoom into a node; clicking the focused node goes up one level
  paths.on("click", function(event, p) {
    if (focus === p) { p = p.parent || root; }
    // Don't zoom into leaf nodes (no children to show); zoom to parent instead
    else if (!p.children || p.children.length === 0) { p = p.parent || root; }
    zoomTo(p);
  });

  resetBtn.on("click", function() { zoomTo(root); });
}

function formatBytes(n) {
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let u = -1;
  do { n /= 1024; u++; } while (n >= 1024 && u < units.length - 1);
  return n.toFixed(1) + " " + units[u];
}

function formatDelta(d) {
  if (d.data.previousBytes == null) return "";
  const current = d.value;
  const previous = d.data.previousBytes;
  const delta = current - previous;
  if (delta === 0) return "No change since previous scan";
  const absDelta = Math.abs(delta);
  if (delta > 0) {
    const pct = ((absDelta / current) * 100).toFixed(0);
    return "Grown by " + formatBytes(absDelta) + " (" + pct + "% of current size)";
  } else {
    const pct = ((absDelta / previous) * 100).toFixed(0);
    return "Shrank by " + formatBytes(absDelta) + " (" + pct + "% of original size)";
  }
}
"""


def render_html_report(
    current_run: NcduRun,
    previous_run: NcduRun | None,
    drilldown: DrilldownNode,
    metrics: GlobalMetrics,
    growth_drilldown: DrilldownNode | None,
    text_report: str,
) -> str:
    usage_data = json.dumps(drilldown_to_d3(drilldown))
    growth_data = json.dumps(drilldown_to_d3(growth_drilldown)) if growth_drilldown else "null"

    uncompressed_bytes = format_bytes(metrics.total_uncompressed.total_size)

    scan_lines = f"<p>Current scan: {html.escape(format_timestamp(current_run.timestamp))}</p>"
    if previous_run is not None:
        scan_lines += (
            f"\n  <p>Previous scan: {html.escape(format_timestamp(previous_run.timestamp))}</p>"
        )

    growth_section = ""
    if growth_drilldown is not None:
        growth_section = """
    <h2>Growth since previous scan</h2>
    <div id="growth-sunburst"></div>
"""

    growth_init = ""
    if growth_drilldown is not None:
        growth_init = "renderSunburst('growth-sunburst', growthData, formatBytes);"

    return f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>durep report — {html.escape(str(drilldown.path))}</title>
  <script src="https://d3js.org/d3.v7.min.js"></script>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2em; color: #333; }}
    .cards {{ display: flex; gap: 1.5em; flex-wrap: wrap; margin-bottom: 2em; }}
    .card {{ background: #f5f5f5; border-radius: 8px; padding: 1em 1.5em; min-width: 150px; }}
    .card .label {{ font-size: 0.85em; color: #666; }}
    .card .value {{ font-size: 1.4em; font-weight: bold; }}
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
{growth_section}
  <h2>Text report</h2>
  <pre>{html.escape(text_report)}</pre>

  <script>
{SUNBURST_JS}
  const usageData = {usage_data};
  const growthData = {growth_data};
  renderSunburst('usage-sunburst', usageData, formatBytes);
  {growth_init}
  </script>
</body>
</html>
"""


def render_overview_text_report(
    series: list[ProjectTimeSeries],
    samples: list[ProjectSample],
) -> str:
    lines: list[str] = []
    lines.append("Disk usage overview")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    if not series:
        lines.append("No data.")
        return "\n".join(lines)

    # Build a table: one row per project, latest size, earliest size, growth, compressible
    rows: list[tuple[str, int, int, int, str, int]] = []
    for ts in series:
        # Find earliest and latest non-zero values
        earliest = 0
        latest = ts.bytes_values[-1] if ts.bytes_values else 0
        for v in ts.bytes_values:
            if v > 0:
                earliest = v
                break

        growth = latest - earliest
        if earliest > 0:
            pct = f"{(growth / earliest) * 100:+.1f}%"
        elif latest > 0:
            pct = "new"
        else:
            pct = "0.0%"

        compressible = ts.uncompressed_values[-1].total_size if ts.uncompressed_values else 0

        rows.append((ts.project, latest, earliest, growth, pct, compressible))

    # Sort by latest size descending
    rows.sort(key=lambda r: r[1], reverse=True)

    # Table header
    lines.append(
        f"  {'Project':<40s}  {'Latest':>12s}  {'Earliest':>12s}"
        f"  {'Growth':>12s}  {'%':>8s}  {'Compressible':>14s}"
    )
    for project, latest, earliest, growth, pct, compressible in rows:
        proj_display = project if len(project) <= 40 else "..." + project[-(40 - 3) :]
        lines.append(
            f"  {proj_display:<40s}  {format_bytes(latest):>12s}  {format_bytes(earliest):>12s}"
            f"  {format_bytes(growth):>12s}  {pct:>8s}  {format_bytes(compressible):>14s}"
        )
    lines.append("")

    return "\n".join(lines)


STACKED_AREA_JS = """\
function renderStackedArea(containerId, seriesData, formatBytes) {
  const container = d3.select("#" + containerId);
  const margin = {top: 20, right: 200, bottom: 40, left: 80};
  const width = 900 - margin.left - margin.right;
  const height = 400 - margin.top - margin.bottom;

  const svg = container.append("svg")
    .attr("viewBox", [0, 0, 900, 400])
    .style("max-width", "900px")
    .append("g")
    .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

  // Parse dates
  const parseDate = d3.timeParse("%Y-%m-%d");
  const dates = seriesData.dates.map(d => parseDate(d));
  const projects = seriesData.projects;
  const values = seriesData.values;
  const measured = seriesData.measured;

  // Sort projects by absolute growth (smallest first = bottom of stack)
  const projectGrowth = projects.map((p, j) => {
    const vals = values[j];
    const first = vals.find(v => v > 0) || 0;
    const last = vals[vals.length - 1];
    return {project: p, absGrowth: Math.abs(last - first)};
  });
  projectGrowth.sort((a, b) => a.absGrowth - b.absGrowth);
  const sortedProjects = projectGrowth.map(pg => pg.project);

  const color = d3.scaleOrdinal()
    .domain(sortedProjects)
    .range(d3.schemeTableau10.concat(d3.schemePaired));

  const x = d3.scaleTime()
    .domain(d3.extent(dates))
    .range([0, width]);

  const y = d3.scaleLinear().range([height, 0]);

  const area = d3.area()
    .x((d, i) => x(dates[i]))
    .y0(d => y(d[0]))
    .y1(d => y(d[1]));

  // Groups for layered drawing order
  const areaGroup = svg.append("g");
  const dotGroup = svg.append("g");

  // Axes
  const spanDays = (dates[dates.length - 1] - dates[0]) / (1000 * 60 * 60 * 24);
  let tickInterval, tickFmt;
  if (spanDays <= 30) {
    tickInterval = d3.timeWeek.every(1);
    tickFmt = d3.timeFormat("%b %d");
  } else if (spanDays <= 180) {
    tickInterval = d3.timeWeek.every(2);
    tickFmt = d3.timeFormat("%b %d");
  } else if (spanDays <= 365) {
    tickInterval = d3.timeMonth.every(1);
    tickFmt = d3.timeFormat("%b %Y");
  } else {
    tickInterval = d3.timeMonth.every(3);
    tickFmt = d3.timeFormat("%b %Y");
  }

  svg.append("g")
    .attr("transform", "translate(0," + height + ")")
    .call(d3.axisBottom(x).ticks(tickInterval).tickFormat(tickFmt));

  const yAxis = svg.append("g");

  // Hidden project tracking and redraw
  const hidden = new Set();

  function redraw() {
    const visibleKeys = sortedProjects.filter(p => !hidden.has(p));

    const tableData = dates.map((d, i) => {
      const row = {date: d};
      projects.forEach((p, j) => { row[p] = hidden.has(p) ? 0 : values[j][i]; });
      return row;
    });

    const stack = d3.stack()
      .keys(visibleKeys)
      .order(d3.stackOrderNone)
      .offset(d3.stackOffsetNone);

    const stackedData = stack(tableData);

    const yMax = d3.max(stackedData, layer => d3.max(layer, d => d[1])) || 0;
    y.domain([0, yMax]).nice();
    yAxis.transition().duration(300).call(d3.axisLeft(y).ticks(6).tickFormat(d => formatBytes(d)));

    // Areas
    const paths = areaGroup.selectAll("path").data(stackedData, d => d.key);
    paths.exit().remove();
    paths.enter().append("path")
      .attr("fill", d => color(d.key))
      .attr("fill-opacity", 0.8)
      .merge(paths)
      .transition().duration(300)
      .attr("d", area);

    // Dots
    dotGroup.selectAll("*").remove();
    stackedData.forEach(layer => {
      const projKey = layer.key;
      const pi = projects.indexOf(projKey);
      const projMeasured = measured[pi];
      const dots = [];
      layer.forEach((d, i) => {
        if (projMeasured[i] && d[1] > d[0]) dots.push({i: i, d: d});
      });
      dotGroup.selectAll(null)
        .data(dots)
        .join("circle")
        .attr("cx", pt => x(dates[pt.i]))
        .attr("cy", pt => y(pt.d[1]))
        .attr("r", 3)
        .attr("fill", color(projKey))
        .attr("stroke", "#fff")
        .attr("stroke-width", 0.5);
    });
  }

  redraw();

  // Legend (clickable)
  const legend = svg.append("g")
    .attr("transform", "translate(" + (width + 10) + ",0)");

  sortedProjects.slice().reverse().forEach((p, i) => {
    const g = legend.append("g")
      .attr("transform", "translate(0," + (i * 20) + ")")
      .style("cursor", "pointer");
    const swatch = g.append("rect").attr("width", 14).attr("height", 14).attr("fill", color(p));
    const label = p.length > 20 ? "..." + p.slice(-(20 - 3)) : p;
    const text = g.append("text").attr("x", 18).attr("y", 11).style("font-size", "11px").text(label);
    g.on("click", function() {
      if (hidden.has(p)) {
        hidden.delete(p);
        swatch.attr("fill-opacity", 1);
        text.style("text-decoration", null).style("fill", null);
      } else {
        hidden.add(p);
        swatch.attr("fill-opacity", 0.2);
        text.style("text-decoration", "line-through").style("fill", "#999");
      }
      redraw();
    });
  });

  // Tooltip
  const tooltip = container.append("div")
    .style("position", "absolute")
    .style("background", "rgba(255,255,255,0.95)")
    .style("border", "1px solid #ccc")
    .style("border-radius", "4px")
    .style("padding", "8px")
    .style("font-size", "12px")
    .style("pointer-events", "none")
    .style("display", "none");

  const bisect = d3.bisector(d => d).left;

  svg.append("rect")
    .attr("width", width)
    .attr("height", height)
    .attr("fill", "transparent")
    .on("mousemove", function(event) {
      const [mx] = d3.pointer(event);
      const dateAtMouse = x.invert(mx);
      const idx = Math.min(bisect(dates, dateAtMouse), dates.length - 1);
      const d = dates[idx];
      let html = "<strong>" + d3.timeFormat("%Y-%m-%d")(d) + "</strong><br>";
      sortedProjects.slice().reverse().forEach((p, j) => {
        if (hidden.has(p)) return;
        const pi = sortedProjects.indexOf(p);
        const val = values[pi][idx];
        if (val > 0) {
          html += "<span style='color:" + color(p) + "'>\u25a0</span> " + p + ": " + formatBytes(val) + "<br>";
        }
      });
      tooltip.style("display", "block").html(html);
      const rect = container.node().getBoundingClientRect();
      tooltip.style("left", (event.clientX - rect.left + 15) + "px")
             .style("top", (event.clientY - rect.top - 10) + "px");
    })
    .on("mouseleave", function() {
      tooltip.style("display", "none");
    });
}
"""


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

    # Summary card values
    total_projects = len(series)
    total_latest = sum(s.bytes_values[-1] for s in series if s.bytes_values)
    total_earliest = 0
    for s in series:
        for v in s.bytes_values:
            if v > 0:
                total_earliest += v
                break
    total_growth = total_latest - total_earliest
    total_compressible = sum(
        s.uncompressed_values[-1].total_size for s in series if s.uncompressed_values
    )

    chart_data = json.dumps(
        {
            "dates": dates,
            "projects": projects,
            "values": values,
            "measured": measured,
        }
    )

    return f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>durep overview</title>
  <script src="https://d3js.org/d3.v7.min.js"></script>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2em; color: #333; }}
    .cards {{ display: flex; gap: 1.5em; flex-wrap: wrap; margin-bottom: 2em; }}
    .card {{ background: #f5f5f5; border-radius: 8px; padding: 1em 1.5em; min-width: 150px; }}
    .card .label {{ font-size: 0.85em; color: #666; }}
    .card .value {{ font-size: 1.4em; font-weight: bold; }}
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
      <div class="label">Projects</div>
      <div class="value">{total_projects}</div>
    </div>
    <div class="card">
      <div class="label">Total size (latest)</div>
      <div class="value">{html.escape(format_bytes(total_latest))}</div>
    </div>
    <div class="card">
      <div class="label">Total growth</div>
      <div class="value">{html.escape(format_bytes(total_growth))}</div>
    </div>
    <div class="card">
      <div class="label">Compressible files</div>
      <div class="value">{html.escape(format_bytes(total_compressible))}</div>
    </div>
  </div>

  <h2>Size over time</h2>
  <div id="overview-chart"></div>

  <h2>Text report</h2>
  <pre>{html.escape(text_report)}</pre>

  <script>
{STACKED_AREA_JS}
function formatBytes(n) {{
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let u = -1;
  do {{ n /= 1024; u++; }} while (n >= 1024 && u < units.length - 1);
  return n.toFixed(1) + " " + units[u];
}}
  const chartData = {chart_data};
  renderStackedArea('overview-chart', chartData, formatBytes);
  </script>
</body>
</html>
"""
