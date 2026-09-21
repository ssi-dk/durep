# durep - disk usage report generator
durep generates human-readable HTML and text reports of disk usage from [ncdu](https://en.wikipedia.org/wiki/Ncdu) JSON reports.
It is useful for informing users of compute clusters about their disk usage.

For SSI-specific deployment and usage on our internal uGerm server, see out (private) repo rit-deploy-durep.

## Installation
Install with `pip` or `uv` from the pyproject.toml: `uv venv && uv pip install .`.
To install with dev dependencies: `uv venv && uv pip install '.[dev]'`.

## Usage
durep has two subcommands:
* `detail`: Takes an ncdu JSON file and creates a detailed report of disk usage with an interactive sunburst diagram.
  Optionally takes a second JSON file which must be of the same top-level directory at a different time, in which case it provides information about disk usage changes between the two.
* `overview`: Takes an input directory and reads all files with names ending in `.json` (case-sensitive), non-recursively, to create a report with total disk usage per top-level directory over time.
  Also takes a TSV file with columns `project` and `legal_owner` for filtering projects in the HTML overview.

### Metadata TSV format
The file passed to `overview --metadata-tsv-path` must be a UTF-8, tab-delimited text file.
Its header must contain these exact column names (in any order): `project` and `legal_owner`. Additional columns are allowed and ignored.

```text
project	legal_owner
proj_a	Alice
proj_b	Bob
```

The `project` value must match the basename of the NCDU scan's root directory: for example,
`/data/proj_a` is matched by `proj_a`.
A blank `legal_owner` means no legal owner.

Empty or whitespace-only rows are ignored. Any other row with a blank or missing `project` value is an error.
Each project must appear only once in the TSV; duplicate project names are an error.

#### Examples
Generate a detailed report from one JSON file
```bash
durep detail --out-dir output report.json
```

Generate a detailed report from a pair of JSON files of the same directory at different times
```bash
durep detail --out-dir output report_20260112.json report_20260329.json
```

Generate an overview of a large collection of NCDU reports
```bash
durep overview ncdu_reports --out-dir output --metadata-tsv-path metadata.tsv
```

### Try the included example data
The [`examples/`](examples/) directory contains six small NCDU scans and matching metadata that can
be used immediately after cloning the repository. It exercises both reports, including detail-report
changes and overview-report project filters.
