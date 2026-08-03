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

## Current state — 2026-08-03

| | |
|---|---|
| Contract | 1.1 (additive; major version is what must match) |
| Remote Script | `remote_script/Alberton_MCP/`, v0.2.0 |
| Server | `server/`, package `alberton-mcp` 0.1.0, 46 tools, `mcp<2` pinned |
| Verified against | Ableton Live 12.4.3 Suite, macOS Apple Silicon, embedded Python 3.11.6 — and the README now says so, promising nothing more |
| Published | No. Publication is deliberately the last step. |

**Tests, all green:**

| Suite | Needs Live | Checks |
|---|---|---|
| `server/tests/` (pytest) | no | 110 |
| `tools/wire_probe.py` | yes | 36 |
| `tools/live_verify.py` | yes | 23 |
| `tools/lifecycle_probe.py` | yes | 23 (+4 manual) |
| `tools/functional_suite.py` | yes | 51, and **46/46 tools exercised** |
| `tools/degenerate_probe.py` | yes | 46 (group and frozen coverage runs when the set has them) |
| `tools/limits_probe.py` | yes | 15 — batch, note and subscription ceilings, overflow |
| `tools/stress_probe.py` | yes | measurement under concurrent human use |
| `tools/scale_report.py` | yes | read-only measurement, no assertions |

---

## Open — decided but not built

Ordered by what a stranger would hit first.

1. **Clean-install rehearsal** — nobody has ever followed the README from nothing. Do
   this last, once the README has stopped moving.

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
