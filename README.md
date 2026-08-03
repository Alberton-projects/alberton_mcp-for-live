# Alberton MCP for Live

An [MCP](https://modelcontextprotocol.io) server plus a companion Ableton Live Remote
Script that let an LLM read and write a Live set through the Live Object Model (LOM).

> **Status: working v0.1** (2026-08-03). Inventory, contract 1.0, bridge and server are
> done and verified: the bridge passes a 34-check wire probe and the server passes 25
> unit tests plus a 14-check end-to-end run against a live Ableton instance. Not yet
> packaged for third parties. Plan and findings: [docs/HANDOFF.md](docs/HANDOFF.md).

## Quick start

1. Install the Remote Script and select it in Live —
   [remote_script/Alberton_MCP/README.md](remote_script/Alberton_MCP/README.md).
2. Point your MCP client at the server —
   [server/README.md](server/README.md) has the Claude Desktop snippet.
3. Sanity checks, with Live open: `python3 tools/wire_probe.py` (bridge) and
   `python3 tools/live_verify.py` (server, end to end).

## Architecture

Two components with a hard boundary between them:

- **Remote Script** (runs inside Live). A thin, generic bridge over LOM object paths:
  read a property, write a property, call a method, read/write notes, run an atomic
  batch, subscribe to changes. It exposes no musical vocabulary of its own, so it almost
  never changes — which matters, because Live only loads Remote Scripts at startup.
- **MCP server** (runs outside Live). All the intelligence: the tool catalogue, path
  resolution, validation, structured errors, caching. New capabilities are server-side
  changes; no Live restart required.

Design rules that will not move:

- Time is always absolute beats as floats — never bars, note-value names, or seconds.
- Batches are atomic: one batch, one undo step.
- The socket binds to `127.0.0.1` only.
- No telemetry.
- Structured errors, never prose parsed as errors.

## Prior art

[`ahujasid/ableton-mcp`](https://github.com/ahujasid/ableton-mcp) (MIT, Siddharth Ahuja,
2025) proved this category of tool works and informed this design. Alberton MCP for Live
is written from scratch with a different architecture — a generic LOM bridge instead of a
fixed command vocabulary in the Remote Script — and shares no code with it.

## License

[MIT](LICENSE).

Alberton MCP for Live is not affiliated with, endorsed by, or sponsored by Ableton AG.
"Ableton" and "Ableton Live" are trademarks of Ableton AG.
