#!/usr/bin/env bash
# Bring THIS checkout (the production one: the main checkout) up to date with origin/main.
#   scripts/deploy.sh            fast-forward, install, build the web client, migrate the database
# It never stops or restarts anything: after it finishes, stop the running scripts/start.sh (Ctrl-C) and start it
# again - the worker and the server load the code once. Check `storyagent media jobs --status running` first: a
# restart interrupts a running heavy job (it is re-queued).
# The same steps run from the web client ("System > Update and restart", admins) or `storyagent update run` -
# storyagent/update.py - and, when scripts/start.sh supervises worker and server, the update ends with a hot restart
# (both exit with code 75 and start.sh starts them again). Keep the two lists of steps in step with each other.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -z "$(git status --porcelain)" ] || { echo "uncommitted changes here; production must be a clean checkout" >&2; exit 1; }
git fetch origin
git merge --ff-only origin/main
if [ ! -x .venv/bin/python ]; then uv venv --python 3.12 .venv; fi
uv pip install --python .venv/bin/python -e .
(cd web && pnpm install --frozen-lockfile && pnpm build)
.venv/bin/python -m storyagent db migrate      # snapshots the database first
.venv/bin/python -m storyagent i18n check
git log --oneline -1
echo "done - now restart scripts/start.sh"
