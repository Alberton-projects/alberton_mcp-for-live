# Session log

Where the project stands, what changed when, and what is still open. Read this first
in a new session; it is the index, not the reasoning.

- **Durable rules** — `CLAUDE.md`
- **Reasoning, decisions and verified Live behaviours** — `docs/HANDOFF.md`
- **Wire protocol and tool catalogue** — `docs/CONTRACT.md`
- **What the LOM actually offers on this machine** — `docs/lom-inventory.md`

Keep entries short. A change belongs here as one line plus its commit; the *why* goes
in HANDOFF, the *spec* in CONTRACT.

---

## Current state — 2026-08-05

| | |
|---|---|
| Contract | 1.2 (additive; major version is what must match) |
| Remote Script | `remote_script/Alberton_MCP/`, v0.3.2 |
| Server | `server/`, package `alberton-mcp` 0.1.0, 46 tools, `mcp<2` pinned |
| Verified against | Ableton Live 12.4.3 Suite, macOS Apple Silicon, embedded Python 3.11.6 — and the README says so, promising nothing more |
| Open work | One item: the clean-install rehearsal. |
| Published | No. Publication is deliberately the last step. |

**Tests, all green.** Everything was re-verified 2026-08-05 after the review, against
the loaded 29-track / 181-scene *Alberton Multiverse*: `live_verify` 23/23,
`functional_suite` 53/53 with 46/46 tools, and — against script 0.3.1 once toggled
in — `wire_probe` 36/36 and `limits_probe` **15/15, including the formerly flaky
overflow check**: 10 notices, 10 dropped, 4 082 changes still delivered, the queue
bounded exactly as designed.

| Suite | Needs Live | Checks |
|---|---|---|
| `server/tests/` (pytest) | no | 149 |
| `tools/wire_probe.py` | yes | 36 |
| `tools/live_verify.py` | yes | 23 |
| `tools/lifecycle_probe.py` | yes | 23 (+4 manual) |
| `tools/functional_suite.py` | yes | 53, and **46/46 tools exercised** |
| `tools/malformed_probe.py` | yes | 59 — calls shaped the way a *model* gets them wrong |
| `tools/degenerate_probe.py` | yes | 46 (group and frozen coverage runs when the set has them) |
| `tools/limits_probe.py` | yes | 15 — batch, note and subscription ceilings, overflow |
| `tools/stress_probe.py` | yes | measurement under concurrent human use |
| `tools/scale_report.py` | yes | read-only measurement, no assertions |

---

## If you are reviewing this

Start here, then `docs/HANDOFF.md` for why things are the way they are and what Live was
actually observed to do. The 2026-08-04 work got its second pair of eyes on 2026-08-05 —
a full-repository review that demonstrated five defects against the fake bridge before
touching code, fixed the same day (see the log entry). What is least examined now:

- **The contextvars guard scope is the freshest code** (`529b098`). Its concurrency
  claim rests on task-copy semantics plus one gather test; the sequential cases are
  pinned hard, including the two that the old test suite could not see.
- **Script 0.3.1's overflow bound has no unit coverage** — the script side never does —
  and is verified only by `limits_probe` against a live instance.
- **The review confirmed the house rule the hard way**: the open item describing the
  overflow defect attributed it to a suppression mechanism that never existed in any
  committed version. Treat any claim here that is not attached to a measurement as a
  guess — including claims about what the code does.
- Nothing in `tools/` is CI: the probes need Live open with the Control Surface selected,
  and only one client may hold the socket at a time.

## How to work on this

Learned by getting it wrong; none of it is obvious from the code.

- **Only one client may talk to the bridge** (CONTRACT A.1). Running any probe in
  `tools/` displaces whatever is connected — including the MCP server inside Claude
  Desktop, whose tools then fail until it reconnects on its next call. Never run two
  probes at once either: the limits probe broke itself this way by opening a second
  socket to measure ping.
