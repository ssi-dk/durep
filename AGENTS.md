# NCDU report
This Python application reads NCDU JSON files and produces a text summary and an interactive HTML
report with sunburst diagrams of disk usage.

This code is a work-in-progress. See @PLAN.md for the overall plan.

## Running
```bash
# Run tests
.venv/bin/python -m pytest tests/ -v

# Run the CLI
.venv/bin/python -m durep --current PATH --out-dir PATH [--previous PATH]
```

## Coding style
* Never add an AI agent as a co-author on commits
* Do not use prepend single undercores to names; the only API is the CLI, so all functions are private
* After making changes, run `ruff check .` and address warnings, then run `ruff format .`
