# Alberton MCP for Live — what it can and cannot do

A user's manual. Everything below is either implemented and tested, or marked as
untested; where something is impossible, the reason is given. Written 2026-08-05
against contract 1.2, Remote Script 0.3.2, 46 tools, Ableton Live 12.4.3 Suite on
macOS.

This is the honest map, not the sales pitch. The parts marked **cannot** are things
the Live Object Model does not offer — no amount of work on this server changes them.

---

## What this is

An MCP server plus a companion Remote Script that let a language model read and write
an Ableton Live set: make tracks and clips, write and edit MIDI, drive device
parameters, draw automation, build arrangements, and watch what changes. The model
talks to the server; the server talks to Live through a small generic bridge.

Two rules shape everything else:

- **Time is absolute beats as floats.** Never bars, never note-value names, never
  seconds. A triplet is `1/3` and it round-trips exactly.
- **Every mutating call is one undo step.** One Cmd-Z in Live takes back one call,
  whole. A call that fails leaves nothing behind.

## Before you start

- Ableton Live must be **open**, with the `Alberton MCP` Control Surface selected in
  Preferences → Link, Tempo & MIDI. No Live, no tools.
- **One client at a time.** The bridge accepts a single connection. Running a probe
  from `tools/` displaces the MCP server until its next call reconnects.
- The server works on **whatever set is open**. It keeps no per-document state, so
  loading another set is safe — but indices belong to the set that was open when you
  read them.
