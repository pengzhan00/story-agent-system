#!/usr/bin/env bash
# Start the web server and the worker of this checkout and keep them up; Ctrl-C stops exactly these two (never anything else).
#   scripts/start.sh [--port N] [--host H]
# Needs .venv (see README). Data lives in $STORY_AGENT_HOME (default: ./data).
#
# Hot restart: when the worker or the server ends with exit code 75 (RESTART_EXIT_CODE in storyagent/restart.py - the
# "Update and restart" button of the web client, `storyagent update run --restart`), this script waits for the other one
# to leave too (it asks for the same), runs the database migration and starts both again; the script re-runs itself, so
# a changed start.sh is used. Any other exit code stops everything, as before. While it runs, the marker
# $STORY_AGENT_HOME/update/supervisor.json holds this script's pid: that is how the API knows a restart will be answered.
set -euo pipefail
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$0")/.."
RESTART_EXIT=75                                  # storyagent.restart.RESTART_EXIT_CODE (tests/test_update.py keeps them equal)
RESTART_GRACE="${STORY_AGENT_RESTART_GRACE:-60}" # seconds the second process gets to leave by itself after the first did
PY=".venv/bin/python"
[ -x "$PY" ] || { echo "missing .venv: uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'" >&2; exit 1; }
[ -f web/dist/index.html ] || echo "note: web/dist is not built (cd web && pnpm install && pnpm build); the API still works" >&2

"$PY" -m storyagent db migrate >/dev/null
"$PY" -m storyagent update marker --pid $$ || echo "note: could not write the supervisor marker; the web client will not offer a restart" >&2
"$PY" -m storyagent worker &
WORKER=$!
"$PY" -m storyagent serve "$@" &
SERVER=$!
cleanup() {
  kill "$WORKER" "$SERVER" 2>/dev/null || true
  wait 2>/dev/null || true
  "$PY" -m storyagent update marker --pid $$ --clear >/dev/null 2>&1 || true
}
trap 'exit 130' INT
trap 'exit 143' TERM
trap cleanup EXIT
echo "worker pid $WORKER, server pid $SERVER - Ctrl-C to stop both"
# macOS ships bash 3.2 (no `wait -n`): poll until either process is gone
while kill -0 "$WORKER" 2>/dev/null && kill -0 "$SERVER" 2>/dev/null; do sleep 1; done
WRC=0
SRC=0
kill -0 "$WORKER" 2>/dev/null || wait "$WORKER" || WRC=$?
kill -0 "$SERVER" 2>/dev/null || wait "$SERVER" || SRC=$?
if [ "$WRC" = "$RESTART_EXIT" ] || [ "$SRC" = "$RESTART_EXIT" ]; then
  echo "restart requested - waiting for the other process to leave" >&2
  waited=0
  while [ "$waited" -lt "$RESTART_GRACE" ] && { kill -0 "$WORKER" 2>/dev/null || kill -0 "$SERVER" 2>/dev/null; }; do
    sleep 1
    waited=$((waited + 1))
  done
  kill "$WORKER" "$SERVER" 2>/dev/null || true       # our own children, only if they did not leave in time
  wait 2>/dev/null || true
  echo "restarting worker and server" >&2
  trap - EXIT INT TERM
  exec "$SELF" "$@"
fi
echo "one of the two stopped (worker $WRC, server $SRC) - stopping the other" >&2
[ "$WRC" != 0 ] && exit "$WRC"
exit "$SRC"
