# Plan: `ncdu` Project Usage Reporter (Python)

## Summary
Build a Python CLI app that reads:
1. Current `ncdu` JSON directory (many JSON files)
2. Optional previous `ncdu` JSON directory (many JSON files) for diff
3. CSV mapping of owned directory prefixes to `project`

Input model updates:
- We do not ingest a single `ncdu` JSON file.
- One project corresponds to one JSON file.
- A JSON file's top-level directory is identical to the directory mapped to that project.

The app outputs:
- One overall interactive HTML report
- One per-project interactive HTML report (including `UNASSIGNED`)
- One human-readable text report summarizing totals and deltas

## Implementation Changes
1. **Project structure and CLI**
- [COMPLETE] Create a package-based app with a single CLI entrypoint (for example `python -m ...`).
- [COMPLETE] Basic output layout scaffolding exists:
  - `overall.html`
  - `text_report.txt`
  - `projects/<project_slug>.html` (directory scaffold)
- [TODO] Update CLI arguments to directory-based inputs:
  - `--current` (required): directory containing current `ncdu` JSON files
  - `--previous` (optional): directory containing previous `ncdu` JSON files for diff
  - `--mapping` (required): CSV ownership table
  - `--out-dir` (required): output directory
  - `--top-n` (default `15`)
  - `--max-depth` (default `4`)

2. **Data ingestion and normalization**
- [COMPLETE] Parse `ncdu` JSON tree into normalized nodes with:
  - absolute path, type (file/dir), apparent size, disk size, file count
- [COMPLETE] Use `dsize` as primary usage metric when present; fallback to `asize`.
- [COMPLETE] Compute recursive aggregates for directories:
  - total bytes, total files, total directories
- [COMPLETE] Parse mapping CSV with exact schema:
  - `directory,project`
- [COMPLETE] Assume ownership is unambiguous; unmatched paths map to synthetic project `UNASSIGNED`.
- [TODO] Add logic to determine/select the correct JSON files from each JSON directory.

3. **Core analytics**
- [TODO] Global metrics:
  - total usage, total files, total dirs, project breakdown
- [TODO] Top-N drilldown dataset:
  - for any selected directory node, include top N children by size
  - stop expanding deeper than `max_depth`
- [TODO] Diff analytics (if `--previous` provided):
  - path-level delta: `current_size - previous_size`
  - project-level rolled-up deltas from path deltas
  - top growth and shrinkage paths/projects

4. **Report generation**
- [TODO] HTML reports (Plotly Sunburst):
  - Overall: whole filesystem/project context + summary cards + diff section
  - Per-project: only nodes assigned to that project + project-specific summary + diff
  - Include client-side drill behavior using precomputed hierarchical data bounded by `top_n` and `max_depth`.
- [TODO] Text report (human-readable):
  - global totals
  - per-project totals
  - top changed paths/projects (if previous snapshot exists)
  - explicit `UNASSIGNED` section

## Public Interfaces / Contracts
- **Mapping CSV contract**
  - Header required: `directory,project`
  - `directory` must be absolute path prefix
  - Multiple rows per project are allowed
- **NCDU input contract**
  - `--current` points to a directory with many `ncdu` JSON files
  - `--previous` (optional) points to a directory with many `ncdu` JSON files
  - One project corresponds to one JSON file
  - JSON top-level directory equals the mapped `directory` for that project
  - [TODO] Selection logic for the correct JSON files is to be implemented
- **CLI contract**
  - Non-zero exit on malformed JSON/CSV, missing required files/directories, or invalid input shape
  - Successful run always writes `overall.html` and `text_report.txt`
  - Writes per-project HTML for every discovered project bucket (including `UNASSIGNED` if non-empty)

## Test Plan
- Unit tests:
  - [COMPLETE] `ncdu` parser on representative nested structures
  - [COMPLETE] prefix-matching correctness (including longest-prefix behavior)
  - [COMPLETE] aggregate calculations (bytes/files/dirs)
  - [TODO] JSON-directory selection logic
  - [TODO] diff logic for added/removed/changed paths
- Integration tests:
  - [TODO] fixture current+previous JSON directories + mapping CSV produces expected outputs
  - [TODO] per-project report includes only in-scope paths
  - [TODO] `UNASSIGNED` handling appears correctly in HTML/text
- Acceptance checks:
  - [TODO] open `overall.html` and confirm interactive drilldown, summary metrics, and diff section
  - [TODO] open one project report and confirm scoped totals match text report project totals

## Assumptions and Defaults
- Project directory ownership is exclusive and unambiguous in source data.
- Primary metric is disk usage (`dsize`), with `asize` fallback.
- Default visualization is Plotly Sunburst.
- Default text output is human-readable summary (not machine-oriented TSV/JSON).
- If `--previous` is omitted, diff sections are present but marked "not available".
