#!/usr/bin/env bash
# One deterministic detection pass, for a systemd timer.
#
# Prints nothing when nothing happened, which is the normal case; systemd then
# has no output to journal. Resolves Hermes's selected dependency environment
# rather than hard-coding a venv path, because PM rebuilds it on upgrade.
set -uo pipefail

PLUGIN="${PITWALL_PLUGIN:-$HOME/.hermes/plugins/pitwall-agent}"
STATE="${PITWALL_STATE:-$HOME/.hermes/state}"
LOG="$STATE/pitwall-events.log"

PY=""
facts="$(ls -1 "$HOME"/.hermes/installs/*/facts.json 2>/dev/null | head -1)"
if [ -n "$facts" ] && command -v python3 >/dev/null 2>&1; then
  venv="$(python3 - "$facts" <<'EOF' 2>/dev/null || true
import json, sys
try:
    print(json.load(open(sys.argv[1]))["packages"]["venv"]["environment"])
except Exception:
    pass
EOF
)"
  [ -n "${venv:-}" ] && [ -x "$venv/bin/python" ] && PY="$venv/bin/python"
fi
[ -n "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || exit 0

# Desktop popups are a per-machine preference: PITWALL_NOTIFY=0 in the unit
# silences them without touching detection, the event log or --react.
args=(--once --react)
[ "${PITWALL_NOTIFY:-1}" != "0" ] && args+=(--notify)

exec env -u PYTHONPATH PYTHONPATH="$PLUGIN" "$PY" -m pitwall.cli watch "${args[@]}" \
  --log "$LOG"
