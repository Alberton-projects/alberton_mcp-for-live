# Alberton MCP for Live

An [MCP](https://modelcontextprotocol.io) server plus a companion Ableton Live Remote
Script that let an LLM read and write a Live set through the Live Object Model (LOM).

> **Status: pre-alpha.** Nothing usable yet. Currently in Phase 0 (LOM introspection).
> See [docs/HANDOFF.md](docs/HANDOFF.md) for the project plan and reasoning.

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
