# Alberton — the Remote Script

The Live-side half of Alberton MCP for Live: a thin, generic bridge implementing
Layer A of `docs/CONTRACT.md` (NDJSON over `127.0.0.1:17853`, eleven ops, atomic
batches, subscriptions). All intelligence lives in the MCP server; this script should
essentially never change.

## Install

**Every command in this file is run from the repository root** — the folder you get
from `git clone`, the one holding `README.md` and `docs/`. Not from this folder.

```
DEST="$HOME/Music/Ableton/User Library/Remote Scripts/Alberton_MCP"
mkdir -p "$DEST" && cp remote_script/Alberton_MCP/__init__.py \
                      remote_script/Alberton_MCP/impl.py "$DEST/"
```

The folder name must keep the underscore (Live imports it as a Python module and a
space breaks the import); Live's Control Surface list renders it as "Alberton MCP".

Use only this location (not the one under `~/Library/Preferences/Ableton/...`): two
copies produce duplicate Control Surface entries and a port clash.

macOS only, so far. On Windows the folder is
`%USERPROFILE%\Documents\Ableton\User Library\Remote Scripts\` — untested; see the
scope note in the top-level README.

## Run

1. Restart Live (it only scans Remote Scripts at startup).
2. Preferences → Link, Tempo & MIDI → any free Control Surface slot → `Alberton MCP`
   (Input/Output: None).
3. The status bar shows `Alberton: listening on 127.0.0.1:17853`.

## Verify

With Live running and the surface selected, from the repository root:

```
python3 tools/wire_probe.py
```

36 contract-compliance checks; tempo is restored and the probe's scratch track is
deleted afterwards. This probe needs nothing installed — it speaks the socket
directly, so the Python that ships with macOS runs it. If it cannot connect it says
what to check.

## Iterate without restarting Live (development only)

`__init__.py` is a frozen loader that executes `impl.py` from disk on every
instantiation. After editing and re-copying `impl.py`, toggle the Control Surface slot
to `None` and back — the new bridge runs immediately. Restart only if `__init__.py`
itself changes.

Logs: `alberton.log` (runtime) and `crash.log` (loader failures), both next to the
installed script.
