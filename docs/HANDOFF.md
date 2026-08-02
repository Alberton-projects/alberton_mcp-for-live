# Handoff — Ableton Live MCP, from scratch

Written 2026-08-01, at the end of a session where we diagnosed the existing AbletonMCP,
documented it, and decided to build a replacement. Read once at the start of the project;
consult afterwards. Durable rules live in `CLAUDE.md`; this file holds the reasoning, the
findings, and what is still open.

Everything marked **[verified]** was checked against the user's machine or against source
code during that session. Everything marked **[unverified]** comes from the assistant's
prior knowledge of Live's Python API and must be confirmed by introspection before any
design depends on it.

---

## 1. Where we are

Phase 0 (LOM introspection) was completed 2026-08-02: `tools/introspect/` dumps the LOM
from inside Live (12.4.3, embedded Python 3.11.6) to `docs/lom-raw.json`, and
`tools/render_inventory.py` renders `docs/lom-inventory.md` from it. Phase 1 produced
`docs/CONTRACT.md`, frozen as 1.0 on 2026-08-02 after user review. Phase 2 was
completed 2026-08-03: the bridge (`remote_script/Alberton/`, v0.1.1) passes all 34
checks of the contract probe (`tools/wire_probe.py`) against a live instance —
including atomic batch rollback and subscription change events. The next action is
Phase 3 (the MCP server). The user works on macOS with Ableton Live 12.4.3 Suite and has
the existing `ableton-mcp` 1.2.0 installed and working, which is useful as a reference
implementation and as a fallback while this project is incomplete.

---

## 2. The prior art, and why we are not forking it

`github.com/ahujasid/ableton-mcp`, MIT licence, Siddharth Ahuja, 2025. **[verified]**
Package `ableton-mcp` 1.2.0 on PyPI; Remote Script at commit `5e9ffbd` (4 June 2026).

It works and it was the right thing for its author to build. Our reasons for starting over
are architectural, not qualitative:

**Its Remote Script hardcodes a vocabulary.** A chain of `if command_type == "…"` with 24
branches. **[verified]** Every new capability is a new branch inside Live, and Live only
loads Remote Scripts at startup — so every capability costs the user a restart. Our design
inverts this: a generic bridge over LOM object paths, so capabilities are added
server-side and the script stops changing.

**Its framing is broken.** The script accumulates received bytes into a buffer and calls
`json.loads` on the entire buffer, with no delimiter and no length prefix. **[verified]**
Two messages arriving back to back concatenate, never parse, and poison the connection.
It is invisible today only because the server sends one command at a time and waits. Any
batching or concurrency exposes it immediately.

**It listens on `0.0.0.0` with no authentication.** **[verified]** Anyone on the same
network can drive the user's DAW.

**It ships telemetry enabled by default.** **[verified]** Anonymous UUID persisted to
`~/Library/Application Support/AbletonMCP/customer_uuid.txt`, events sent to a Supabase
project: tool name, success, duration, platform. With an explicit consent environment
variable it also sends prompt text (up to 1000 chars), generated MIDI data, browser URIs
and file paths. Disable with `DISABLE_TELEMETRY=true`.

**It contains dead code.** `get_browser_categories` and `get_browser_items` are routed in
the dispatcher but have no implementation; a `load_instrument_or_effect` branch is
unreachable. **[verified]**

After removing all of the above, what would remain of a fork is roughly fifty lines of
socket-and-thread skeleton that we want to rewrite anyway. Hence: from scratch, with
attribution in the README as prior art that informed the design.

---

## 3. What the existing tool exposes, and what the LOM appears to offer beyond it

The current 21 tools cover: session and track inspection; creating MIDI tracks; renaming
tracks and clips; creating MIDI clips; importing audio clips; writing notes; firing and
stopping clips; global transport; tempo; browser tree and path listing; loading devices
and drum kits; and four Arrangement operations (switch view, move playhead, list
arrangement clips, duplicate a Session clip into the Arrangement). **[verified]**

A full reference lives in the packaged skill `ableton-mcp-guia` and in the PDF
*AbletonMCP — Referència completa* produced in the same session.

Capabilities believed to exist in the LOM but not exposed by the current tool
— all [unverified] when written; **Phase 0 (2026-08-02) confirmed every item on this
list**, with exact signatures in `docs/lom-inventory.md`:

