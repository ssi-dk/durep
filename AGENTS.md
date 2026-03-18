# NCDU report
This Python application reads NCDU JSON files as well as a table mapping directories to projects, and produces per-project HTML reports with interactive sunburst diagrams of the disk usage.

This code is a work-in-progress. See @PLAN.md for the overall plan. 

## Coding style
* Never add an AI agent as a co-author on commits
* Do not use prepend single undercores to names; the only API is the CLI, so all functions are private
* After making changes, run `ruff check .` and address warnings, then run `ruff format .`
