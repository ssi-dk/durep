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

## After making changes:
* Check for type errors using `.venv/bin/pyright 2>&1`. Fix errors IF you judge that the errors are not false positives,
  and that fixing them will not degrade the code.
  An example of where fixing the error may degrade the code is if a NumPy array does not play well with typing,
  and you could fix the type errors by switching to a normal list, but that would degrade performance.
* After types pass, run `ruff check .` and address warnings, then run `ruff format .`

## Other notes:
* The only stable API is the CLI, so any source-code level changes are considered non-breaking.
