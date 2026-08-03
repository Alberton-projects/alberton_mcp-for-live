# Contract — wire protocol and MCP tool catalogue

Version 1.0 — frozen 2026-08-02 after user review. Designed against
`docs/lom-inventory.md` (Live 12.4.3, embedded Python 3.11.6). Decisions of record:
subscriptions ship in v1; the generic LOM escape hatches are exposed to the model for
read and write; v1 write scope is Session-first (Arrangement-native writing is v1.1).
Changes to Layer A from here on require a version bump and a migration note.

Layer A is what the Remote Script speaks. It is intentionally small and generic: adding
a musical capability must never require touching it. Layer B is the MCP tool catalogue
served to the model; it is implemented entirely in the server by composing Layer A.

---

## Layer A — wire protocol (MCP server ⇄ Remote Script)

### A.1 Transport

- TCP, script listens on `127.0.0.1:17853`. Localhost only, ever (hard rule).
- One client at a time (the MCP server). A new connection replaces the old one; the
  script drops the previous socket and clears its subscriptions.
- The port is a constant in the script. The server reads `ALBERTON_PORT` (default
  17853) so a changed constant needs no server release. Coexists with the legacy
  `ableton-mcp` on 9877.

### A.2 Framing

- Newline-delimited JSON (NDJSON): exactly one JSON object per line, UTF-8, `\n`
  terminator. JSON string escaping guarantees no raw newline inside a document.
- Maximum line size 16 MiB in both directions; longer lines are refused with
  `too_large` (requests) or truncated at source by paging (responses never exceed it).
- Receivers MUST ignore unknown fields in any frame (forward compatibility).
- Never parse an accumulated buffer as a whole; split on `\n` first (the prior-art bug
  this project exists to not have).

### A.3 Frames

Request (server → script):

```json
{"id": 42, "op": "get", "path": "song.tracks.0", "props": ["name", "color"]}
```

Response (script → server) — exactly one per request id:

```json
{"id": 42, "ok": true, "result": {"values": {"name": "Bass", "color": 16725558}}}
{"id": 43, "ok": false, "error": {"code": "path_not_found", "message": "song.tracks.9: index out of range (len 4)", "path": "song.tracks.9"}}
```

Event (script → server, subscriptions only; has no `id`, carries `sub`):

```json
{"event": "change", "sub": 7, "seq": 112, "path": "song", "prop": "tempo", "value": 124.0}
{"event": "overflow", "sub": 7, "seq": 113, "dropped": 240}
{"event": "gone", "sub": 7, "seq": 114, "reason": "path_invalid"}
```

- `id` is any JSON integer or string chosen by the server; the script echoes it.
- Ops execute strictly FIFO on Live's main thread, but the server MUST correlate by
  `id`, not by order: events interleave freely between responses.

### A.4 Value encoding

| LOM value | JSON encoding |
|---|---|
| int / float / bool / str / None | native JSON (`None` → `null`) |
| Boost enum value | integer (names and values are in the inventory) |
| LOM object | `{"$obj": {"class": "Track", "path": "song.tracks.0"}}` |
| LOM vector | `{"$vec": {"class": "Track", "len": 4}}` (elements addressable by path index) |
| anything else | `{"$repr": "<...>", "class": "..."}` — should not appear; file a gap |

Times and durations are always absolute beats as floats (clip-local beats measured from
the clip's content origin for note data; song-absolute beats for Arrangement and
transport). Never bars, never note-value names, never seconds — except properties that
Live itself defines in seconds (e.g. audio-clip sample offsets), which pass through
untranslated and are documented per tool.

Colors on the wire are Live's integer RGB. Layer B accepts/returns `#RRGGBB` and
converts.

### A.5 Path grammar

```
path      = root *("." segment)
root      = "song" / "app"
segment   = identifier / index        ; identifier = LOM property name
index     = 1*DIGIT                   ; 0-based, only valid after a vector-valued segment
```

