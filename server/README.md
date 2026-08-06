# alberton-mcp — the MCP server

The out-of-Live half of Alberton MCP for Live. Speaks MCP (stdio) to the model
and CONTRACT Layer A (NDJSON, `127.0.0.1:17853`) to the Remote Script bridge.
All intelligence lives here: locator resolution, inventory validation, color
and dB conversion, batch compilation (any mutating tool = one undo step),
browser indexing, and the change feed.

## Install and verify

You need [`uv`](https://docs.astral.sh/uv/) — it is not part of macOS, and it is
what fetches a suitable Python (3.10+) and the dependencies:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, from the repository root, check that the package builds and the server works:

```
uv run --project server pytest
```

149 tests, no Ableton required — including one that boots this server over a real
stdio transport and talks to it as an MCP client would.

**You do not start this server yourself.** Your MCP client spawns it and speaks
JSON-RPC to it over stdin/stdout, which is why the command below is in the client's
configuration rather than in your hands:

```
uv run --project server alberton-mcp
```

Run that in a terminal and it will sit there silently, waiting for a client that
never comes — and the first newline you type produces a JSON parse error. That is
the protocol working as designed, not a broken install.

It needs the `Alberton MCP` Remote Script selected in Live to do anything useful
(see `remote_script/Alberton_MCP/README.md`).

Environment: `ALBERTON_HOST` (default `127.0.0.1`), `ALBERTON_PORT` (default
`17853`).

## Claude Desktop

Two values are yours, not mine — get them by running, from the repository root:

```
echo "\"command\": \"$(command -v uv)\"," && echo "\"project\": \"$PWD/server\""
```

Then add this to `~/Library/Application Support/Claude/claude_desktop_config.json`
inside its `mcpServers` object, alongside any servers already there, substituting
those two values:

```json
"alberton": {
  "command": "/absolute/path/to/uv",
  "args": [
    "run", "--project",
    "/absolute/path/to/the/repository/server",
    "alberton-mcp"
  ]
}
```

Both paths must be absolute — the client does not run in a shell, so `~`, `$HOME`
and relative paths do not expand. Restart Claude Desktop afterwards; it starts the
server once, at launch, so it will not see a newly edited config or newly edited
server code until it does.

Any other MCP client works the same way: it needs the `uv` binary, the `--project`
directory, and the `alberton-mcp` entry point.

## Tests

From the repository root:

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
