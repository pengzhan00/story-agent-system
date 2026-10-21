#!/usr/bin/env bash
# Start the web server and the worker of this checkout; Ctrl-C stops exactly these two (never anything else).
#   scripts/start.sh [--port N] [--host H]
# Needs .venv (see README). Data lives in $STORY_AGENT_HOME (default: ./data).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"
[ -x "$PY" ] || { echo "missing .venv: uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'" >&2; exit 1; }
[ -f web/dist/index.html ] || echo "note: web/dist is not built (cd web && pnpm install && pnpm build); the API still works" >&2

"$PY" -m storyagent db migrate >/dev/null
"$PY" -m storyagent worker &
WORKER=$!
"$PY" -m storyagent serve "$@" &
SERVER=$!
trap 'kill "$WORKER" "$SERVER" 2>/dev/null || true; wait 2>/dev/null || true' INT TERM EXIT
echo "worker pid $WORKER, server pid $SERVER - Ctrl-C to stop both"
# macOS ships bash 3.2 (no `wait -n`): poll until either process is gone
while kill -0 "$WORKER" 2>/dev/null && kill -0 "$SERVER" 2>/dev/null; do sleep 2; done
echo "one of the two stopped - stopping the other" >&2