Examples: `song.tempo` is invalid as a *path* (it is path `song` + prop `tempo`);
`song.tracks.0.clip_slots.2.clip` addresses a Clip. The script resolves paths by
`getattr` chains and vector indexing only — it has no name lookup, no search, no
defaults. Name→index resolution is server-side intelligence.

### A.6 Operations

Eleven ops. This set is the stable surface; growth here needs a contract revision and a
Live restart for every user, so the bar is high.

**`ping`** `{}` → `{"contract": "1.0", "script": "<semver>", "live": "12.4.3",
"python": "3.11.6"}`. Health check and version handshake. The server MUST refuse to
operate on a major-version mismatch of `contract`.

**`describe`** `{path}` → `{"class", "path", "props": {name: <encoded scalar|$obj|$vec>}}`.
One object, one level: every property of the object at `path`, values encoded per A.4
(object-valued properties come back as `$obj`/`$vec` stubs, not expanded). Orientation
primitive; the static shape (writability, docs, methods) lives in the inventory and is
baked into the server, not fetched over the wire.

**`get`** `{path, props: [str, ...]}` → `{"values": {prop: value, ...}}`. Reads are
independent; a failing prop yields `{"$error": {...}}` in its slot rather than failing
the op.

**`set`** `{path, props: {prop: value, ...}}` → `{"values": {...}}` — the **read-back**
values of exactly the props written, re-read after writing. The caller compares; a
silent clamp (e.g. tempo limits) is thereby visible. Partial failure fails the op with
`error.prop` set and no further props attempted (wrap multi-prop invariants in `batch`).

**`call`** `{path, method, args: [...], kwargs: {...}}` → `{"value": <encoded>}`.
Arguments accept the same encoding as A.4 in reverse; `$obj` references are resolved to
live objects before the call. Methods that return LOM objects return `$obj` stubs.

**`get_notes`** `{path, from_time?, time_span?, from_pitch?, pitch_span?}` →
`{"notes": [note, ...]}` for a MIDI clip. Defaults: the whole clip, pitches 0–127.

Note shape (both directions; times in clip-local beats):

```json
{"id": 27, "pitch": 60, "start": 1.5, "duration": 0.3333333333333333,
 "velocity": 96, "mute": false, "probability": 1.0,
 "velocity_deviation": 0.0, "release_velocity": 64}
```

**`edit_notes`** `{path, add?: [note-sans-id], update?: [note-with-id],
remove_ids?: [int], remove_region?: {from_time, time_span, from_pitch?, pitch_span?},
} ` → `{"added_ids": [...], "counts": {"added": n, "updated": n, "removed": n}}`.
Order within the op: remove_region, remove_ids, update, add. The whole op is one undo
step. `update` uses note ids (`apply_note_modifications`); ids are stable per clip
until the note is deleted.

**`batch`** `{ops: [request-sans-id, ...], stop_on_error?: true}` → `{"results":
[response-result-or-error, ...], "rolled_back": bool, "undo_hint": str?}`. Executes
sub-ops FIFO inside a single `begin_undo_step()`/`end_undo_step()`. On sub-op failure
with `stop_on_error` (the default): remaining ops are skipped, the undo step is closed
and immediately undone (`Song.undo()`), `rolled_back: true`, and the per-op results
array shows what succeeded before the failure. A batch is therefore atomic-or-absent in
the set. Sub-ops may be any op except `batch`, `subscribe`, `unsubscribe`, `ping`.
Non-undoable side effects (transport start/stop) execute and cannot roll back; tools
that mix them with edits document it.

**`subscribe`** `{path, props: [str, ...]}` → `{"sub": <int>, "values": {...}}`. Every
prop must have a listener in the inventory (`prop` or vector-membership listeners such
as `tracks`). Returns the subscription id and the current values (so the caller starts
synchronized). **`unsubscribe`** `{sub}` → `{}`. Subscriptions die with the connection.

Event semantics (backpressure by design):

- Listener callbacks only mark `(sub, prop)` dirty. A main-thread flush (~100 ms tick)
  reads current values and emits one `change` event per dirty pair — bursts coalesce to
  the latest value; intermediate values are unobservable by contract.
