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
| Open work | None. Every gate has been passed, the cold-LLM test included. |
| Published | **Yes — public** at `github.com/Alberton-projects/alberton_mcp-for-live` since 2026-08-06. |

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

Nothing. The last item — the clean-install rehearsal — was run on 2026-08-06 and is
recorded below. One residual, too small to be an item: **the Claude Desktop entry has
still only ever been exercised by its author.** Its known defect (hardcoded paths) is
fixed, but nobody has followed the corrected instructions into a working client. That
is a five-minute check for whoever first installs this from the published repository.

Everything else on this list is done. Testing found, in order: the stringified-locator
bug, twelve unusable tool descriptions, a stale watch registry, a `gone` event that never
arrives, an orientation call costing 17 000 tokens, fifteen tools never run against Live,
two opaque errors, a race with a human editing at the same moment, and notes silently
written to a frozen track. None of them were predicted.

## Open — undecided

- ~~The cold-LLM documentation test~~ — **run and passed 2026-08-06**, see the log
  entry. One documentation defect found and fixed the same day.
- Widening the supported scope. Only Live 12.4.3 Suite on macOS Apple Silicon has ever
  been tested; the user cannot currently test Windows or Live 11, so the README states
  that scope and promises nothing beyond it. Revisit when someone reports otherwise.
- One LinkedIn article per thing published to the repository.

---

## Log

### 2026-09-29 — rules made mechanical

`tools/check_rules.py` (+ tests) checks the vocabulary rule, English in code, the Remote
Script's eleven ops and its 127.0.0.1 bind. CI (`.github/workflows/checks.yml`) runs pytest
and the checks; a cloud SessionStart hook installs the server's dev dependencies and states
that Live is not reachable. One test string in `degenerate_probe.py` is marked as deliberate.
Nothing in the server or the Remote Script changed.

### 2026-08-14 — finishing the sweep the previous entry started

A review of the unpushed commits, before pushing them. The fix of 2026-08-11 was right
and incomplete: it corrected the two files it was aimed at and left the same claim
standing elsewhere.

- `ef0c73c` The **prompt the musician pastes** no longer claims Live is already open. It
  ended with "the Ableton set I have open" — read at the one moment when it is not, three
  lines under a requirement asking for Live closed. An assistant taking it literally would
  restore the very order the previous commit removed.
- `62d542a` Both defects had survived in **`tools/introspect/README.md`**: an install
  command with no stated working directory — the `cp: __init__.py: No such file or
  directory` from the clean-install rehearsal, fixed for the real script in `32ca1e4` —
  and a "Restart Live" first step, the wording corrected in `2b5c232`.
- `afbd392` The **prompt now carries the handover**, and asks for a beat. The prompt is
  the only thing the assistant receives; the three manual steps live in the README, which
  only the musician reads — so it described a seamless errand with no hint that there is a
  moment where it must stop and hand back. The demonstration became an 8-bar 4/4 beat on
  drums, bass and minimal percussion instead of a C major arpeggio, with "confirm you can
  read my set" keeping the wiring test the arpeggio used to be.

The lesson is about method, not wording: a phrasing fix is finished when the phrase is
gone from the repository, not when the file that prompted it reads correctly. Both were
found by grepping for the claim, not by re-reading the diff.

### 2026-08-11 — the install instruction had the order backwards

Found while scripting a screen recording of the install: having to choose a shooting
order made the contradiction plain.

- `446aa7e` **Live must be closed while the script is installed, not open.** Live scans
  Remote Scripts only at startup, so a musician following the old requirement ("about
  ten minutes, with Live open") would either have to restart or find no `Alberton MCP`
  in the Control Surface list and conclude the install had failed. Both READMEs now ask
  for Live closed and fold opening it into step 1, with the restart kept as the recovery
  for anyone who already had it running. Still three things, not four.
- `2b5c232` The Remote Script's own README now says **start** Live — or restart it, if
  it was already running — instead of assuming a restart. Copying the two files with
  Live closed is the ordinary case; the restart was the exception described as the rule.

### 2026-08-06 — driven for real, and MIDI CC found its way in

An afternoon using the server as an instrument rather than testing it — reading the
author's own piano piece, analysing it, and writing derived versions back. Two things
came out of it that belong here.

- **MIDI CC is unwritable, and a plugin can hand you the parameter anyway.** Every
  envelope entry point in the LOM takes a `DeviceParameter`, so CC64 and friends are
  unreachable. But Pianoteq's Configure panel publishes its sustain pedal to the host,
  and it then appears in `device.parameters` as an ordinary automatable control —
  confirmed by reading it back the moment the user published it. Generalizes to any
  plugin control: what the plugin exposes is reachable, what it keeps is not.
  HANDOFF §7, and both manuals now carry the limit with its escape hatch.
- **The read-transform-write pattern is documented** (both manuals): read notes,
  transform them outside Live where there is real computation, write to a new track.
  Proved by turning a rubato performance into a quantized two-staff engraving copy
  while the original stayed untouched one track away.

The scripts that did it stayed in scratch, deliberately: the rule that made the hand
split work ("the third attack of each rolled gesture belongs to the left hand") is a
property of that piece's gesture, not of piano engraving, and `tools/` means probes
that verify the server against Live. That work is client work — the same shelf as the
Phase 4 client — and the server earning its keep is precisely that it made the work
possible from outside.

Also corrected in passing, by the author: **half-pedalling is standard technique**, so
a sustain pedal at an intermediate value is a musical choice, not a mistake. The
assistant had dismissed it while proposing `hold` mode. Recorded because it
generalizes: do not infer that a control is binary from how it is usually drawn.

### 2026-08-06 — the cold model: from a URL to a polyrhythm in one sitting

The test this project was built to pass. ChatGPT Desktop on the second user account —
a model that had never seen this code, this conversation, or its author — was given
the public repository URL and a musical task, nothing else. **Four minutes later** it
had cloned, installed the Remote Script, registered the server in its own client (the
Codex-shared config — translating our Claude Desktop instructions unaided), run the
149 tests, guided the human through the Control Surface click, and written a C major
arpeggio into the open set. Over the following hour it iterated tremolo automation by
feel, transposed the clip to B minor atomically, and built a 4:3 clave — rim shots at
beats 0, 4/3, 8/3 — with a bass filling the gaps, using **13 of the 46 tools**,
including browse, load_device and song_batch. It told the user to press Cmd-S because
"Alberton cannot save" — a sentence it could only have learned from the manual.

Its own error report, triaged:

- Three were its client's sandbox (GitHub DNS, local TCP permission, tools not
  loading mid-conversation) — not ours, all self-resolved.
