# Alberton MCP for Live

Talk to your Ableton Live set. An [MCP](https://modelcontextprotocol.io) server plus a
companion Remote Script let an AI assistant read and write a Live set for you — create
tracks and clips, write and edit MIDI, drive devices, draw automation, build
arrangements with prompts.

> **Status** (2026-08-06): working. An AI that had never seen this project installed
> it from this URL on a clean machine and was making music in the open set four
> minutes later. Tested deeply on one setup so far — see *What it has been tested on*.

## Install — for musicians

You do not need to know the terminal. Your AI assistant does the installing; you do
only three things.

You need:

- a computer with **Ableton Live 12** installed — everything has been verified on
  macOS; nothing in the design is Mac-specific, so Windows should work, but nobody
  has tried yet ([tell us](https://github.com/Alberton-projects/alberton_mcp-for-live/issues)
  if you are the first),
- an **AI assistant on that computer that can run commands and connect to MCP
  servers** — Claude Desktop or Claude Code, ChatGPT Desktop with Codex, or similar,
- about **ten minutes**, with Live open.

Open your assistant and paste this:

> I want to install and use this:
> https://github.com/Alberton-projects/alberton_mcp-for-live — guide me step by step
> from scratch on this computer, including connecting yourself to the server over
> MCP. Explain each step in plain words before doing it. When everything is
> connected, create a 4-beat MIDI clip with a C major arpeggio in the Ableton set I
> have open, so we both know it works.

That is the whole procedure. The assistant reads this repository and does the rest.
Only three things are yours:

1. **One click inside Live**, when asked: Preferences → Link, Tempo & MIDI → choose
   **Alberton MCP** in a free Control Surface slot (Input and Output: **None**).
2. **Approve** what your assistant proposes to run, if it asks.
3. **Press Cmd-S** (Ctrl-S on Windows) when you like what you hear. Nothing is ever
   saved for you — your set is always yours to keep or discard.

When it works, ask for music in your own words: the assistant sees the same manual
you can read at [docs/MANUAL.md](docs/MANUAL.md) — what this can do, what it can
reach, and what Live allows nobody to do. (Also in Catalan:
[docs/MANUAL.ca.md](docs/MANUAL.ca.md).)

If any step confuses you or fails:
[open an issue](https://github.com/Alberton-projects/alberton_mcp-for-live/issues)
and say where you got stuck.

## Install by hand — for terminal people

Four steps, in this order, because each one proves the ground the next stands on.
Run everything from the repository root — the folder `git clone` gave you.

1. **Install the Remote Script and select it in Live** —
   [remote_script/Alberton_MCP/README.md](remote_script/Alberton_MCP/README.md).
2. **Check the bridge**: `python3 tools/wire_probe.py`. This needs nothing
   installed — it speaks the socket directly, so the Python that ships with macOS
   runs it. 36 checks; if it cannot connect it tells you what to look at. Do not go
   on until this passes.
3. **Install the server's dependencies and test them**:
   `uv run --directory server pytest` (149 tests, no Ableton needed). You need `uv`
   first — it is not part of macOS; [server/README.md](server/README.md) has the
   one-line install.
4. **Point your MCP client at the server** —
   [server/README.md](server/README.md) has the Claude Desktop entry and a command
   that prints the two paths you must fill in.

`python3 tools/live_verify.py` is the end-to-end check across both halves: 23 checks
against a real Live, and it too needs nothing installed.

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

## Testing

149 unit tests run without Ableton, against an in-process fake of the bridge that speaks
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
