# NCDU report
This Python application reads NCDU JSON files and produces a text summary and an interactive HTML
report with D3 sunburst diagrams of disk usage. Optionally compares two scans to show growth/shrinkage.

## Architecture
- `src/durep/ncdu.py` — NCDU JSON parser, produces `NcduNode` tree and `NcduRun` (with timestamp)
- `src/durep/analytics.py` — Computation: uncompressed stats, global metrics, directory deltas, drilldown trees
- `src/durep/reports.py` — Text and HTML report rendering, D3 sunburst JS
- `src/durep/cli.py` — CLI argument parsing and pipeline orchestration

## Running
```bash
# Install (first time, or after changing dependencies)
uv pip install -e ".[test]"

# Run tests
.venv/bin/python -m pytest tests/ -v

# Run the CLI
.venv/bin/python -m durep --current PATH --out-dir PATH [--previous PATH]

# Lint and format
ruff check .
ruff format .
```

## Verification
Open the generated `overall.html` in a browser and verify:
- Sunburst renders and is interactive (click to drill down, click center to go back)
- Summary cards show correct totals
- Text report is embedded at the bottom
- If `--previous` was given: growth sunburst appears

## Coding style
* Never add an AI agent as a co-author on commits
* Do not prepend single underscores to names; the only API is the CLI, so all functions are private
* After making changes, run `ruff check .` and address warnings, then run `ruff format .`
