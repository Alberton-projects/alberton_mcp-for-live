# Alberton MCP for Live

An [MCP](https://modelcontextprotocol.io) server plus a companion Ableton Live Remote
Script that let an LLM read and write a Live set through the Live Object Model (LOM).

> **Status: working, not yet packaged for third parties** (2026-08-03). 46 tools covering
> Session and Arrangement writing, clip automation, audio import, device and macro
> control, and change subscriptions. Plan and findings:
> [docs/SESSION-LOG.md](docs/SESSION-LOG.md).

## What it has been tested on

Everything below was verified by running against a real Ableton instance, not inferred.
The LOM is undocumented and version-dependent, so this list is the honest extent of what
is known to work — not a guess at what probably does.

| | |
|---|---|
| **Ableton Live** | 12.4.3 **Suite** |
| **Operating system** | macOS 15 (Darwin 24.6), Apple Silicon |
| **Python inside Live** | 3.11.6 (Live's own embedded interpreter) |
| **Python for the server** | 3.10–3.13 |

**Not tested anywhere:** Windows; Live 11 or earlier; Live 12.0–12.3; Live Intro and
Standard. It may well work on some of those — the design deliberately avoids anything
Suite-specific, and the server hardcodes no platform paths — but nobody has run it, so
nothing is claimed.

If you try one of them, two things are worth knowing. Remote Scripts live somewhere else
on Windows (`%USERPROFILE%\Documents\Ableton\User Library\Remote Scripts\`), and the LOM
inventory this project designs against was generated on 12.4.3 — regenerate it with
`tools/introspect/` on your version before trusting anything unusual. Reports of either
outcome are welcome.

## Quick start

1. Install the Remote Script and select it in Live —
   [remote_script/Alberton_MCP/README.md](remote_script/Alberton_MCP/README.md).
2. Point your MCP client at the server —
   [server/README.md](server/README.md) has the Claude Desktop snippet.
3. Sanity checks, with Live open: `python3 tools/wire_probe.py` (bridge) and
   `python3 tools/live_verify.py` (server, end to end).

## Testing

142 unit tests run without Ableton, against an in-process fake of the bridge that speaks
the real wire protocol over real TCP — that is the CI suite. Eight further probes run
against a live instance and are what actually found the bugs: contract compliance,
end-to-end tool behaviour, connection lifecycle and robustness, every tool in the
catalogue with a coverage report, calls shaped the way a model gets them wrong,
degenerate material, the declared limits, and measurement under concurrent human use —
plus a read-only report of what a set costs to read. See
[server/README.md](server/README.md).

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
