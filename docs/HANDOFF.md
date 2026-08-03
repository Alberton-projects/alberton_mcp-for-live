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
completed 2026-08-03: the bridge (`remote_script/Alberton_MCP/`, v0.1.1 — the folder
was renamed from `Alberton` the same day, shown as "Alberton MCP" in Live) passes all 34
checks of the contract probe (`tools/wire_probe.py`) against a live instance. Phase 3
was completed the same night: `server/` (package `alberton-mcp`, `mcp<2` pinned) serves
the full Layer B catalogue — 39 tools — with 25 unit tests against an in-process fake
bridge and a 14-check end-to-end run (`tools/live_verify.py`) against real Live, both
green. **v1.1 landed 2026-08-03** and closed the whole out-of-scope list bar the
deliberate exclusions: clip automation (`automate_parameter`, `clear_automation`),
Arrangement-native writing (`create_arrangement_clip`, `set_arrangement_clip`,
`delete_arrangement_clip`, polymorphic clip locators), audio import
(`import_audio_clip`, validated paths), browser-cache invalidation, and a `summary`
mode on the note-reading tools for context economy. 59 unit tests and 23 end-to-end
checks against real Live, all green. The publishing decision (§4) is still open,
deliberately. The user works on macOS with Ableton Live 12.4.3 Suite and has
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

