#!/usr/bin/env bash
# Bring THIS checkout (the production one: the main checkout) up to date with origin/main.
#   scripts/deploy.sh            fast-forward, install, build the web client, migrate the database, check i18n
# The steps are not written here: they are `storyagent update run` (storyagent/update.py), the very code behind the
# "Update and restart" button in the web client (System page, administrators) - same steps, checks, log format
# (<home>/logs/update-*.log) and audit record. Change them there, once.
# This script never stops or restarts anything:
#   * with scripts/start.sh running (it supervises worker and server): click "Restart only" in the web client
#     (System > Update and restart) afterwards, or use the button for the whole update instead of this script - it
#     waits for running jobs and restarts both by itself (exit code 75);
#   * without it: stop the running processes (Ctrl-C) and start scripts/start.sh again - worker and server load the code
#     once. Check `storyagent media jobs --status running` first: a restart interrupts a running heavy job (re-queued).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -z "$(git status --porcelain)" ] || { echo "uncommitted changes here; production must be a clean checkout" >&2; exit 1; }
if [ ! -x .venv/bin/python ]; then      # first deployment: the CLI needs the package installed once
  uv venv --python 3.12 .venv
  uv pip install --python .venv/bin/python -e .
fi
.venv/bin/python -m storyagent update run
git log --oneline -1
echo "done - restart: web client System > Update and restart > Restart only (if scripts/start.sh is running), else Ctrl-C and run scripts/start.sh again"
