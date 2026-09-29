#!/bin/bash
# SessionStart: in a cloud session Ableton Live is not reachable. Install the server's
# dev dependencies and say so up front, so that nothing that needs Live is reported as
# verified. Local sessions: no output.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

(cd "$CLAUDE_PROJECT_DIR/server" && uv sync --group dev --quiet) >&2

cat <<'JSON'
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "Cloud session: Ableton Live is NOT reachable here (it runs on the owner's Mac; nothing listens on 127.0.0.1:17853 in this container). What you can verify here: `cd server && uv run pytest` (the fake bridge covers the server) and `python3 tools/check_rules.py`. What you cannot: every probe under tools/ that talks to Live (wire_probe, live_verify, lifecycle_probe, functional_suite, malformed_probe, degenerate_probe, limits_probe, stress_probe) and any change to remote_script/, which only Live can exercise. Such changes stay 'pending live verification' in docs/SESSION-LOG.md, with the probes to run; never report them as verified."}}
JSON
