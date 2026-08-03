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

The suite runs against an in-process fake bridge (real TCP, real framing).
End-to-end verification against a live Ableton instance:

```
python3 tools/live_verify.py        # from the repository root, Live open
```