- `seq` is per-subscription and monotonic. If the outbound queue exceeds its cap the
  script drops oldest events and emits `overflow` with the dropped count; the server
  re-reads via `get` to resynchronize.
- If the subscribed object dies (track deleted), the script emits `gone` and frees the
  subscription.

### A.7 Errors

```json
{"code": "property_read_only", "message": "Clip.length is read-only", "path": "song.tracks.0.clip_slots.0.clip", "prop": "length"}
```

Closed code set (v1): `bad_request`, `unknown_op`, `path_not_found`,
`property_not_found`, `property_read_only`, `method_not_found`, `type_error`,
`not_a_midi_clip`, `live_error` (C++ exception surfaced; `message` carries its text),
`unsupported_in_batch`, `subscription_not_found`, `not_listenable`, `too_large`,
`internal`. `message` is for humans; `code` (+ `path`/`prop`/`method`) is for machines.
No caller ever parses prose to detect failure.

### A.8 Threading model (informative, but load-bearing)

The socket thread only reads lines into an inbox and writes frames from an outbox. All
LOM access happens on Live's main thread: a tick (~100 ms) drains the inbox, executes
ops FIFO, fills the outbox, then flushes dirty subscriptions. Listener callbacks (main
thread, Live-initiated) never touch the socket directly. Nothing in the script blocks
the UI for longer than one op; the 16 MiB and notes-count limits bound op cost.

### A.9 Limits

| Limit | Value |
|---|---|
| Line size | 16 MiB |
| Ops per batch | 256 |
| Notes per `edit_notes` / `get_notes` reply | 20 000 |
| Active subscriptions | 128 |
| Event outbox | 4 096 frames, then `overflow` |
| Server-side request timeout | 15 s (`live_error`/`internal` after) |

---

## Layer B — MCP tool catalogue

Conventions for every tool:

- **Time**: floats in beats. `*_beats` suffixes are omitted; units are beats unless a
  parameter is explicitly `_seconds`. Clip-note times are clip-local; transport and
  Arrangement times are song-absolute.
- **Locators**: `track` accepts an integer index or an exact name (server resolves
  names; ambiguous or missing names are structured errors listing candidates).
  `clip` is `{track, slot}` (Session) — slot is the scene index.
- **Colors**: `#RRGGBB` strings.
- **Every mutating tool** returns read-back state of what it changed, plus the
  canonical locator of what it created. Every mutating tool is one undo step; `batch`
  makes several tools one step.
