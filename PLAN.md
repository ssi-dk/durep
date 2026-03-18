# Plan: Implement analytics and report generation

## Context
The CLI scaffold and NCDU parser are in place. The mapping CSV concept has been retired — it was
a relic of an earlier design. The app now takes one or two NCDU JSON files (current + optional
previous) and produces a text summary and an interactive HTML report with Plotly Sunburst charts.
This plan implements the full pipeline: analytics, text report, and HTML report.

## Changes

### 1. `pyproject.toml`
- No new runtime dependencies. D3.js is embedded inline from its minified source.

### 2. `src/durep/cli.py` — Remove mapping, wire up real pipeline
- Delete `--mapping` from `CliArgs`, `build_parser()`, and `validate()`.
- Delete `require_file` call for mapping.
- Delete `render_overall_html()` and `render_text_report()` skeleton functions.
- Delete creation of `projects/` subdirectory from `execute()`.
- In `execute()`: parse both JSON files, run analytics, call report renderers, write output.

### 3. `src/durep/analytics.py` — Implement computation logic

Add these functions (data structures already defined):

```
compute_uncompressed_stats(root: NcduNode) -> UncompressedStats
```
Walk the entire tree; for each file node check `path.suffix.lstrip(".")` against `EXTENSIONS`.
Accumulate `disk_size` into the matching format counter.

```
compute_global_metrics(root: NcduNode) -> GlobalMetrics
```
Use `root.total_bytes`, `root.total_files`, and `compute_uncompressed_stats(root)`.

```
compute_directory_deltas(current: NcduNode, previous: NcduNode) -> dict[Path, PathDelta]
```
Walk both trees in parallel (match by path). For every directory node present in both snapshots,
create a `PathDelta(path, current_bytes=current.total_bytes, previous_bytes=previous.total_bytes)`.
Return the full dict — callers use `.delta_bytes` lazily (positive = growth, negative = shrinkage).
This also means `current_bytes` and `previous_bytes` are always available for display (e.g., the
sunburst can show both actual storage and the change).

```
build_drilldown_tree(root: NcduNode, top_n: int, max_depth: int) -> DrilldownNode
```
Recursively prune: at each level, keep only the top `top_n` children by `total_bytes`. Stop
recursing at `max_depth`. Compute `uncompressed` stats per node as part of this traversal.
If there are more than `top_n` children, aggregate the remainder into a synthetic "Other (N items)"
leaf node with `total_bytes` = sum of remaining children's bytes. This node has no children
(no drilldown), so the sunburst chart doesn't explode with thousands of entries.

```
build_growth_drilldown(current: NcduNode, deltas: dict[Path, int], top_n: int, max_depth: int) -> DrilldownNode | None
```
Build a DrilldownNode tree where `total_bytes` = delta bytes. Only include nodes with positive
delta. Returns `None` if nothing grew. Prune with `top_n` / `max_depth` same as above.

### 4. `src/durep/reports.py` — New file for rendering

```
render_text_report(root: NcduNode, metrics: GlobalMetrics,
                   deltas: dict[Path, int] | None, top_n: int) -> str
```
Sections:
- Header: root path, generated date, previous snapshot path or "not available"
- Summary: total disk usage (human-readable), total files, total directories
- Uncompressed data breakdown (fasta/fastq/sam/vcf), skip if all zero
- Top N largest directories (from walking root, sort by total_bytes)
- Changes section: if deltas is None, print "not available". Otherwise: net change, top N
  growing paths, top N shrinking paths.

```
render_html_report(drilldown: DrilldownNode, metrics: GlobalMetrics,
                   growth_drilldown: DrilldownNode | None,
                   text_report: str, previous_path: Path | None) -> str
```
Self-contained HTML file (~200 KB total, suitable for email):
- D3.js v7 hierarchy + shape modules inlined (no CDN, no external dependency, ~100 KB)
- Summary cards: total usage, total files, total dirs, top uncompressed format
- D3 Sunburst of disk usage (from `drilldown`), click-to-drill behavior
- If `growth_drilldown` is not None: second D3 Sunburst titled "Growth since previous snapshot"
- Text report embedded in a `<pre>` block at the bottom
- Chart data embedded as JSON in a `<script>` tag

Sunburst data: convert DrilldownNode tree to a nested JSON object (D3 hierarchy format).
D3's `d3.hierarchy()` accepts `{ name, value, children: [...] }` directly.
The "Other (N items)" synthetic nodes are leaf nodes with no `children` key.

D3 sunburst implementation (~150 lines of JS) embedded as a template string in `reports.py`:
- `d3.hierarchy(data).sum(d => d.value)` to compute subtree sizes
- `d3.partition()` for the layout
- `<path>` arcs via `d3.arc()`
- Click handler to zoom into a segment (standard zoomable sunburst pattern)

Helper:
```
format_bytes(n: int) -> str
```
Human-readable bytes (B, KB, MB, GB, TB).

### 5. `tests/test_analytics.py` — Already exists; add tests for:
- `compute_uncompressed_stats`: tree with known fasta/fastq/sam/vcf files
- `compute_global_metrics`: check totals match root node
- `compute_directory_deltas`: current > previous (growth), current < previous (shrinkage)
- `build_drilldown_tree`: verify top_n pruning and max_depth cutoff

### 6. `tests/test_cli.py` — Update:
- Remove all references to `--mapping` and mapping file fixtures
- Update `test_run_creates_step1_outputs()` to not pass `--mapping`
- Verify `overall.html` and `text_report.txt` are written (contract unchanged)

## Critical files
- `src/durep/cli.py` — remove mapping, wire execute()
- `src/durep/analytics.py` — add all computation functions
- `src/durep/reports.py` — new file, all rendering
- `src/durep/ncdu.py` — read-only; NcduNode used throughout
- `tests/test_analytics.py` — implement tests
- `tests/test_cli.py` — update to remove mapping
- `pyproject.toml` — no new runtime deps; D3 is inlined

## Reused existing code
- `NcduNode`, `parse_ncdu_json_file` from `src/durep/ncdu.py`
- `UncompressedStats`, `GlobalMetrics`, `DrilldownNode`, `PathDelta`, `EXTENSIONS` from `src/durep/analytics.py`
- `CliArgs.parse_cli_args`, `positive_int`, `build_parser` from `src/durep/cli.py`

## Notes on mapping CSV removal
- Delete `--mapping` from CLI entirely
- Delete `require_file` call for mapping in `validate()`
- `utils.py` (`Project` NewType) stays — still used by `ugerm.py` for future file-discovery work
- Update `PLAN.md` to reflect that mapping CSV is removed

## Verification
```bash
# Install
uv pip install -e ".[test]"

# Run tests
uvx pytest tests/ -v

# Smoke test with a real ncdu file
durep --current /path/to/ncdu.json --out-dir /tmp/durep-out

# With diff
durep --current /path/to/current.json --previous /path/to/previous.json --out-dir /tmp/durep-out

# Lint and format
ruff check .
ruff format .
```

Open `/tmp/durep-out/overall.html` in a browser and verify:
- Sunburst renders and is interactive
- Summary cards show correct totals
- Text report is embedded at the bottom
- If diff was run: growth sunburst appears
