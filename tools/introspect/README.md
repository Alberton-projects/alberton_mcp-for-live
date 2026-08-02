# AlbertonIntrospect — Phase 0 tooling

One-shot, read-only Remote Script that dumps the Live Object Model of the running Live
instance to `docs/lom-raw.json` (plus a progress log `docs/lom-introspect.log`). The
human-readable `docs/lom-inventory.md` is rendered from the JSON outside Live.

Superseded in Phase 2 by the real Remote Script; kept for reproducing the inventory on
new Live versions.

## Install

```
DEST="$HOME/Music/Ableton/User Library/Remote Scripts/AlbertonIntrospect"
mkdir -p "$DEST" && cp __init__.py impl.py "$DEST/"
```

## Run

1. Restart Live (it only scans Remote Scripts at startup).
2. Have a set open with at least: one MIDI track with an instrument and a clip with a few
   notes, and one audio track with a short clip. More variety = richer instance dump.
3. Preferences → Link, Tempo & MIDI → any free Control Surface slot → `AlbertonIntrospect`
   (leave Input/Output as None).
4. Within ~5 s the status bar shows `Alberton Phase 0: LOM dump written`.

## Iterate without restarting Live

`__init__.py` is a loader that executes `impl.py` from disk on every instantiation.
After editing `impl.py` (and re-copying it to DEST), set the Control Surface slot to
`None` and back to `AlbertonIntrospect` — the new code runs immediately. A Live restart
is only needed when `__init__.py` itself changes.

Load failures are appended to `crash.log` next to the installed script.
