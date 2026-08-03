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
| Contract | 1.0, frozen |
| Remote Script | `remote_script/Alberton_MCP/`, v0.1.1 — unchanged since Phase 2 |
| Server | `server/`, package `alberton-mcp` 0.1.0, 46 tools, `mcp<2` pinned |
| Verified against | Ableton Live 12.4.3 Suite, macOS Apple Silicon, embedded Python 3.11.6 |
| Published | No. Publication is deliberately the last step. |

**Tests, all green:**

| Suite | Needs Live | Checks |
|---|---|---|
| `server/tests/` (pytest) | no | 104 |
| `tools/wire_probe.py` | yes | 34 |
| `tools/live_verify.py` | yes | 23 |
| `tools/lifecycle_probe.py` | yes | 23 (+4 manual) |
| `tools/functional_suite.py` | yes | 51, and **46/46 tools exercised** |
| `tools/degenerate_probe.py` | yes | 43 (+2 need a human) |
| `tools/limits_probe.py` | yes | 15 — batch, note and subscription ceilings, overflow |
| `tools/stress_probe.py` | yes | measurement under concurrent human use |
| `tools/scale_report.py` | yes | read-only measurement, no assertions |

---

## Open — decided but not built

Ordered by what a stranger would hit first.

1. **Stress under human hands** — audio rolling while parameters are moved by hand and
   the server writes at the same time. Mostly level 2 (it is what finally exercises
   subscription overflow), but it adds an axis nothing has tested: every suite so far
   assumed the user was not touching Live.
2. **Level 2 testing** — the declared limits: a batch of exactly 256 and 257 ops, a clip
   near the 20 000-note ceiling, subscription event overflow. UI blocking is already
   answered: measured on a 29-track set, Live stayed responsive throughout.
3. **Two degenerate cases need a human**: a group track (Cmd-G) and a frozen track.
   Live exposes neither to the LOM, so `degenerate_probe.py` skips them.
2. **Level 3, portability** — only Live 12.4.3 Suite on macOS has ever been tested.
   Either test Live 11 / other 12.x / Windows, or state the supported scope in the
   README and promise nothing more.
3. **Clean-install rehearsal** — nobody has ever followed the README from nothing. Do
   this last, once the README has stopped moving.

## Open — undecided

- Whether to publish, and where (§4 of HANDOFF). Current intent: yes, but last.
- GitHub repository with a GitLab mirror; the user's existing scripts move in first.
- One LinkedIn article per thing published to the repository.

---

## Log

### 2026-08-03 — under load, and with a human in the way

- `db5a078` **Level 2 and stress**: the declared ceilings provoked deliberately (batch 256
  and 257, 16 000 notes, 128 subscriptions, event overflow) and a 90 s session with the
  server hammering while the user played. Nothing broke: 0 disconnects, 0 stalls, no
  audible dropouts, ping 199 ms right after a 16 000-note write. Overflow behaves as
  specified once provoked. Two findings: responses and events share one outbound queue,
  so a client that stops draining starves itself (now a stated client obligation), and
  `create_*_track`'s computed index is invalidated by a human editing at the same
  moment — the read-back now verifies and corrects it.

### 2026-08-03 — degenerate material

- `5e1abef` **Degenerate probe**: empty tracks and clips, awkward names, values at their
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
