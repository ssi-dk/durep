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
