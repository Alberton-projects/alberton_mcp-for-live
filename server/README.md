# alberton-mcp — the MCP server

The out-of-Live half of Alberton MCP for Live. Speaks MCP (stdio) to the model
and CONTRACT Layer A (NDJSON, `127.0.0.1:17853`) to the Remote Script bridge.
All intelligence lives here: locator resolution, inventory validation, color
and dB conversion, batch compilation (any mutating tool = one undo step),
browser indexing, and the change feed.

## Run

Requires the `Alberton MCP` Remote Script selected in Live (see
`remote_script/Alberton_MCP/README.md`).

```
uv run --project server alberton-mcp
```

Environment: `ALBERTON_HOST` (default `127.0.0.1`), `ALBERTON_PORT` (default
`17853`).

## Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`
(alongside, not replacing, any existing servers):

```json
"alberton": {
  "command": "/Users/workingburcet/.local/bin/uv",
  "args": [
    "run", "--project",
    "/Users/workingburcet/Alberton-MCP for Live/server",
    "alberton-mcp"
  ]
}
```

## Tests

```
uv run --project server pytest
```

149 tests, no Ableton required — they run against an in-process fake bridge
(real TCP, real framing), plus a smoke test that boots the server over a real
stdio transport and checks every tool's schema and its behaviour when Live is
absent. This is the CI suite.

With Live open, from the repository root:

```
python3 tools/wire_probe.py         # 36 checks: the bridge against CONTRACT 1.2
python3 tools/live_verify.py        # 23 checks: the tools, end to end
python3 tools/lifecycle_probe.py    # 23 checks: dropped connections, garbage, churn
python3 tools/functional_suite.py   # 53 checks: every tool, plus a coverage report
python3 tools/malformed_probe.py    # 59 checks: calls shaped the way a model gets them wrong
python3 tools/degenerate_probe.py   # 46 checks: empty, awkward and extreme material
python3 tools/limits_probe.py       # 15 checks: the declared ceilings, provoked
python3 tools/stress_probe.py       # measurement under concurrent human use
python3 tools/scale_report.py       # read-only: what your set costs to read
```

`functional_suite.py` finishes by listing any tool it never called, so coverage is
measured rather than claimed.

`lifecycle_probe.py --manual` adds the checks that need Live restarted or the
Control Surface toggled; it prompts for each step. Every probe works on scratch
material named `ZZ …`, sweeps leftovers from a previous killed run before it
starts, and leaves the set as it found it. Run one probe at a time: the bridge
accepts a single client, so a probe displaces the MCP server (it reconnects on
its next call) and two probes displace each other.