- **Do not touch Live while a probe runs.** Adding or removing a track shifts every
  index behind it, and a probe that computed one a moment earlier will act on the wrong
  thing. This is a real hazard, not a testing artefact — it is how the `create_*_track`
  race was found.
- **Probes work on scratch material named `ZZ …`** and delete it afterwards, and each one
  now *sweeps* leftovers before it starts (`tools/scratch.py`), so a killed run heals on
  the next. The prefix is a contract, not a habit: `live_verify` used to call its tracks
  "Alberton MCP verify", and after a killed run those sat in the user's own performance
  set looking like part of the rig.
- **Run anything that touches Live in the background and wait on its summary.** Chaining
  probes inside a foreground time limit kills them mid-run: `functional_suite` died
  half-way that way and left tracks behind. Start them detached, then poll for the
  result.
- **Editing `impl.py` needs no Live restart** — toggle the Control Surface to None and
  back. Only `__init__.py` changing costs a restart, and it is deliberately frozen.
- **Record a commit hash in this file in a *separate* commit.** Writing it and then
  `--amend`ing rewrites the very hash just recorded; it happened twice and left four
  dead references.
- **The test set is `proves MCP-1`**: 6 tracks (Bass, Drums, Structure, Pad, two audio),
  100 BPM, 7/4, E minor, built in the first musical session. The Bass track carries a
  `Bass Raw` rack whose chain holds an Operator — the only nested-device material
  available, and what the rack-path tests use. The larger measurements come from the
  user's own *Alberton Multiverse*: 29 tracks, 181 scenes, 368 clips.

- **Measure before fixing. On this project the stated cause has usually been wrong.**
  Four of the five repairs made on 2026-08-04 began with a diagnosis of mine that did not
  survive a measurement:

  | What the open item said | What was true |
  |---|---|
  | The bridge should survive a bad op | It cannot — Live's own thread hung inside the call, and the tick handler already caught everything. The guard had to move to the parser |
  | An id-less error makes the caller wait out 15 s | `_drop_connection` already fails in-flight requests; what was lost was the *reason* |
  | `get_track` pays for the whole clip map | The clip map was 0.8 s of 3.2 s. The rest was eight sequential awaits |
  | The Kit Selector never stores the Resample FX | `autopattr @greedy` did store it; the recalled value never reached the script — and the fix first proposed would have orphaned saved data |

  The measurement is usually five minutes and it has changed the fix every time. A
  round trip to Live costs ~0.40 s **whatever it carries**, so when something is slow,
  count the awaits before optimising the payload.

- **The server the client talks to is not the source you just edited.** The MCP server
  loads its code once, at start; editing `server/src` changes nothing until it restarts.
  Half an hour went into testing behaviour that had been fixed hours earlier. The Remote
  Script is the opposite — `impl.py` reloads with a Control Surface toggle.
- **Live reports a parameter's SHORT name.** The device declares `PC Interval`; the LOM
  says `PC ms`. `Cymbals` is `Cymb`, `Piano1` is `Pno1`. Every tool here matches the name
  the LOM gives, so a model that reads a long name somewhere and passes it will not find
  the parameter.
- **Run the control case before searching.** Four rounds went into bisecting a timing
  value that appeared to fix a fault. Returning to the *original* value — the user's idea,
  not mine — worked just as well, and showed the timing had never been involved: something
  else had changed underneath while I measured. Prove the fault still reproduces before
  hunting for a threshold.
- **A new probe fails against itself first.** `malformed_probe` reported eight failures on
  its first clean run; six were its own — it built calls outside its `try`, and it knew
  only the Layer B error codes, not the closed wire set in CONTRACT A.7. Read a new
  probe's failures as claims about the probe until proven otherwise.

## Open — decided but not built

1. **Clean-install rehearsal** — nobody has ever followed the README from nothing, and
   it is the last thing between here and publication. The server README's Claude Desktop
   snippet still carries this machine's absolute paths; that is part of this item.

