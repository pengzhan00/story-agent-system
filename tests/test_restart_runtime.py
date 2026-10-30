"""A real API process must leave even while a client holds a streaming response open."""
from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import requests


@pytest.mark.parametrize("bounded", [False, True])
def test_api_shutdown_is_bounded_with_an_open_stream(tmp_path: Path, bounded: bool) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "services.toml").write_text('[api]\nshutdown_timeout_seconds = 1\n')
    source = textwrap.dedent('''
        import asyncio, sys, uvicorn
        from fastapi import FastAPI
        from starlette.responses import StreamingResponse
        from storyagent.api import app as module
        from storyagent.config import load_settings
        from storyagent.restart import RESTART_EXIT_CODE
        app = FastAPI()
        @app.get("/ready")
        async def ready():
            return {"ready": True}
        @app.get("/stream")
        async def stream():
            async def endless():
                yield b"data: ready\\n\\n"
                await asyncio.Event().wait()
            return StreamingResponse(endless(), media_type="text/event-stream")
        @app.post("/stop")
        async def stop():
            app.state.leave(RESTART_EXIT_CODE)
            return {"stopping": True}
        module.create_app = lambda *a, **kw: app
        if sys.argv[3] == "unbounded":
            original = uvicorn.Config
            def without_deadline(*args, **kwargs):
                kwargs.pop("timeout_graceful_shutdown", None)
                return original(*args, **kwargs)
            uvicorn.Config = without_deadline
        settings = load_settings({"STORY_AGENT_HOME": sys.argv[1]})
        sys.exit(module.serve(settings, "127.0.0.1", int(sys.argv[2])))
    ''')
    proc = subprocess.Popen([sys.executable, "-c", source, str(home), str(port),
                             "bounded" if bounded else "unbounded"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    response = None
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                if requests.get(base + "/ready", timeout=0.3).status_code == 200:
                    break
            except requests.RequestException:
                pass
            assert proc.poll() is None and time.monotonic() < deadline, "test server did not start"
            time.sleep(0.02)
        response = requests.get(base + "/stream", stream=True, timeout=2)
        assert response.raw.read(1) == b"d"
        assert requests.post(base + "/stop", timeout=2).status_code == 200
        if bounded:
            _, stderr = proc.communicate(timeout=8)
            assert proc.returncode == 75, stderr
        else:
            # Negative control: taking away the deadline must reproduce the original shutdown hang.
            with pytest.raises(subprocess.TimeoutExpired):
                proc.communicate(timeout=2)
    finally:
        if response is not None:
            response.close()
        if proc.poll() is None:
            proc.kill()  # only this test's own child
            proc.communicate(timeout=5)