- `ClipSlot.delete_clip`, `Song.delete_track`, `Song.duplicate_track`,
  `Song.create_audio_track`, `Song.create_return_track`
- `Song.begin_undo_step()` / `end_undo_step()` / `undo()` / `redo()`
- Mixer parameters as writable `DeviceParameter` objects: volume, panning, sends
- `Device.parameters` — writable device controls
- Scenes: enumerate, create, rename, fire
- Clip properties: loop points, warp mode, gain, pitch, `quantize()`
- `Clip.get_notes_extended` / `add_new_notes` (Live 11+): per-note identity, probability,
  velocity deviation
- Clip automation envelopes
- Property listeners (`add_*_listener`) — push notification of changes instead of polling
- Live 12 scale awareness (`Song.scale_name`, `root_note`)

Noteworthy details from the verification: `Clip.add_new_notes((object)) -> IntU64Vector`
returns the new notes' ids; `MidiNote` carries `note_id`, `probability`,
`velocity_deviation`, `release_velocity`; `Song.undo()`/`redo()` return a `str`;
`Scene.fire` takes `force_legato` and `can_select_scene_on_launch` keywords;
`Song.scale_name` and `root_note` are read-write and both have listeners.

The listener capability is the most architecturally significant: it changes the
interaction model from "the model asks" to "Live tells", which is what would let the
system react to what a user plays rather than only to what they type.

---

## 4. Decisions taken

**Architecture.** Thin generic Remote Script; all intelligence in the MCP server. Rationale
in `CLAUDE.md`.

**Tool design targets intent, not API surface.** Fewer, higher-level tools that succeed or
fail atomically, rather than one tool per LOM method. Example: writing a bar of music
should be one call that creates, names, colours and fills a clip in a single transaction —
not three calls each with its own partial-failure mode.

**Time model.** Absolute float beats everywhere. Nested tuplets are rational numbers;
double-precision floats represent them exactly for any musical purpose, and Live stores
positions as floats, so nothing is lost. The only cost is that off-grid notes are awkward
to edit by hand in Live's UI — a legibility cost, not an audio one.

**Three-tier strategy for irregular subdivisions** (decided with the user, who works with
nested tuplets):
1. When a whole passage shares one subdivision, change Live's grid / clip signature so
   notes land on the grid and stay hand-editable.
2. When irregularity is local or tuplet levels cross, write exact float positions and
   accept that the passage is read-only inside Live.
3. Always emit a reference track carrying pulses, accents and segment boundaries — this is
   what makes structure Live cannot draw visible to a human.

**Domain separation.** The user's Nuzic system stays entirely out of this repository. It
will consume the server as a client. A consequence worth noting: the MCP tool schema
becomes the de facto interchange format, so no separate intermediate representation needs
designing up front.

**Equal temperament.** The user has decided to work in TET 12 only. Microtonality
(Nuzic supports up to TET 53) is explicitly out of scope. This removes the hardest mapping
problem — Live and MIDI are integer 12-TET — and should not be reintroduced casually.

**Publication.** Build the tool the user needs first; decide about publishing later. The
design is general-purpose regardless, so publishing costs little extra when the time comes.
Do not let hypothetical community preferences distort design choices.

---

## 5. Open questions

