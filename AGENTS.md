# durep - NCDU report generator
This Python application reads NCDU JSON files and produces a text summary and an interactive HTML
report of disk usage.
Two subcommands produce different HTML reports:
* `detail`: Produce detailed sunburst diagram of a single project's usage at one time, or at two times (latest disk usage plus delta from last report)
* `overview`: Show stacked area diagrams of overall usage of many projects at several time points.

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
