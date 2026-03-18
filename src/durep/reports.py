from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from durep.analytics import GlobalMetrics, PathDelta
from durep.ncdu import NcduNode


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
    root: NcduNode,
    metrics: GlobalMetrics,
    deltas: dict[Path, PathDelta] | None,
    top_n: int,
) -> str:
    lines: list[str] = []

    # Header
    lines.append(f"Disk usage report: {root.path}")
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
    if u.fasta or u.fastq or u.sam or u.vcf:
        lines.append("Uncompressed data")
        if u.fasta:
            lines.append(f"  FASTA: {format_bytes(u.fasta)}")
        if u.fastq:
            lines.append(f"  FASTQ: {format_bytes(u.fastq)}")
        if u.sam:
            lines.append(f"  SAM:   {format_bytes(u.sam)}")
        if u.vcf:
            lines.append(f"  VCF:   {format_bytes(u.vcf)}")
        lines.append("")

    # Top N largest directories
    dirs = _collect_dirs_by_size(root)
    dirs.sort(key=lambda d: d.total_bytes, reverse=True)
    top_dirs = dirs[:top_n]
    lines.append(f"Top {len(top_dirs)} largest directories")
    for d in top_dirs:
        lines.append(f"  {format_bytes(d.total_bytes):>12s}  {d.path}")
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


def _collect_dirs_by_size(root: NcduNode) -> list[NcduNode]:
    result: list[NcduNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.node_type == "dir":
            result.append(node)
            stack.extend(node.children)
    return result