- **Errors**: `{code, message, hint?}` mirroring A.7 plus `ambiguous_name`,
  `not_found`, `invalid_argument`. `hint` is actionable ("song has 4 tracks, indices
  0–3").
- **Context economy**: overview tools accept `detail: "minimal" | "standard" | "full"`
  (default `standard`) and never dump note arrays unless asked. Since v1.1, note-bearing
  tools also accept `summary` / `note_summary`: statistics (count, pitch range and
  classes, notes per bar, velocity and duration spread, max polyphony, distance from a
  grid) instead of every note. Statistics only — naming chords or keys is the client's
  job, not this server's.

### B.1 Orientation

| Tool | Params | Returns |
|---|---|---|
| `session_overview` | `detail?` | tempo, time signature, scale (name, root), playing state, track list (index, name, color, type midi/audio/return/master, arm/mute/solo, device names, per-slot clip map name+color+playing), scene list |
| `get_track` | `track, detail?` | track props, mixer (volume, pan, sends — normalized and dB/display), devices with parameter names, clip slots |
| `get_clip` | `clip, include_notes?: false` | clip props (name, color, length, loop, signature, playing state) and optionally notes |
| `get_notes` | `clip, from_time?, time_span?, from_pitch?, pitch_span?` | note array (A.6 shape) |
| `get_changes` | `since?: seq` | coalesced change feed from active watches (B.6) with per-event seq; empty array if none |

### B.2 LOM escape hatches (advanced; the full inventory is reachable)

| Tool | Params | Notes |
|---|---|---|
| `lom_describe` | `path` | A.6 `describe` passthrough |
| `lom_get` | `path, props[]` | passthrough |
| `lom_set` | `path, props{}` | passthrough; returns read-back; refuse props absent from the inventory's writable set before touching the wire |
| `lom_call` | `path, method, args?, kwargs?` | passthrough; the server validates the method exists in the inventory |

These four make every future LOM capability reachable the day it is discovered, without
a script change and usually without a server release.

### B.3 Song and transport

| Tool | Params |
|---|---|
| `set_song` | any of `tempo (20–999)`, `signature_numerator`, `signature_denominator`, `scale_name`, `root_note (0–11)`, `groove_amount`, `metronome` |
| `transport` | `action: "play" \| "stop" \| "continue"`, `position?` (song beats; also usable alone to move the playhead) |

### B.4 Tracks, clips, notes, scenes (Session-first)

| Tool | Params → returns |
|---|---|
| `create_midi_track` | `name, color?, index?` → track locator |
| `create_audio_track` | `name, color?, index?` → track locator |
| `set_track` | `track` + any of `name, color, arm, mute, solo, volume (normalized 0–1 \| {"db": x})`, `pan (-1..1)`, `sends: [{index \| name, value}]` → read-back |
| `delete_track` | `track` |
| `duplicate_track` | `track` → new track locator |
| `create_clip` | `track, slot, length, name, color?, signature_numerator?, signature_denominator?, loop?: true, notes?: [note-sans-id]` → clip locator + `added_ids`. One transaction: create+name+colour+signature+fill is a single undo step (the intent-level example from the handoff, verbatim) |
| `set_clip` | `clip` + any of `name, color, loop_start, loop_end, looping, signature_*` → read-back |
| `delete_clip` | `clip` |
| `duplicate_clip_to_slot` | `clip, target: {track, slot}` |
| `edit_notes` | `clip, add?, update?, remove_ids?, remove_region?` → ids + counts (A.6 semantics) |
| `quantize_clip` | `clip, grid (beats float, e.g. 0.25), amount (0–1)` |
| `create_scene` | `index?, name?, color?` → scene locator |
| `set_scene` | `scene, name?, color?` |
| `delete_scene` | `scene` |
| `fire_scene` / `fire_clip` / `stop_clip` / `stop_all_clips` | locators; fire respects launch quantization |

### B.5 Devices and browser

| Tool | Params |
|---|---|
| `browse` | `query, category?: instruments\|drums\|audio_effects\|midi_effects\|sounds` — server-side index over the browser tree; returns loadable URIs with human names |
| `load_device` | `track, uri, position?` |
| `set_device_parameter` | `track, device (index \| name), parameter (index \| name), value (normalized \| {"display": "..."} )` → read-back incl. display string |

### B.6 Watches (Layer A subscriptions, surfaced)

| Tool | Params |
|---|---|
| `watch` | `path, props[]` → `{watch_id, current_values}` |
| `unwatch` | `watch_id` |
| `get_changes` | `since?` → events accumulated server-side (ring buffer 10 000, coalesced per A.6); the reply states `dropped > 0` if the buffer wrapped |

MCP is pull-based, so changes reach the model when it asks; the value of v1 listeners
is that the *server* stays continuously correct (caches invalidate themselves) and the
feed is one cheap call away.

### B.7 Arrangement (v1 = parity with the prior art)

| Tool | Params |
|---|---|
| `show_view` | `"session" \| "arrangement"` |
| `list_arrangement_clips` | `track?` → clips with song-absolute start/end beats |
| `duplicate_clip_to_arrangement` | `clip, time` (song beats) |

**v1.1 (landed 2026-08-03)** — Arrangement-native writing, no new wire ops needed, as
predicted:

| Tool | Params |
|---|---|
| `create_arrangement_clip` | `track, time (song beats), length, name, color?, notes?, signature_*?` — refuses to overlap an existing clip |
| `import_audio_clip` | `track, file_path, time \| slot, name?, color?` — path validated before Live is asked |
| `set_arrangement_clip` | locator + `name, color, muted, start_marker, end_marker, looping, loop_*` |
| `delete_arrangement_clip` | locator |

The clip locator became polymorphic — `{track, slot}` (Session), `{track, time}` or
`{track, arrangement}` (Arrangement) — so `get_clip`, `get_notes`, `edit_notes`,
`automate_parameter` and friends work in both views unchanged.

A clip's Arrangement position is **read-only** in the LOM (`start_time`/`end_time` have
no setter), so "move" is delete-and-recreate. Recorded rather than worked around: an
`insert`-style shuffle would have to rewrite every clip after it.

