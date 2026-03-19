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


def render_text_report(
    current_run: NcduRun,
    metrics: GlobalMetrics,
    deltas: dict[Path, PathDelta] | None,
    top_n: int,
) -> str:
    root = current_run.root
    lines: list[str] = []

    # Header
    lines.append(f"Disk usage report: {root.path}")
    if current_run.timestamp is not None:
        lines.append(f"Scanned: {current_run.timestamp.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
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
    for node, direct in top_dirs:
        lines.append(f"  {format_bytes(direct):>12s}  {format_bytes(node.total_bytes):>12s}  {node.path}")
    lines.append("")

    # Changes
    lines.append("Changes since previous snapshot")
    if deltas is None:
        lines.append("  Previous snapshot not available.")
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

  d3.partition().size([2 * Math.PI, radius])(root);

  const arc = d3.arc()
    .startAngle(d => d.x0)
    .endAngle(d => d.x1)
    .padAngle(d => Math.min((d.x1 - d.x0) / 2, 0.005))
    .padRadius(radius / 2)
    .innerRadius(d => d.y0)
    .outerRadius(d => d.y1 - 1);

  const container = d3.select("#" + containerId);
  const svg = container.append("svg")
    .attr("viewBox", [-radius, -radius, width, width])
    .style("max-width", width + "px")
    .style("font", "10px sans-serif");

  const path = svg.append("g")
    .selectAll("path")
    .data(root.descendants().filter(d => d.depth))
    .join("path")
      .attr("fill", d => { let n = d; while (n.depth > 1) n = n.parent; return color(n.data.name); })
      .attr("fill-opacity", d => 0.9 - d.depth * 0.15)
      .attr("d", arc);

  path.append("title")
    .text(d => d.ancestors().map(a => a.data.name).reverse().join("/") + "\\n" + formatBytes(d.value));

  const label = svg.append("g")
    .attr("pointer-events", "none")
    .attr("text-anchor", "middle")
    .selectAll("text")
    .data(root.descendants().filter(d => d.depth && (d.y0 + d.y1) / 2 * (d.x1 - d.x0) > 10))
    .join("text")
      .attr("transform", function(d) {
        const x = (d.x0 + d.x1) / 2 * 180 / Math.PI;
        const y = (d.y0 + d.y1) / 2;
        return `rotate(${x - 90}) translate(${y},0) rotate(${x < 180 ? 0 : 180})`;
      })
      .attr("dy", "0.35em")
      .text(d => d.data.name);

  // Click to zoom
  let current = root;
  path.style("cursor", "pointer").on("click", function(event, p) {
    if (current === p) { p = root; }
    current = p;
    root.each(d => {
      d.target = {
        x0: Math.max(0, Math.min(1, (d.x0 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
        x1: Math.max(0, Math.min(1, (d.x1 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
        y0: Math.max(0, d.y0 - p.y0),
        y1: Math.max(0, d.y1 - p.y0)
      };
    });
    const t = svg.transition().duration(500);
    path.transition(t)
      .tween("data", d => { const i = d3.interpolate(d.current || d, d.target); return t => { d.current = i(t); }; })
      .attrTween("d", d => () => arc(d.current));
    label.transition(t).attr("fill-opacity", 0).remove();
  });
  path.each(d => { d.current = d; });
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
    drilldown: DrilldownNode,
    metrics: GlobalMetrics,
    growth_drilldown: DrilldownNode | None,
    text_report: str,
) -> str:
    usage_data = json.dumps(drilldown_to_d3(drilldown))
    growth_data = json.dumps(drilldown_to_d3(growth_drilldown)) if growth_drilldown else "null"

    u = metrics.total_uncompressed
    top_uncompressed_items = [
        ("FASTA", u.fasta),
        ("FASTQ", u.fastq),
        ("SAM", u.sam),
        ("VCF", u.vcf),
    ]
    top_uncompressed_items.sort(key=lambda x: x[1], reverse=True)
    top_fmt, top_val = top_uncompressed_items[0]
    top_uncompressed = f"{top_fmt}: {format_bytes(top_val)}" if top_val > 0 else "none"

    growth_section = ""
    if growth_drilldown is not None:
        growth_section = """
    <h2>Growth since previous snapshot</h2>
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
  <p>{html.escape(str(drilldown.path))}</p>

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
      <div class="label">Top uncompressed format</div>
      <div class="value">{html.escape(top_uncompressed)}</div>
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