Everything else on this list is done. Testing found, in order: the stringified-locator
bug, twelve unusable tool descriptions, a stale watch registry, a `gone` event that never
arrives, an orientation call costing 17 000 tokens, fifteen tools never run against Live,
two opaque errors, a race with a human editing at the same moment, and notes silently
written to a frozen track. None of them were predicted.

## Open — undecided

- Whether to publish, and where (§4 of HANDOFF). Current intent: yes, but last.
- Widening the supported scope. Only Live 12.4.3 Suite on macOS Apple Silicon has ever
  been tested; the user cannot currently test Windows or Live 11, so the README states
  that scope and promises nothing beyond it. Revisit when someone reports otherwise.
- GitHub repository with a GitLab mirror; the user's existing scripts move in first.
- One LinkedIn article per thing published to the repository.

---

## Log

### 2026-08-05 — a user's manual, and the rows it refused to leave untested

- `459c262` **`docs/MANUAL.md` and `docs/MANUAL.ca.md`** — what the server can do, what
  it can *reach* through the escape hatch, and what Live's API genuinely does not
  offer. Three levels, because a binary can/cannot would have been false. English is
  canonical; the Catalan translation says so at the top.
- **Writing it turned up capabilities nobody had noticed**: `delete_device`,
  `move_device`, `create_return_track`, `duplicate_scene`, `crop`, `duplicate_loop`,
  `duplicate_region`, `capture_midi`, `tap_tempo`, `jump_by`, `scrub_by` — all sitting
  in the inventory, none exercised. The user asked whether they had been tested. They
  had not, so they were: **every one now verified** against the loaded Multiverse on
  ZZ scratch with save-and-restore for the set's own state (tempo, playhead, scene
  count, returns). HANDOFF §7 carries the two with real semantics — `move_device`
  silently refuses a move that breaks Live's chain ordering, and `jump_by` is relative
  to `start_time`, not to the visible playhead.
- The manual's tool names were cross-checked against `server.py`: no invented names,
  and eight real tools it had failed to mention were added.

### 2026-08-05 — microtonality answered: note_tunings writes, and restores bit-exact

- `6e7cab2` **Script 0.3.2**: a Boost setter that declares `boost::python::tuple`
  refuses the list JSON delivers; `_op_set` now retries a TypeError'd list as a
  tuple. Found live, verbatim signature in the error, against the user's
  hand-activated **72-EDO** — which then answered the question the TET-12 decision
  had been standing on: `note_tunings` reads as a plain list of absolute cents from
  degree 0 (float32, steps 16.6667) and **writes over the wire** — one degree bent
  +5 cents, the other 71 untouched, the saved ladder restored with max delta
  0.00e+00. 8/8 checks; `wire_probe` 36/36 against 0.3.2, no regression.
- `ReferencePitch` and `PitchClassAndOctave` confirmed live (A = 440.0, degree 54 of
  72, octave 3). The reference pitch is read-only in practice: `frequency` has no
  setter, and the object property joins the constructed-object family
  (`EnvelopeEvent`, `TWarpMarker`) that JSON cannot feed. HANDOFF §4 records all of
  it: TET 12 by choice, TET 53 a tuning file away.

### 2026-08-05 — the unproven, proven: 33 green checks across four probe runs

- **Cue points, transport flags, routing, take lanes, warp markers, ten devices —
  contrasted** (HANDOFF §7 has the details). The headlines: an Arrangement-locators
  tool is buildable today (full CuePoint lifecycle works); routing is assignable by
  passing an available-list `$obj`; the deferred-apply map is measured (`record_mode`,
  `loop`, `punch_in/out` one tick late; `loop_start/length`, `arrangement_overdub`
  immediate); `move_warp_marker` works and `add_warp_marker` is unconstructible;
  Drum Sampler is class `DrumCellDevice`; loading a second instrument REPLACES the
  first. Probe scripts stayed in scratch; the findings are the deliverable.
