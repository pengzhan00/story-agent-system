"""Load a changed configuration into the running server: the context (settings, ledger, stores) is rebuilt and
swapped in, so the next request sees the new models, templates and plugins. Requests already running finish on the
old one."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import anyio
from fastapi import FastAPI

from storyagent.config import Settings
from storyagent.config.reload import fingerprint

log = logging.getLogger(__name__)


def reload_now(app: FastAPI, loader: Callable[[], Settings]) -> str:
    """Read the configuration afresh and swap it in. Returns the error text ("" = reloaded); on an error the old
    configuration stays."""
    from .app import build_context

    try:
        settings = loader()
        app.state.ctx = build_context(settings)
        app.state.seen = fingerprint(settings.config_dir)
        app.state.reload_error = ""
        log.info("configuration reloaded")
    except Exception as exc:  # noqa: BLE001 - a bad file must not take the running server down
        app.state.reload_error = f"{type(exc).__name__}: {exc}"[:400]
        log.warning("configuration not reloaded: %s", app.state.reload_error)
    return str(app.state.reload_error)


async def watch(app: FastAPI, loader: Callable[[], Settings], every: float) -> None:
    """Every ``every`` seconds: has the config folder changed? Then reload."""
    while True:
        await asyncio.sleep(every)
        folder = app.state.ctx.settings.config_dir
        now = await anyio.to_thread.run_sync(fingerprint, folder)
        if now != app.state.seen:
            app.state.seen = now          # a failed reload is not retried until the files change again
            await anyio.to_thread.run_sync(reload_now, app, loader)
