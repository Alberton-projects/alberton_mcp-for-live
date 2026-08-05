"""Scratch material, and getting rid of what a dead run left behind.

Every probe works on tracks named "ZZ …" and deletes them at the end. A run
that is killed — a timeout, a wedged bridge, a traceback in the wrong place —
never reaches its cleanup, and the leftovers sit in whatever set happened to be
open. On a stranger's machine that is untidy; on the user's own performance set
it is alarming, because a track called "Alberton MCP verify" reads like part of
the rig rather than like rubbish.

So the naming is a contract, and the sweep below is what makes it worth having:
each probe clears the previous run's remains before it starts.
"""

SCRATCH_PREFIX = "ZZ "


async def sweep(session, api, note=print):
    """Delete every "ZZ …" track. Returns how many there were."""
    overview = await api.session_overview(session, detail="minimal")
    left = [t for t in overview["tracks"]
            if str(t.get("name", "")).startswith(SCRATCH_PREFIX)]
    for track in sorted(left, key=lambda t: -t["index"]):
        try:
            await api.delete_track(session, track=track["index"])
            note("  swept a leftover from an earlier run: %r" % track["name"])
        except Exception as exc:                   # noqa: BLE001
            note("  could not sweep %r: %s" % (track["name"], exc))
    return len(left)