- `9fdb98b` **The distiller was eating real methods.** Everything named `add_*` or
  `remove_*` was dropped from the baked summary as presumed listener machinery, so
  `lom_call` refused `add_new_notes`, `remove_notes_by_id`, `add_warp_marker` and
  three more with "no method in the inventory" while Live would have taken them.
  Found because a probe's "not constructible" verdict was too quick to be Live's —
  it was ours. Filter narrowed to `*_listener` machinery; six methods recovered.
- **TET-12 revisited at the user's correction** (HANDOFF §4): the decision had rested
  on "the LOM cannot do microtonality", which is false — an active TuningSystem's
  `note_tunings` are RW in cents. What the LOM cannot do is *activate* one; that
  takes a hand-loaded tuning file, after which writing tunings is one probe away.
  TET 12 stays the scope by choice now, not by believed impossibility.

### 2026-08-05 — recording into the Arrangement, and the map of the unproven

- **Session→Arrangement recording verified over the wire** on the loaded Multiverse:
  `record_mode` + `fire_clip` recorded a ZZ session clip into the Arrangement as a
  looping clip (loop = source length, span = time recorded, notes not unrolled),
  starting at the quantized launch boundary. Three behaviours joined HANDOFF §7:
  `record_mode` applies on the NEXT tick (same-op read-back shows the old value —
  the deferred-apply family gains a second member), `current_song_time` cannot be
  set past `song_length`, and an automated recording must disarm-check and stop all
  session clips first, then restore `back_to_arranger` to what it was. The probe's
  own first run taught the last one: its abort path blanket-reset a flag that
  belonged to the user. Probe script kept in scratch — it mutates performance state
  (arm, queued clips) and is not battery material; the findings are the deliverable.
- **The uncontrasted map is written down** (HANDOFF §7): what deserves a deliberate
  test (cue points, routing, take lanes, punch/overdub/capture, song loop brace,
  warp-marker writes), what is theory until a device is loaded, what is out of scope
  by decision — and two features the inventory vetoes outright: clip follow actions
  and rack macro variants are not in Live 12.4.3's LOM.

### 2026-08-05 — the second LOM dump: theory and evidence stop looking alike

