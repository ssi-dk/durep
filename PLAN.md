# Plan: Add `durep overview` subcommand

## Context
Managers need a high-level report comparing many projects over time. The current CLI produces detailed per-project reports with sunburst diagrams. The new `overview` subcommand accepts many NCDU JSON files (potentially with different root dirs), extracts only top-level totals, and renders a stacked area chart showing project sizes over time plus a text summary.

## CLI restructure (`src/durep/cli.py`)

Convert the flat CLI to use argparse subcommands:

- **`durep detail`** — the existing behavior (1–2 scans, same root required, sunburst report). Move current args (`scan`, `--top-n`, `--max-depth`) under this subcommand.
- **`durep overview`** — new subcommand:
  - Positional: `scan` (nargs="+") — one or more NCDU JSON paths
  - `--out-dir` (required)
  - `--log-level` (shared)
  - No `--top-n` or `--max-depth` (not needed)

Both subcommands share `--out-dir` and `--log-level`. The `CliArgs` dataclass splits into `DetailArgs` and `OverviewArgs` (or a union), each with their own `from_namespace` and `validate`.

Add `execute_overview()` function that:
1. Parses all input JSON files via `parse_ncdu_json_file()`
2. Extracts a project sample from each (project name = full root path, date = timestamp binned to day, total_bytes, total_files, uncompressed stats)
3. Calls new analytics to build the time series
4. Renders text + HTML reports
5. Writes to `--out-dir`

## New analytics (`src/durep/analytics.py`)

Add data structures and functions:

```python
@dataclass
class ProjectSample:
    project: str          # root path as string
    date: datetime.date   # timestamp truncated to day
    total_bytes: int
    total_files: int
    total_directories: int
    uncompressed: UncompressedStats

@dataclass
class ProjectTimeSeries:
    project: str
    dates: list[datetime.date]    # aligned to global date range
    bytes_values: list[int]       # one per date
    uncompressed_values: list[UncompressedStats]  # one per date
```

New functions:

- **`extract_project_sample(run: NcduRun) -> ProjectSample`** — reads root node totals + timestamp + uncompressed stats. Raises if timestamp is missing. Calls `compute_all_uncompressed_stats()` to get the root's `UncompressedStats`.
- **`build_overview_series(samples: list[ProjectSample]) -> list[ProjectTimeSeries]`**:
  1. Collect all unique dates across all projects
  2. Build a complete date range (min to max, daily)
  3. For each project:
     - Map its sample dates to total_bytes and uncompressed stats
     - If a project has data for days 1 and 3 but not 2: forward-fill day 1's values into day 2
     - If a project has no data before its first appearance: fill with 0 (no backward interpolation)
  4. If two samples for the same project fall on the same day, keep the latest one
  5. Return sorted list of `ProjectTimeSeries`

## Text report (`src/durep/reports.py`)

New function **`render_overview_text_report(series: list[ProjectTimeSeries], samples: list[ProjectSample]) -> str`**:

- Header with generation timestamp
- Table: one row per project, columns for latest size, earliest size, growth (absolute + %), uncompressed data total
- Sorted by latest size descending

## HTML report (`src/durep/reports.py`)

New function **`render_overview_html_report(series: list[ProjectTimeSeries], text_report: str) -> str`**:

- Summary cards: total projects, total size (latest date), total growth
- D3.js stacked area chart:
  - X-axis: dates
  - Y-axis: bytes (formatted)
  - One layer per project, stacked
  - **Stack order**: projects with the smallest absolute total growth at the bottom — this stabilizes the chart so upper layers shift less as lower layers change
  - Hover tooltip showing project name, date, size
  - Color palette with enough distinct colors
- Text report embedded in `<pre>` at bottom (same pattern as existing report)

## Tests

Add tests for:
- `extract_project_sample` — basic extraction, missing timestamp error, uncompressed stats included
- `build_overview_series` — day binning, forward-fill, no backward-fill, same-day dedup
- CLI integration: `durep overview` with multiple files produces output
- CLI validation: subcommand required, at least 1 file

Update existing CLI tests to use `durep detail` subcommand.

## Files to modify
- `src/durep/cli.py` — restructure to subcommands, add `execute_overview()`
- `src/durep/analytics.py` — add `ProjectSnapshot`, `ProjectTimeSeries`, `build_overview_series()`
- `src/durep/reports.py` — add `render_overview_text_report()`, `render_overview_html_report()` with stacked area D3 JS
- `tests/test_cli.py` — update existing tests for `detail` subcommand, add `overview` tests
- `tests/test_analytics.py` — add overview analytics tests

## Existing code to reuse
- `parse_ncdu_json_file()` from `ncdu.py` — parses each input file
- `format_bytes()` from `reports.py` — byte formatting in text and HTML
- `format_timestamp()` from `reports.py` — timestamp display
- HTML template pattern from `render_html_report()` — same structure (cards + chart + pre)
- `SUNBURST_JS` `formatBytes` JS function — reuse in overview HTML

## Verification
1. `ruff check . && ruff format .`
2. `pytest tests/ -v` — all existing + new tests pass
3. Generate an overview report with 3+ NCDU JSON files spanning multiple days
4. Open HTML in browser: stacked area chart renders, hover works, projects distinguishable
5. Verify forward-fill: project missing a middle day shows flat line through that gap
6. Verify no backward-fill: project appearing on day 3 shows 0 before day 3