- **Nothing is saved automatically.** Everything lands in the live document; saving is
  yours to do (Live's API has no save at all — see *Cannot*).

---

## What it does — with a dedicated tool

### See the set

| Want | Tool |
|---|---|
| The whole set: tempo, signature, scale, tracks, devices, named scenes | `session_overview` |
| One track in depth: mixer, devices, **inside racks**, clips | `get_track` |
| One clip's properties, optionally its notes | `get_clip` |
| Notes of a clip, or **statistics instead of notes** | `get_notes(summary=true)` |
| What changed since you last looked | `watch`, `get_changes`, `unwatch` |

`get_track` walks rack chains: a rack shows its chains, each chain its devices, and
every nested device carries a ready-to-use locator like `"1/0/8/0/4"`. With
`detail='full'` each parameter arrives with its value, range, reading in Live's units,
and whether it is stepped or disabled.

**Ask for summaries before note dumps.** A 150-note clip is a wall of JSON; its
summary is a paragraph — count, pitch range, notes per bar, velocity spread, max
polyphony, and how far the onsets sit off the grid (so you can tell a played take from
a programmed one).

### Write music

| Want | Tool |
|---|---|
| A clip created, named, coloured and filled — one undo step | `create_clip` |
| Surgical note edits by note id | `edit_notes` |
| Rename, recolour, change loop points or signature | `set_clip` |
| Quantize | `quantize_clip` |
| Copy a Session clip to another slot | `duplicate_clip_to_slot` |
| Empty a Session slot | `delete_clip` |
| A clip straight into the Arrangement | `create_arrangement_clip` |
| Copy a Session clip into the Arrangement | `duplicate_clip_to_arrangement` |
| List, edit or remove Arrangement clips | `list_arrangement_clips`, `set_arrangement_clip`, `delete_arrangement_clip` |
| Import an audio file | `import_audio_clip` |
| Structure a human can see | `create_reference_clip` |

Notes carry pitch, start, duration, velocity, mute, probability, velocity deviation
and release velocity. Every note has a **stable id** — that is what makes editing
surgical instead of destroy-and-rewrite.

### Tracks, mixer, scenes, transport

`create_midi_track`, `create_audio_track`, `set_track` (name, colour, arm, mute, solo,
volume in **dB or normalized**, pan, sends), `delete_track`, `duplicate_track`;
`create_scene`, `set_scene`, `delete_scene`; `fire_clip`, `fire_scene`, `stop_clip`,
`stop_all_clips`; `set_song` (tempo, signature, scale, root, groove, metronome);
`transport` (play/stop/continue and the playhead); `show_view`.

### Devices and automation

| Want | Tool |
|---|---|
| Find something loadable | `browse` |
| Load it | `load_device` |
| Re-read the browser after installing a pack | `refresh_browser_index` |
| Set a parameter — including rack macros and devices nested in racks | `set_device_parameter` |
| Draw automation from a few breakpoints | `automate_parameter` |
| Remove automation | `clear_automation` |

`set_device_parameter` takes a value in the parameter's own range, or
`{"display": -6.0}` to write in the units Live shows (dB, %, semitones).
`automate_parameter` takes the **shape** — a few breakpoints — and the server renders
it into the envelope.

### Do several things as one undo step

`song_batch` compiles a sequence of tools into a single atomic batch: all of it lands
or none of it does, and Cmd-Z takes back the lot.

### Reach anything else

`lom_get`, `lom_set`, `lom_call`, `lom_describe` expose the Live Object Model
directly. Anything Live's API offers is reachable through these on the day you need
it — see the next section for what that buys you today.

---

## What it can do through the escape hatch — no dedicated tool yet

These work through `lom_call` / `lom_set` right now. **Every row below was run against
a real Live and observed to work** — there is nothing here on the strength of the
documentation alone.

| Want | How |
|---|---|
| Delete a device | `lom_call` on the track: `delete_device(index)` |
| Move or reorder devices | `lom_call` on song: `move_device(device, track, position)` — see the caveat below |
| Rename a device | `lom_set` its `name` |
| Create a return track | `lom_call` on song: `create_return_track()` — every track gains a send |
| Duplicate a scene | `lom_call` on song: `duplicate_scene(index)` |
| Arrangement locators (markers) | `set_or_delete_cue` at the playhead; `CuePoint.name` writes; `jump()` moves there |
| Track input/output routing | read `available_input_routing_types`, write `input_routing_type` with that element's `$obj` |
| Record Session clips into the Arrangement | set `record_mode`, fire the clip |
| Song loop brace, punch in/out, overdub | `lom_set` on song |
| Move an audio clip's warp markers | `move_warp_marker(beat, distance)` |
| Rewrite a tuning system | `lom_set` `note_tunings` (absolute cents per degree) — verified on 72-EDO |
| Crop a clip, duplicate its loop or a region | `crop()`, `duplicate_loop()`, `duplicate_region(...)` |
| Capture MIDI just played | `capture_midi()` — does nothing, without complaint, when there is nothing to capture |
| Tap tempo, nudge, scrub | `tap_tempo()`, `jump_by(...)`, `scrub_by(...)` |
| Read take lanes (comping) | `take_lanes` on a track — read-only |

**Two caveats worth their own paragraph.** `move_device` returns the index the device
ended up at, and **Live enforces the chain's ordering rules**: on a MIDI track an audio
effect cannot be moved in front of the instrument. An illegal move is not an error — it
returns the unchanged index and nothing happens, so compare before and after (or ask
`find_device_position` first). And `jump_by` moves relative to `song.start_time`, which
is where playback would begin and **not always the visible playhead**: it was seen to
land at beat 4 from a playhead sitting at 39.8. When you want an absolute move, use the
`transport` tool with a position.

**Microtonality is possible.** With a tuning file activated by hand in Live, the whole
scale is readable and writable: `note_tunings` is a plain list of absolute cents per
degree. Verified against a 72-EDO — one degree bent by 5 cents, the rest untouched,
then restored bit-exact. Live's API cannot *activate* a tuning; a human loads the
file, after which everything is drivable.

---

## What it cannot do

Each of these is a property of Live's API, verified. They will not be fixed by working
on this server.

**Max for Live content is invisible.** A `live.step` grid, a multislider — anything a
M4L author declares as a list or blob — is absent from the device's parameter list
entirely. A step sequencer answers with its twenty knobs and **not one of its notes**,
and nothing in the answer says content is missing. `get_track` now marks Max for Live
devices and warns that their parameter lists may be incomplete, which is the most
honest thing available: the API offers no way to count what it is hiding.

**Freeze, unfreeze, group, ungroup.** Read-only in the LOM with no method to set them.
A human does these in Live. (Worse: a frozen track *accepts* note writes through the
API even though Live's own UI locks them — the notes land, the rendered audio does not
change. `edit_notes` warns when it notices.)

**Move an Arrangement clip.** `start_time` and `end_time` have no setter. Moving means
delete and recreate, or duplicate to the new time.

**Follow actions and rack macro variants do not exist** in Live 12.4.3's LOM. Two
obvious-sounding features that simply are not there.

**Add a warp marker.** Live wants a C++ `TWarpMarker` object and no converter accepts
anything JSON can send. Existing markers can be moved and removed.

**Build envelope events directly.** Same family. The server works around it by
rendering shapes as a tiling of small steps — so you get the shape you asked for, made
of steps.

**Change the reference pitch (A = 440).** `ReferencePitch.frequency` has no setter.
The diapason is whatever the tuning file says.

**Export, render or bounce audio.** Nothing in the LOM does it, at all.

**Save the set.** There is no save method anywhere in Live's API. Everything written
lands in the open document; keeping it is a human pressing Cmd-S.

**Touch Live's preferences, install Remote Scripts, or restart Live.**

**Move the playhead past the end of the song.** Live refuses positions beyond
`song_length`.

---

## Surprises worth knowing

Each of these cost somebody an evening.

- **Colours snap to Live's palette.** Ask for `#FF8800`, get `#F66C03`. The read-back
  is the truth.
- **Tempo quantizes to float32.** Write 123.45, read 123.44999694824219.
- **Some properties apply one tick late.** `record_mode`, `loop`, `punch_in`,
  `punch_out`: the read-back in the same call still shows the old value. Read again a
  moment later. (`loop_start`, `loop_length`, `arrangement_overdub` are immediate.)
- **Live reports a parameter's SHORT name.** The device says `PC Interval`, the API
  says `PC ms`; `Cymbals` is `Cymb`. Use the name the tools give you.
- **Firing a clip starts the transport, and `stop_clip` does not stop it.** Use
  `transport(action='stop')`.
- **Loading a second instrument replaces the first** on that track. Effects append.
- **`is_quantized` means "has named steps", not "integers only".** Whether a value
  survived is answered by reading it back, never by the flag.
- **Names survive intact** — quotes, newlines, emoji, CJK, 300 characters. Byte for
  byte.
- **Naming a track is safer than indexing it.** A name is resolved *and guarded*: if
  the object moved or was deleted between the lookup and the write, the call refuses
  and writes nothing. A bare index means "whatever is at that index, now".
- **`create_arrangement_clip` and `import_audio_clip` are two undo steps**, not one:
  the clip must exist before it can be named and filled.

---

## Limits and speed

| | |
|---|---|
| Round trip to Live | ~0.2–0.4 s, **whatever it carries** |
| Operations per batch | 256 |
| Notes per read or write | 20 000 |
| Message size | 16 MiB |
| Active watches | 128 |
| Event queue | 4 096, then an overflow notice |

Because a round trip costs the same whatever it carries, **the number of calls is what
costs time, not their size**. Asking for one big thing beats asking for ten small ones.

Two answers scale themselves rather than dumping everything: `session_overview` omits
the per-slot clip map above 600 slots (and says so), and `get_track(detail='full')`
falls back to parameter names above 400 parameters on a track.

---

## When something goes wrong

Failures arrive as `{"error": {"code", "message", "hint"}}` — never as prose to be
parsed. The codes that matter:

- **`bridge_unreachable`** — Live is closed, or the Control Surface is not selected,
  or another client took the socket.
- **`not_found`** — including *"it moved or was deleted before this call reached
  Live"*, which means **nothing was written**. Read the set again and retry.
- **`conflict`** — the slot or span is occupied.
- **`invalid_argument`** — the hint lists the legal values.

If Live stops responding to everything: reload the Control Surface (set it to None and
back). That restarts the bridge without restarting Live.

---

*This manual describes verified behaviour on Live 12.4.3 Suite, macOS, Apple Silicon.
Nothing has been tested on Windows or on other Live versions. Reasoning and evidence
live in `docs/HANDOFF.md`; the wire and tool specification in `docs/CONTRACT.md`.*