- One was musical taste (tremolo too fast), one was documented behaviour working as
  designed (palette snapping — it accepted the read-back as truth, verbatim from the
  docs).
- One was ambiguous and well-handled: loading two heavy presets outlived the reply
  window; **it did not blindly retry — it read the set and confirmed everything had
  landed**. That recipe is now in the manual.
- And **one was ours**: `song_batch`'s docstring said that to create tracks and fill
  them in one batch you should "pass explicit indices" — advice that cannot work,
  because locators resolve at compile time against the set as it is. The model
  followed the docs into `track 4 out of range`; the atomic rollback held ("no es va
  escriure res", its words), and it split the work in two. The docstring, CONTRACT
  B.9 and both manuals now state the real rule: create first, fill second.

The safety story held end to end: the one failure that reached the musical phase was
rolled back whole, the model trusted the structured error, and recovered correctly on
the first try.

### 2026-08-06 — the clean install, rehearsed by someone who knew nothing

The last gate before publication, run the only honest way available: **a second macOS
user account on the same Mac**, following the repository's own README. That account had
no `uv`, an empty Remote Scripts folder, no venv, no caches — and the person walking it
could not ask the author, because the author was on the other side of a fast user
switch. Six defects, every one fixed the same day, none of them findable from here:

| What a newcomer met | Fixed |
|---|---|
| `wire_probe.py` — the first command the README names — answered a closed Live with a raw Python traceback | `b18338c` |
| The Remote Script install block assumed you stood inside `remote_script/Alberton_MCP/`, while the verify command three paragraphs down assumed the repository root. Neither said so; a fresh clone lands at the root and gets *No such file or directory* | `32ca1e4` |
| `uv` was never mentioned as a prerequisite. It is not part of macOS | `616404e` |
| `uv run --project server alberton-mcp` was presented as "how to run the server". Run it by hand and it sits mute, then reports *Internal Server Error* at the first newline — an stdio MCP server is spawned by a client, not typed at | `616404e` |
| The Claude Desktop snippet carried this machine's absolute paths, with nothing saying which parts were the reader's to change | `616404e` |
| The quick start sent you to configure a client before anything had shown the bridge worked | `619da11` |
| **`uv run --project server pytest` — the documented test command — produced 144 failures on a perfectly good checkout.** pytest resolves its config upward from the arguments, so from the repository root it never reaches `server/pyproject.toml` and `asyncio_mode = auto` is never applied | `1f7919a` |

The last one is the one to remember. It had been wrong since the file was written, and
it survived because **nobody had ever run the documented command** — development used
`cd server && python -m pytest`, which works by accident of the working directory. A
wall of 144 red tests is exactly what makes a stranger conclude they broke it and walk
away. Reproduced here the moment it was reported: identical failure, same count.

Everything else passed on that clean account, and two of them without installing
anything at all: `wire_probe` 36/36, `live_verify` 23/23 (both on the Python macOS
ships), then `uv` fetching Python 3.13 and 42 packages unaided, and `pytest` 149/149
— which includes the smoke test that boots the server over a real stdio transport and
talks to it as an MCP client. So the MCP surface is proven on a clean machine without
anyone logging into a client anywhere.

Two facts about the environment worth keeping: **the bridge port is per-machine, not
per-account** — only one Live can hold 17853 whichever user runs it — and, following
from that, **any other local user can drive your Live**, because localhost is not the
same boundary as "your session".

Older entries (2026-08-02 to 2026-08-05, the build, the review and the probes): `docs/history/session-log-2026-08-02-to-08-05.md`.
