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

## Current state — 2026-08-04

| | |
|---|---|
| Contract | 1.1 (additive; major version is what must match) |
| Remote Script | `remote_script/Alberton_MCP/`, v0.2.1 |
| Server | `server/`, package `alberton-mcp` 0.1.0, 46 tools, `mcp<2` pinned |
| Verified against | Ableton Live 12.4.3 Suite, macOS Apple Silicon, embedded Python 3.11.6 — and the README says so, promising nothing more |
| Open work | One item: the clean-install rehearsal. Everything else decided has been built. |
| Published | No. Publication is deliberately the last step. |

**Tests, all green.** Everything needing Live was last run 2026-08-04 against the loaded
29-track / 181-scene *Alberton Multiverse*, except `malformed_probe`, which is green
against both that and an empty set.

| Suite | Needs Live | Checks |
|---|---|---|
| `server/tests/` (pytest) | no | 129 |
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
- **Probes work on scratch material named `ZZ …`** and delete it afterwards. If one dies
  half-way, look for tracks with that prefix; nothing else in the set is ever touched.
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

1. **A name locator resolved to an index writes to whatever now sits at that index.**
   Confirmed 2026-08-04 by widening the window on purpose: `set_track(track="ZZ race 2")`
   resolved to index 29, the human deleted that track, and the write landed on `ZZ race
   3` — **and returned success**. The caller named a track, that track no longer existed,
   and a different one was silently modified. `delete_track` walks the same path.
   `_track_readback` already guards this shape, but only for `create_*_track` and
   `duplicate_track`, where the race was first found by accident; every other tool taking
   a name is exposed. A pre-write identity check — contract 1.1 put `ptr` on `$obj` stubs
   for exactly this — narrows the window to one tick at the cost of a round trip; closing
   it completely needs a conditional op in the Remote Script. **The most important thing
   on this list**, and the only one that can destroy a user's work.
2. **`get_track(detail='full')` cannot see inside a rack.** It reports the top-level
   devices' parameters and stops. Chasing a fault down a real chain — sequencer, [PITCH],
   receiver, plugin, some of them inside racks — meant hand-rolling a walk over
   `…devices.N.chains.M.devices.K`. The LOM can answer the question; no tool here asks it.
3. **Nothing tells a caller that a parameter exists but cannot be read.** A M4L author's
   list-typed parameter is simply absent from `device.parameters` — nine of the Kit
   Selector's twenty-four are — and the answer looks complete. A count, or a note, would
   at least say something is missing.
4. **Clean-install rehearsal** — nobody has ever followed the README from nothing, and
   it is the last thing between here and publication.

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
