from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from durep.analytics import DrilldownNode, GlobalMetrics, PathDelta
from durep.ncdu import NcduNode, NcduRun


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
        net = sum(d.delta_bytes for d in deltas.values())
        lines.append(f"  Net change: {format_bytes(net)}")
        lines.append("")

        sorted_deltas = sorted(deltas.values(), key=lambda d: d.delta_bytes, reverse=True)
        growing = [d for d in sorted_deltas if d.delta_bytes > 0]
        shrinking = [d for d in reversed(sorted_deltas) if d.delta_bytes < 0]

        if growing:
            lines.append(f"  Top {min(top_n, len(growing))} growing paths")
            for d in growing[:top_n]:
                lines.append(f"    {'+' + format_bytes(d.delta_bytes):>13s}  {d.path}")
            lines.append("")

        if shrinking:
            lines.append(f"  Top {min(top_n, len(shrinking))} shrinking paths")
            for d in shrinking[:top_n]:
                lines.append(f"    {format_bytes(d.delta_bytes):>13s}  {d.path}")
            lines.append("")

    return "\n".join(lines)


def _collect_dirs_by_direct_bytes(root: NcduNode) -> list[tuple[NcduNode, int]]:
    result: list[tuple[NcduNode, int]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.node_type == "dir":
            child_dir_bytes = sum(c.total_bytes for c in node.children if c.node_type == "dir")
            direct = node.total_bytes - child_dir_bytes
            result.append((node, direct))
            stack.extend(node.children)
    return result


def drilldown_to_d3(node: DrilldownNode) -> dict[str, Any]:
    result: dict[str, Any] = {"name": node.path.name or str(node.path)}
    if node.children:
        result["children"] = [drilldown_to_d3(c) for c in node.children]
    else:
        result["value"] = node.total_bytes
    return result


SUNBURST_JS = """\
function renderSunburst(containerId, data, formatBytes) {
  const width = 700;
  const radius = width / 2;

  const color = d3.scaleOrdinal(d3.quantize(d3.interpolateRainbow,
    (data.children ? data.children.length : 0) + 1));

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
      .attr("fill", d => { let n = d; while (n.depth > 1) n = n.parent; return color(n.data.name); })
      .attr("fill-opacity", d => { const r = d.depth - focus.depth; if (r === 0 && focus !== root) return 0.3; return r > 0 && r <= maxVisibleRings ? 0.9 - r * 0.15 : 0; })
      .attr("d", arc)
      .style("cursor", "pointer");

  paths.append("title")
    .text(d => d.ancestors().map(a => a.data.name).reverse().join("/") + "\\n" + formatBytes(d.value));

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

  // Click to zoom
  paths.on("click", function(event, p) {
    if (focus === p) { p = root; }
    // Don't zoom into leaf nodes (no children to show); zoom to parent instead
    if (p !== root && (!p.children || p.children.length === 0)) { p = p.parent || root; }
    focus = p;

    breadcrumb.text(fullPath(p) + " (" + formatBytes(p.value) + ")");

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
      .attr("fill-opacity", d => { const r = d.depth - p.depth; return r > 0 && r <= maxVisibleRings ? 0.9 - r * 0.15 : 0; });

    // Fade labels out during transition, then rebuild after
    labelGroup.selectAll("text").transition(t).attr("fill-opacity", 0);
    t.end().then(() => updateLabels());
  });
}

function formatBytes(n) {
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let u = -1;
  do { n /= 1024; u++; } while (n >= 1024 && u < units.length - 1);
  return n.toFixed(1) + " " + units[u];
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