- `3d960af` `70299c5` **Re-introspected against the loaded Multiverse.** The module walk
  is byte-stable across restarts — dir(Live) was always complete, so the server's baked
  validation surface loses and gains nothing. What changed is *evidence*: the inventory
  now marks every class **seen live** or **never met as an instance on this machine**,
  and the count moved from 17 to 30 of 83. Thirteen confirmed: Chain, ChainMixerDevice,
  CompressorDevice, DrumChain, DrumPad, Envelope, Eq8Device, MaxDevice, RackDevice,
  Sample, SimplerDevice, TakeLane, WarpMarker. The introspector gained a class sweep
  (every track, six rack levels deep, drum pads, take lanes, and the Sample /
  warp-marker / envelope classes hanging off each track's first clips); the renderer
  stopped cutting docstrings mid-sentence. The 53 still unmet are named and each has a
  reason — no cue points in the set, no tuning file loaded, a dozen Live devices unused,
  and the MidiNote family reachable only through calls a read-only walk never makes.
  CuePoint is the one worth a deliberate confirmation someday: Arrangement locators are
  an obvious future tool surface. Also recorded: Live's Licensing module exposes an
  attribute whose name is a class name concatenated with its docstring — Ableton's bug,
  reproduced in both dumps, harmless.

### 2026-08-05 — into the racks, and honest about what cannot be seen

- `c56c0b0` **`get_track` walks rack chains** — two round trips per nesting level, none
  on a rack-free track — and every nested device carries the index-based slash locator
  (`"1/0/8/0/4"`) that `set_device_parameter` already takes. `full` reads nested
  parameters in the same one batch as top-level ones, under the same 400-parameter
  track budget; the walk is bounded (200 devices, 8 levels) and says when it stops.
  **Max for Live devices are marked `max_for_live`** and a track holding any carries a
  note that their list/blob parameters are invisible to the LOM and the lists may be
  incomplete — the honest answer, since the LOM offers no way to know what is missing.
  Verified read-only on the loaded set: the drums group's FX rack opens two levels —
  eight sub-racks, a m4l-flagged Gated Delay inside its GATE rack — and MIDI REC's two
  Alberton devices are flagged with the note. 149 unit tests; open items 1 and 2 close.

### 2026-08-05 — the review, and the guard rebuilt

- **Full-repository review on Fable 5** — the model switch was made for exactly this.
  Five defects, every one demonstrated against the project's own fake bridge before any
  diagnosis was trusted, and 0/5 reproduce after the fixes:
- `529b098` **The guard belonged to the call, not the bridge.** As shared bridge state
  it leaked out of read-only calls — `get_track("Bass")`, Bass deleted, and an unrelated
  `set_track` *by index* failed with an error about Bass — and a guard whose index had
  vanished at the tail fell through the expectation_failed-only check into a
  success-shaped empty answer for a write that never ran. Two parallel calls could also
  consume each other's guards. Guards now live in a per-call `contextvars` scope opened
  by `server._run`; any failed probe raises `not_found`; and `song_batch`,
  `set_device_parameter` and `duplicate_clip_to_arrangement` — which had **no guard at
  all** — are covered. The old leak test wrote by index to the read's own untouched
  track, so it pinned the benign case; the new tests pin the deletion, the tail
  deletion, both newly guarded tools and parallel isolation. 142 unit tests.
- `ba96c9c` **`quantize_clip` refused the grids its own hint advertises** — tolerance
  1e-6 against the 3.3e-5 error of the documented 0.3333/0.1667. Unseen because every
  suite quantizes at 0.25.
- `a0964f1` Small hardenings: the dead `isabs` check in audio import, `fire_clip`'s one
  unstructured KeyError, `create_scene`'s unverified read-back (tracks had the guard
  since 2026-08-03, scenes did not), `lom_set`-inside-`song_batch` laxness.
- `23aae9d` **Script 0.3.1.** A failed batch's deferred rollback now runs even when the
  requester vanished within the tick — atomic-or-absent is a promise about the set, not
  the reply. The overflow notice is bounded at one outstanding per subscription: the old
  open item blamed a suppression mechanism that **never existed in any committed
  version** — the notice always bypassed the cap, without bound, and sat behind ~4 096
  queued frames where the probe's early-exit drain never reached it. `get_notes` checks
  its limit before encoding. `limits_probe` now drains to its deadline (old open item 2).
- `3f9cb55` **Docs match the code**: CONTRACT headed 1.2 with `expect` in A.6, counts
  current in all three READMEs, the master-name reservation and the two-undo-step
  exception written down, CLAUDE.md's script rule reworded to what it always meant —
  no vocabulary in the script, but fixes are fine and cost one toggle.
- Verified after the rework against the loaded *Alberton Multiverse*: `live_verify`
  23/23 and `functional_suite` 53/53 with 46/46 tools against the server; then, with
  0.3.1 toggled in, `wire_probe` 36/36 and `limits_probe` 15/15 — the stalled-consumer
  check that had failed once and passed twice on the same build now reports 10 notices,
  10 dropped, 4 082 changes delivered: bounded, and the notice arrives.

### 2026-08-04 — eight waits became three

- `02f82b0` **`get_track` was not paying for the clip map; it was paying for waiting.**
  Of 3.20 s on a 181-scene track, probing every slot was 0.40 s and reading its 70 clips
  another 0.40 s. The rest was eight sequential awaits for work that mostly had no order
  between its parts. A round trip costs a bridge tick whatever it carries, so the count
  is the only thing that matters. Now three: both describes ride in one batch — the
  bridge accepts a `describe` inside a batch, which nothing here had used — and
  everything else follows in one more. The slot probe is gone: an empty slot asked for
  its clip simply fails and comes back None, so reading all 181 blind finds the same 70
  and removes the last read that needed an answer before it could be asked.
  **3.20 s → 1.20 s by index, 1.60 s by name.**