- Does the LOM allow setting Live 12's tuning/scale programmatically? Irrelevant under the
  TET 12 decision, but worth recording in the inventory. — Answered in Phase 0: scale yes
  (`Song.scale_name`, `root_note`, `scale_mode` are RW with listeners); tuning partially
  (`Song.tuning_system` is read-only, though the active `TuningSystem`'s fields are RW).
- Exact shape of the generic operation set. Candidate: `get`, `set`, `call`, `get_notes`,
  `set_notes`, `batch`, `subscribe`, `unsubscribe` over dotted paths such as
  `song.tracks.3.clip_slots.2.clip`. Needs a written contract before implementation.
  — Resolved 2026-08-02: eleven ops specified in `docs/CONTRACT.md` §A.6.
- How far to go with listeners in v1. They are the highest-value capability and also the
  most complex (lifecycle, unsubscription, backpressure). Possibly defer to v2.
  — Decided by the user 2026-08-02: v1, with coalesce-on-tick backpressure and
  overflow/gone events (CONTRACT §A.6) and pull-based surfacing over MCP (§B.6).
- Project name. Should not imply endorsement by Ableton. — Resolved 2026-08-02:
  "Alberton MCP for Live", with a not-affiliated disclaimer in the README.
- Licence for this project (MIT is the obvious default given the ecosystem). — Resolved
  2026-08-02: MIT.
- Whether the server should ship a browser index cache and how it is invalidated.
  — Narrowed 2026-08-02: v1 rebuilds on demand / on server start (CONTRACT, out-of-scope
  list); smarter invalidation stays open.

---

## 6. Plan, in order

Working mode, decided with the user 2026-08-02: phases 0–2 run interactively with the
user present, because they hold the human gates (Live restarts, Preferences, design
decisions). Phase 3 may run as long autonomous goal-driven sessions once the contract is
frozen and the Remote Script is stable.

**Phase 0 — LOM introspection.** Run code inside Live that walks the `Live` module and the
live object graph, and emit `docs/lom-inventory.md`: classes, properties, which are
writable, methods and signatures, listener names, as they exist in Live 12.4.3 on this
machine. Everything downstream is designed against this file rather than against recalled
knowledge. Half a day; it is the foundation.

**Phase 1 — The contract.** A written document specifying the wire protocol (framing,
message shape, error shape, batching, undo grouping, subscription lifecycle) and the
catalogue of MCP tools with their signatures. No code. This is where the "absolute float
beats" rule gets encoded once, and where the fork-versus-scratch question answers itself
definitively.

**Phase 2 — The Remote Script.** Thin, generic, correct framing, batch with undo grouping,
127.0.0.1. Written once, changed rarely.

**Phase 3 — The MCP server.** Tool implementations, path resolution, browser index,
structured errors, read-back verification, context-efficient responses.

**Phase 4 (optional, later) — Nuzic client.** A separate repository. Compiles Nuzic
compositions to calls against this server. Testable without Ableton.

---

## 7. Practical notes for whoever picks this up

Editing the Remote Script requires restarting Ableton Live. Batch script changes; iterate
on the server.

Two valid Remote Script locations exist on macOS (`~/Music/Ableton/User Library/Remote
Scripts/` and `~/Library/Preferences/Ableton/Live <version>/User Remote Scripts/`). The
README of the prior art documents only the second. Use one; two copies of the same script
produce duplicate Control Surface entries and a port conflict. **[verified — this bit us]**

Live only scans Remote Scripts at startup, so installing a script while Live is open does
nothing until restart. A missing Control Surface selection in Preferences → Link, Tempo &
MIDI is the second most common cause of "it does not connect".

`Song.tempo` quantizes to float32 (write 123.45, read back 123.44999694824219), so
read-back comparisons need a tolerance there. Note times, in contrast, round-trip in
float64 bit-exactly — verified with 1/3 triplet floats. **[verified 2026-08-03]**

`Song.undo()` called in the same main-thread slice as `end_undo_step()` does not yet
see that step in the undo history; one tick later it does. Batch rollback must
therefore be deferred to the next tick — the bridge does this and only then sends the
batch response. **[verified 2026-08-03 — this bit us]**

The user has an existing `ableton-mcp` install pinned to `mcp[cli]==1.28.1` in
`claude_desktop_config.json`. Leave it working; this project should be developed alongside
it on a different port until it can replace it.

The user's app repository `nuzic_app` (GitLab) has a personal access token embedded in
plaintext in the git remote URL. Unrelated to this project, but it was flagged to them and
should be rotated.

---

## 8. Reference material from the diagnostic session

- Skill `ableton-mcp-guia` — operating guide for the *existing* AbletonMCP, with a full
  tool reference in `references/`. Useful as a specification of the behaviour we are
  replacing, and as a checklist of what the new tool must at minimum match.
- PDF *AbletonMCP — Referència completa*, 15 pages, same content in human-readable form.
- `NUZIC_SYSTEM_RULES.md` in the user's `nuzic_app` repository — the formal specification
  of the Nuzic system. Relevant only for Phase 4, and only as a design reference before
  then. Note it is a local untracked file, not committed upstream.