### B.8 Structure made visible

| Tool | Params |
|---|---|
| `create_reference_clip` | `track, slot, length, name, color?, segments?: [{start, duration?, label}], pulses?: [start...], accents?: [start...]` |

Renders structure as a MIDI clip: one lane of short notes per layer (segments as held
notes with the label in the clip name map, pulses/accents as ticks). Generic music
structure only — no domain vocabulary. This is hard-rule material: output must be
legible to a human who opens the set cold.

### B.9 Atomicity across tools

| Tool | Params |
|---|---|
| `song_batch` | `calls: [{tool, params}, ...], stop_on_error?: true` |

Compiles to one Layer A `batch`: any sequence of B.3/B.4/B.5/B.8 mutating tools becomes
exactly one undo step, atomic-or-absent. Orientation tools are allowed inside for
read-modify-write sequences.

---

## Parity appendix — legacy `ableton-mcp` (21 tools) → this catalogue

| Legacy | Here |
|---|---|
| get_session_info / get_track_info | `session_overview` / `get_track` |
| create_midi_track / set_track_name | `create_midi_track` / `set_track` |
| create_clip / add_notes_to_clip / set_clip_name | `create_clip` (one transaction) / `edit_notes` / `set_clip` |
| create_audio_clip | v1.1 (audio import; needs path handling policy) |
| set_tempo | `set_song` |
| fire_clip / stop_clip / start_playback / stop_playback | `fire_clip` / `stop_clip` / `transport` |
| get_browser_tree / get_browser_items_at_path | `browse` (indexed, queryable) |
| load_instrument_or_effect / load_drum_kit | `load_device` (+ `browse`) |
| switch_to_arrangement_view / set_arrangement_time | `show_view` / `transport{position}` |
| get_arrangement_clips / duplicate_to_arrangement | `list_arrangement_clips` / `duplicate_clip_to_arrangement` |

Gaps vs legacy: `create_audio_clip` only — accepted for v1 (Session MIDI is the core
use), scheduled v1.1.

## Out of scope for v1 (recorded so they are choices, not oversights)

- ~~Arrangement-native writing~~ — **landed 2026-08-03** (§B.7)
- ~~Audio clip import~~ — **landed 2026-08-03**. Policy chosen with the user: any
  absolute path, validated server-side (exists, regular file, readable, non-empty,
  known audio extension — override the list with `ALBERTON_AUDIO_EXTENSIONS`) so
  mistakes are structured errors rather than opaque LOM exceptions. No sandbox: the
  server already runs with the user's own permissions.
- ~~Automation envelope *writing*~~ — **landed 2026-08-03**, ahead of schedule, once the
  envelope API had been exercised against Live: `automate_parameter` (breakpoints in,
  step-rendered envelope out, one undo step) and `clear_automation`. `lom_set`/`lom_call`
  became batchable inside `song_batch` at the same time.
- ~~Browser index invalidation~~ — **landed 2026-08-03**: `browse(refresh=true)` and
  `refresh_browser_index(category?)`, with the cache age reported in every `browse`
  reply. Automatic invalidation stays out: Live exposes no browser-changed listener.
- Any authentication on the socket (localhost bind is the boundary; revisit only if
  that ever changes)