### 2026-08-04 — the race closed, with a window of zero

- **Contract 1.2, script 0.3.0: the `expect` op.** A batch runs in one main-thread slice,
  in order, and stops at the first failure, so an `expect` in front of a write makes the
  window zero — a human dragging a track in Live cannot slip between the check and the
  write, because Live's UI runs on that same thread. A name-resolved ref now carries the
  object's `_live_ptr`, and `_run_atomic` puts the check in front of every mutation
  automatically: all 31 call sites, none of them touched. Verified against Live —
  `not_found`, *nothing was written*, and the ordinary rename still works. Against an
  older script it falls back to reading the identity and undoing afterwards; both paths
  have tests.
- Three things that had to be fixed to make the guard real: the fake bridge gave tracks no
  identity, so the guard **could not fail** there even where it would have failed against
  Live; the server accepted a `$error` as an identity; and `expect` was a top-level op the
  batch allowlist rejected — the only place it is worth anything.
- **Probes sweep their own leftovers now** (`tools/scratch.py`), and `live_verify` finally
  uses the `ZZ` prefix its own convention requires — its `Alberton MCP verify` tracks had
  been sitting in the user's performance set looking native after a killed run.

### 2026-08-04 — driving it for real, with a human in the way

- **The name→index race is confirmed.** Reproduced deliberately by holding the resolution
  open until the human edited the set: the write landed on the wrong track and reported
  success. It is now the first item on the open list.
- **Blob invisibility generalises beyond `live.step`.** The Kit Selector's nine FX
  `multislider`s are absent from `device.parameters` too — the LOM offers 18 of the 24
  parameters the device declares. Anything a Max for Live author declares as a list is
  invisible to us, and the answer does not say so.
- An evening also went to a fault that was not one: a group's filter macro at an extreme,
  put there deliberately and recalled from a saved kit, is indistinguishable from a dead
  device. Written up in the devices' own `REVIEW.md` §11, with the test-hygiene rule it
  produced.

### 2026-08-04 — an error with no id to pin it on

- `e54367a` **The bridge answers `too_large` with `id: null` and hangs up**, because the
  line it refused never parsed far enough to have an id. In-flight requests already
  failed fast — `_drop_connection` sees to that — but the *reason* was thrown away, so a
  caller who asked for more than 16 MiB was told "connection to the bridge lost" and
  would have gone looking for a crashed Live. The client now measures the line and
  refuses an oversized one itself, naming the size, so the bridge is never provoked and
  the error lands on the request that caused it; an id-less refusal that arrives anyway
  is kept, and the drop carries the bridge's own words.
- **`malformed_probe.py` is green against a loaded set too** — 59/59 on the 29-track
  *Alberton Multiverse*, after its health check was reduced from a full
  `session_overview` to one op.

### 2026-08-04 — bridge 0.2.1, and a diagnosis that was wrong

- `14b93fb` **"The bridge should survive a bad op" was not achievable, and the reason is
  the finding.** The tick handler already catches and logs everything, and nothing was
  logged — because nothing was raised. Live's main thread went into `song.tempo = nan`
  and never came out. No Python `try` rescues a call into Live that does not return, so
  the guard moved to the earliest possible point: `json.loads` accepts bare `NaN` and
  `Infinity`, and `parse_constant` makes it refuse them. The frame never exists and the
  refusal falls into the `bad_request` path that was already there — one argument, no new
  code path. Matters for publication rather than for us: the server has refused these
  since `3043d66`, but the bridge is a public socket, and until now any third-party client
  could stop a stranger's Live with a division that went wrong. Verified by sending the
  exact killing frame raw, past both server guards: refused in 0.4 s, next request
  answered normally.

