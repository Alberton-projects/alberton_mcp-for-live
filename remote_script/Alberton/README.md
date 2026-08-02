# Alberton — the Remote Script

The Live-side half of Alberton MCP for Live: a thin, generic bridge implementing
Layer A of `docs/CONTRACT.md` (NDJSON over `127.0.0.1:17853`, eleven ops, atomic
batches, subscriptions). All intelligence lives in the MCP server; this script should
essentially never change.

## Install

```
DEST="$HOME/Music/Ableton/User Library/Remote Scripts/Alberton"
mkdir -p "$DEST" && cp __init__.py impl.py "$DEST/"
```

Use only this location (not the one under `~/Library/Preferences/Ableton/...`): two
copies produce duplicate Control Surface entries and a port clash.

## Run

1. Restart Live (it only scans Remote Scripts at startup).
2. Preferences → Link, Tempo & MIDI → any free Control Surface slot → `Alberton`
   (Input/Output: None).
3. The status bar shows `Alberton: listening on 127.0.0.1:17853`.

## Verify

With Live running and the surface selected:

```
python3 tools/wire_probe.py
```

34 contract-compliance checks; tempo is restored and the probe's scratch track is
deleted afterwards.

## Iterate without restarting Live (development only)

`__init__.py` is a frozen loader that executes `impl.py` from disk on every
instantiation. After editing and re-copying `impl.py`, toggle the Control Surface slot
to `None` and back — the new bridge runs immediately. Restart only if `__init__.py`
itself changes.

Logs: `alberton.log` (runtime) and `crash.log` (loader failures), both next to the
installed script.
