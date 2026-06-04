# durep - NCDU report generator
This Python application reads NCDU JSON files and produces a text summary and an interactive HTML
report of disk usage.
Two subcommands produce different HTML reports:
* `detail`: Produce detailed sunburst diagram of a single project's usage at one time, or at two times (latest disk usage plus delta from last report)
* `overview`: Show stacked area diagrams of overall usage of many projects at several time points.

## Architecture
- `src/durep/cli.py` — CLI parsing, argument validation, logging setup, and dispatch for `detail` and `overview`
- `src/durep/workflows.py` — Pipeline orchestration: load scans, schedule overview parsing, compute analytics, load metadata, and write reports
- `src/durep/ncdu.py` — Streaming NCDU JSON parser, NCDU tree/sample data models, compressed-file format stats, display-node budgeting, and lightweight overview sample parsing
- `src/durep/analytics.py` — Computation layer: global metrics, directory deltas, drilldown trees, project samples, and interpolated overview time series
- `src/durep/reports.py` — Text and self-contained HTML rendering for detail and overview reports, including serialization for D3 charts and embedded static assets
- `src/durep/metadata.py` — Optional overview metadata TSV parsing and resolution for legal owners and project leads
- `src/durep/sunburst.js` — Interactive D3 sunburst renderer used by `detail` reports
- `src/durep/stacked_area.js` — Interactive D3 stacked area chart, legend, filters, and summary-card updates used by `overview` reports
- `src/durep/d3.v7.min.js` — Vendored D3 runtime embedded into generated HTML reports
- `src/durep/__main__.py` and `src/durep/__init__.py` — Package entry point and version metadata
- `src/durep/utils.py` — Shared `Project` `NewType` helper

## Running
Run commands using uv in a local .venv
```bash
# Install (first time, or after changing dependencies)
uv pip install -e ".[dev]"

# Run tests
.venv/bin/python -m pytest tests/ -v

# Lint and format
ruff check .
ruff format .
```

## Verification
Open the generated `report.html` in a browser and verify:
- Sunburst renders and is interactive (click to drill down, click center to go back)
- Summary cards show correct totals
- Text report is embedded at the bottom
- If `--previous` was given: growth sunburst appears

## Coding style
* Never add an AI agent as git commit co-author
* Do not prepend single underscores to names; the only API is the CLI, so all functions are private
* Use type hints in every signature to be able to lean on static checks.
* Avoid default values for function arguments

## After making changes:
* Check for type errors using `.venv/bin/pyright 2>&1`. Fix errors IF you judge that the errors are not false positives,
  and that fixing them will not degrade the code.
  An example of where fixing the error may degrade the code is if a NumPy array does not play well with typing,
  and you could fix the type errors by switching to a normal list, but that would degrade performance.
* After types pass, run linter, then formatter

## Other notes:
* The only stable API is the CLI, so any source-code level changes are considered non-breaking.