### 2026-08-04 — an unknown enum was quietly the default

- `14c3f2c` **`detail='verbose'` was accepted and answered as `standard`.** Same for
  `session_overview(detail='everything')` and for `automate_parameter`'s `mode`, where
  anything that was not `hold` silently became `ramp`. A caller cannot tell it is
  reasoning on less than it asked for — the failure the structured-error rule exists to
  prevent. `_require_choice` refuses and lists the real values, the idiom
  `refresh_browser_index` already had. The probe's other six failures were its own: it
  knew only the Layer B codes, so every `WireError` looked like a bare exception.
  **59 checks, 0 failed** — against an empty set.

### 2026-08-04 — a number that is not a number

- `3043d66` **The malformed-call probe found a wedge on its first run.** `tempo=NaN` stops
  the bridge permanently: no crash, no traceback, socket still open, main-thread pump
  never answers again. Live had to be force-quit. Two guards now — `_require_number`
  rejects non-finite floats (every comparison with NaN is False, so NaN passed every
  range check in the server), and `bridge.request` refuses to serialise a frame carrying
  one, which covers `lom_set`, parameter values and batch innards where no range check
  exists. `_run` was also dropping the `hint` from every wire error. The probe itself ran
  against the working set and restored the tempo only at the end; it now health-checks
  after each dangerous write and aborts the moment the bridge goes quiet.

### 2026-08-04 — what a caller needs before it writes

- `ec43c7e` **`get_track(detail='full')` now carries each parameter's value, range, its
  reading in Live's units, whether it is stepped and whether it is disabled.** It had
  returned names only, while `set_device_parameter`'s docstring sent callers there for the
  range — a gap nothing tested, which is how the two drifted apart. Found by using the
  server for real work rather than by a probe: driving a Max for Live device meant one
  `lom_get` per parameter, and the project's own functional suite ran exactly that loop.
  A macro-mapped parameter now says so; a write to one is accepted and ignored by Live,
  which nothing previously reported. One batched read per track instead of one per device.
- `786d1cd` **Blob parameters are invisible to the LOM** — a `live.step` grid is not in
  `device.parameters` at all, so a step sequencer answers with its knobs and none of its
  notes. HANDOFF §7.

### 2026-08-03 — group and frozen tracks

- `8b15e9f` **The two states the LOM cannot create**, made by hand and then probed. Groups
  read correctly and their children point at the parent by identity — both children
  return the same `ptr` from different paths, contract 1.1 paying off the day it
  shipped. Frozen tracks refuse clip creation but **accept note writes**, which Live's
  own UI forbids: the note lands, the audio does not change, and the caller was told
  nothing. `edit_notes` now asks about the freeze inside the batch it already sends and
  warns; `session_overview` and `get_track` mark frozen tracks.

### 2026-08-03 — bridge 0.2.0, contract 1.1

- `b79594b` **The first Remote Script change since Phase 2**, and only because testing found
  two absences. `$obj` stubs carry `ptr` — Live's object identity — beside the
  best-effort `path`, which is null for envelopes, Arrangement clips and parameters and
  can go stale whenever a human edits. Answers are written ahead of events, so a client
  can no longer starve its own replies with its own subscriptions: the 1.0 scenario that
  left a connection permanently mute now answers in seconds. The server checks only the
  major contract version, so an older script keeps working.

### 2026-08-03 — under load, and with a human in the way

- `184c921` **Level 2 and stress**: the declared ceilings provoked deliberately (batch 256
  and 257, 16 000 notes, 128 subscriptions, event overflow) and a 90 s session with the
  server hammering while the user played. Nothing broke: 0 disconnects, 0 stalls, no
  audible dropouts, ping 199 ms right after a 16 000-note write. Overflow behaves as
  specified once provoked. Two findings: responses and events share one outbound queue,
  so a client that stops draining starves itself (now a stated client obligation), and
  `create_*_track`'s computed index is invalidated by a human editing at the same
  moment — the read-back now verifies and corrects it.