**Contract 1.1 / bridge 0.2.0**, 2026-08-03 — the first change to the Remote Script
since Phase 2, made because testing found two absences rather than because a feature
wanted them. Both additive: `$obj` stubs now carry `ptr` (Live's own object identity)
alongside the best-effort `path`, and the script keeps two outbound queues so answers
are written ahead of events. The server compares only the **major** contract version, so
an older script still works with a newer server.

The priority queue is verifiable: under 1.0, a connection with a throttled receive
buffer and 120 playhead subscriptions never answered a `ping` again; under 1.1 the same
scenario replies within seconds while still reporting 125 overflow notices. Identity
matters because a path is null for envelopes, Arrangement clips and device parameters,
and because any path can be invalidated a millisecond later by a human editing in Live.

Under load, measured 2026-08-03. **The main thread does not stall.** Writing 16 000
notes takes 3.0 s end to end and a ping straight afterwards returns in 199 ms against a
200 ms baseline; 90 s of concurrent hammering — the server storming reads and writes
while a human dragged faders, switched views and loaded devices with audio playing —
gave 0 disconnects, 0 stalls over 1 s, ping median 70 ms, and **no audible clicks or
dropouts** (judged by ear, which no probe can do).

The event outbox does overflow as designed, but only when provoked: a real session ran at
~12 events/s, four orders of magnitude below the cap. Throttling a client's receive
buffer and subscribing 120 times to `current_song_time` produced 40 overflow notices,
each naming what it dropped, with 4 076 changes still delivered. The lesson is a client
obligation, now in CONTRACT: **responses and events share one outbound queue**, so a
client that stops draining starves its own command responses — the drowned connection
never answered a ping while a fresh one to the same script answered instantly. The
saturation is per connection and reconnecting clears it.

`create_*_track` computes the new track's index from a count taken before the call, so a
human adding or removing a track at that moment invalidates it — this bit us for real
during a stress session. The read-back now verifies the name is where it should be,
finds it if not, and reports a conflict rather than guessing when the name is in two
places. A millisecond window remains inside the create-and-name batch, and closing it
would cost the atomicity that makes creating a named clip one undo step.

`Clip.remove_notes_by_id` refuses the whole call unless **every** id is present, and
says only "All given IDs must be present in clip" — so the server reads the clip back on
that failure and names the missing ones. Note ids are per clip and are not reused after
a delete. **[verified 2026-08-03]**

Frozen tracks, **[verified 2026-08-03]** on a track frozen by hand. Reads all work.
`ClipSlot.create_clip` and `Track.create_midi_clip` refuse with "Clips cannot be created
on frozen tracks". Mixer writes are allowed. But **`add_new_notes` on a clip of a frozen
track succeeds and really writes the note** — Live's own UI locks that clip, its API does
not, and the rendered audio does not change, so a caller gets success and hears nothing.
The server now says so: `edit_notes` asks `is_frozen` inside the batch it was already
sending (no extra round trip) and returns a warning, while `session_overview` and
`get_track` report `frozen` on the tracks that are. A frozen track can be deleted.

Group tracks, **[verified 2026-08-03]** on a group made by hand. The group reads as
`is_foldable` with `fold_state` read-write, `can_be_armed` false and `arm` null, and it
has clip slots of its own where `is_group_slot` and `controls_other_clips` are true.
Children report `is_grouped` and a `group_track` stub — and both children's stubs carry
the *same* `ptr` from different paths, which is contract 1.1 earning its keep on the day
it shipped. `Song.delete_track` refuses to remove the last remaining member of a group
("Couldn't delete track"); delete the group itself instead.

Neither freezing nor grouping is exposed to the LOM: `is_frozen`, `can_be_frozen`,
`is_grouped`, `is_foldable` and `group_track` are all read-only and there is no method to
set them. Those two states can only be produced by hand in Live, so tests that need them
must skip rather than build them. **[verified 2026-08-03]**

Names survive the wire intact — quotes, backslashes, JSON-looking text, literal newlines
and carriage returns, tabs, emoji, CJK, right-to-left script, 300 characters, the empty
string. Written and read back byte-identical on Live 12.4.3. The newline case is the one
that matters: it is the framing bug in the prior art, and JSON string escaping means
NDJSON never sees it. **[verified 2026-08-03]**

Regular tracks, return tracks and the master live in three separate places
(`song.tracks`, `song.return_tracks`, `song.master_track`) and each counts from zero, so
an index alone is ambiguous across them — and `Song.delete_track(0)` on a return would
delete the regular track sharing that index. Returns have their own
`Song.delete_return_track`; the master can be neither deleted nor duplicated. Neither
returns nor the master hold Session clips (zero clip slots) and the master has no sends.
Live 12.4.3 names the master track **"Main"**. **[verified 2026-08-03]**

`DeviceParameter.is_quantized` means "has named discrete values" (Operator's Algorithm),
**not** "accepts only whole numbers". Others round silently anyway — Transpose takes
semitones over −48..48 with `is_quantized` false. So whether a written shape survived is
answered by reading it back, never by the flag; `automate_parameter` reports a `snapped`
boolean derived from the read-back for exactly this reason. **[verified 2026-08-03]**

Firing a Session clip starts the transport, and `stop_clip` does not stop it. A position
written to a rolling transport has already advanced by the time it is read back — not a
clamp, just time passing. **[verified 2026-08-03]**

`Song.is_playing` is writable: setting it starts and stops playback. Do not reach for it
as an example of a read-only property. **[verified 2026-08-03]**

Scale, measured on a real 29-track, 180-scene, 368-clip set (`tools/scale_report.py`,
read-only) **[verified 2026-08-03]**: a wire round trip costs ~200 ms regardless of
payload, because the script services its inbox on a ~100 ms tick — so latency, not work,
is the bottleneck, and server-side reads should be issued concurrently rather than
chunk-by-chunk. A 200-op batch round-trips as fast as a single `get`, and Live's UI stays
responsive throughout (ping ~200 ms straight after the heaviest read), because the
script's 50 ms per-tick budget bounds how long the main thread spends. The trap is
response size, not speed: probing every Session slot to draw a clip map cost 5 973 wire
ops and ~17 000 tokens of JSON on that set. Orientation tools must scale their answer to
the set, not to their own completeness.

Loading another set does **not** keep the connection alive: Live tears the Remote Script
down and re-creates it per document, so the socket drops and comes back — the same path
as a restart. And deleting a watched object emits **no** event at all: the script's
liveness check only runs while servicing a listener callback, and a dead object fires
none. Both **[verified 2026-08-03]**; the server compensates by verifying watch liveness
when `get_changes` is called.

Deselecting the Control Surface closes the listening socket cleanly — no orphan
listener — and reselecting it brings the bridge back; a server holding one Session
reconnects on its own, subscriptions included. The same holds across a full Live quit
and restart. **Note the ordinary human sequence**: people open Live first and load their
set afterwards, so a reconnecting server can land on a *different document* than the one
it left. The server keeps no per-document state, so that works — but subscription ids
are per-connection and restart at 1 in a new Live, which is why the server voids its
watch registry and event feed whenever the connection that created them is gone.
**[verified 2026-08-03]**

An Arrangement clip's position is read-only: `Clip.start_time` and `end_time` have no
setter, so a clip cannot be moved — delete and recreate it, or duplicate to the new
time. `Track.create_midi_clip(time, length)` and `Track.create_audio_clip(path, time)`
place clips directly in the Arrangement, and the returned `Clip` has no canonical path
(it lives in `track.arrangement_clips`, which the bridge's `_path_of` does not scan);
find it by matching `start_time`, which is exact because two Arrangement clips on one
track cannot start at the same beat. Creating a clip that overlaps an existing one lets
Live silently trim the neighbour, so the server refuses it instead.
**[verified 2026-08-03]**

Track/clip colors snap to Live's palette on write (`#FF8800` reads back `#F66C03`).
Tools must treat the read-back as canonical. **[verified 2026-08-03]**

Clip automation, all **[verified 2026-08-03]** while building `automate_parameter`:
`Clip.create_automation_envelope` is **not** idempotent — it raises "There is already an
envelope for the parameter", so find-then-create. The resulting `Envelope` has no
canonical path (`_path_of` returns null for it); address it through the clip's
`automation_envelopes` vector, and match it to its parameter by comparing `_live_ptr`,
because `Envelope.parameter` comes back as a path-less stub and two devices on one track
can expose identically named macros. `insert_step(time, duration, value)` is the only
writable primitive (`EnvelopeEvent` objects cannot be constructed over the wire), so
smooth shapes are rendered server-side as a tiling of small steps.
`Envelope.value_at_time(t)` sampled exactly on a step boundary reports the step that
*ends* there, and at beat 0 — with nothing before it — the parameter's static value;
probe at step midpoints instead.

`DeviceParameter.display_value` is numeric in the parameter's *display units*, both to
read and to write — despite the docstring suggesting a string. Writing `-6.0` to a
volume parameter sets the fader to −6 dB (normalized value read back 0.6999…). This is
the clean route for dB-addressed mixing; the string rendering lives in
`str_for_value`. **[verified 2026-08-03]**

The user had an existing `ableton-mcp` install pinned to `mcp[cli]==1.28.1` in
`claude_desktop_config.json`; the plan was to develop alongside it until this project
could replace it. — Replaced 2026-08-03: the config now carries an `alberton` entry
pointing at `server/` and the legacy entry is gone (timestamped backup kept next to the
config). The legacy Remote Script folder and its Control Surface slot can be retired at
leisure; the only feature gap until v1.1 is audio-clip import.

The user's app repository `nuzic_app` (GitLab) has a personal access token embedded in
plaintext in the git remote URL. Unrelated to this project, but it was flagged to them and
should be rotated.

**A Max for Live device's blob parameters are invisible to the LOM, and they can hold the
device's entire musical content.** A `live.*` object whose parameter is declared
`parameter_type 3` (blob) — `live.step` is the common case — is absent from
`device.parameters`, absent from the set's `ParameterList`, and unreachable by any tool we
have. It is saved separately, in an `MxDBlob` chunk. A step sequencer therefore reports
its twenty-odd knobs and *not one of its notes*. Two consequences. First, the LOM is not a
complete view of a M4L device, and a caller cannot tell from the parameter list that
anything is missing. Second, Live restores the blob **after** the ordinary parameters, so
a device whose UI mirrors blob state in a normal parameter can load with the two
disagreeing — observed in the wild: sequencer lanes playing with their Active toggles
showing off, unfixable from the UI because the toggle already held the right value.
**[verified 2026-08-04, Live 12.4.3, against four Step Sequencer instances]**

`get_track(detail='full')` returns device parameter **names only** (`api.py`, the `_gets`
over `…parameters.%d` asks for `name`). The docstring of `set_device_parameter` tells the
caller to look there for a parameter's `[min, max]`, which is not on offer — the range
needs a `lom_get` per parameter, one round trip each. Either the docstring or the tool is
wrong; the tool is the better thing to fix, since choosing a legal value is the first
thing a caller needs. **[found 2026-08-04]**

---

## 8. Reference material from the diagnostic session

- Skill `ableton-mcp-guia` — operating guide for the *existing* AbletonMCP, with a full
  tool reference in `references/`. Useful as a specification of the behaviour we are
  replacing, and as a checklist of what the new tool must at minimum match.
- PDF *AbletonMCP — Referència completa*, 15 pages, same content in human-readable form.
- `NUZIC_SYSTEM_RULES.md` in the user's `nuzic_app` repository — the formal specification
  of the Nuzic system. Relevant only for Phase 4, and only as a design reference before
  then. Note it is a local untracked file, not committed upstream.