### 2026-08-03 — degenerate material

- `15b2896` **Degenerate probe**: empty tracks and clips, awkward names, values at their
  limits, nonsense locators. 43 checks. Two bugs: `edit_notes` passed Live's opaque
  "All given IDs must be present" straight through, and `session_overview(detail='full')`
  returned early when there were no Session slots to probe, so it never reported the
  returns or the master. Names round-trip byte-identical including literal newlines —
  the prior art's framing bug, proven absent.

### 2026-08-03 — reaching the whole set

- `9cb8b77` **Locators cover everything**: the track locator now reaches return tracks and
  the master (`"master"`, `"return:0"`, `"return:A-Reverb"`, or their own names — the
  master is called "Main"), with guards so a return is never deleted through the regular
  track vector. The device locator descends into racks with a slash path
  (`"Bass Raw/0/Operator"`), so macros and nested devices are addressable by name.
  `tempo_follower_enabled` joined `set_song`. 18 new unit tests, 13 checks against real
  Live including the Bass Raw rack.

### 2026-08-03 — functional coverage, scale, lifecycle

- `573428e` **Functional suite**: every tool against real Live, on scratch material it
  cleans up; coverage is diffed against the decorators in `server.py` so it reports what
  it never called. 51 checks, 46/46 tools. Fifteen tools had never touched Live before
  this. Turned up three Live behaviours: `is_quantized` means "has named discrete
  values", not "integers only" (Transpose rounds to semitones with the flag false);
  firing a clip starts the transport and `stop_clip` does not stop it; `Song.is_playing`
  is writable.
- `21fbba3` **Scale**: measured against a real 29-track / 180-scene / 368-clip set.
  `session_overview` at `standard` cost 17 402 tokens and 5 973 wire ops probing every
  Session slot; it now scales its answer to the set (2 753 tokens, 281 ops) and says so
  when it omits the clip map. Server-side reads issue concurrently — a round trip costs
  one bridge tick whatever it carries.
- `f514dbb` **Watch liveness**: the bridge's `gone` is passive and in practice never
  arrives, because a deleted object fires no listeners. `get_changes` verifies liveness
  at pull time instead, which suits a pull-based protocol.
- `20a2bea` **Watch registry**: voided when the connection that created it is gone.
  Found because Live was reopened before the set was loaded — the ordinary human order —
  so the server reconnected to a different document.
- `da0effc` **Pre-publication testing**: MCP surface smoke test (CI, no Live) and the
  lifecycle probe (dropped connections, garbage, 16 MiB lines, churn). Twelve tool
  descriptions were too thin to guide a model; rewritten.
- `9c1ec04` Stringified locators accepted; `song_batch` keeps inner error hints.

### 2026-08-03 — v1.1

- `607bcb1` **v1.1**: Arrangement-native writing, audio import with validated paths,
  note summaries for context economy, browser cache invalidation. The clip locator
  became polymorphic.
- `ba53578` **Clip automation**: `automate_parameter` (breakpoints in, stepped envelope
  out, one undo step) and `clear_automation`, pulled forward from v1.1's list.
- `bca1b83` Remote Script folder renamed to `Alberton_MCP`.

### 2026-08-02 — phases 0 to 3

- `591bdca` **Phase 3**: the MCP server, 25 unit tests and 14 end-to-end checks.
- `bedf1bb` **Phase 2**: the Remote Script bridge, 34/34 on the contract probe.
- `b7f3d30` Contract frozen at 1.0.
- `c851849` **Phase 1**: wire protocol and tool catalogue.
- `972b99d` **Phase 0**: LOM introspection; `docs/lom-inventory.md` generated from
  inside Live.
- `6a236ee` Repository bootstrap: name, MIT licence, prior-art attribution.
