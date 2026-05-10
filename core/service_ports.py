"""
Dynamic service port discovery and auto-launch for ComfyUI and ACEStep.

Usage:
    from core.service_ports import get_comfyui_url, get_acestep_url

Both functions scan common ports, auto-launch the service if offline,
and cache the result for the process lifetime.
Call invalidate_cache() to force a re-scan.

Timing policy
─────────────
ACEStep loads large models into GPU/MPS memory.  To avoid memory contention
we wait until ComfyUI's render queue is fully empty before launching ACEStep.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

# ── port scan order ───────────────────────────────────────────────────────────
# ComfyUI community default is 8188; Desktop/App variants commonly use 8000/8080.
# Do not probe 8001 here — that is reserved for ACE-Step and creates noisy 404s.
COMFYUI_PORTS: list[int] = [8188, 8000, 8080]
# ACEStep API server defaults to 8001 (start_api_server_macos.sh)
ACESTEP_PORTS: list[int] = [8001, 8002, 8003]

# ACE-Step-1.5 source directory (has its own .venv)
ACESTEP_DIR = Path(os.path.expanduser("~/ACE-Step-1.5"))
# The venv created by `uv sync` inside ACE-Step-1.5
ACESTEP_VENV_PYTHON = ACESTEP_DIR / ".venv" / "bin" / "python3.12"

# ── module-level cache ────────────────────────────────────────────────────────
_lock = threading.Lock()
_comfyui_url: Optional[str] = None   # e.g. http://127.0.0.1:8000
_comfyui_api: Optional[str] = None   # e.g. http://127.0.0.1:8000/api  (App) or same as url (CLI)
_acestep_url: Optional[str] = None


def _configured_comfyui_url() -> Optional[str]:
    """Optional explicit ComfyUI URL from env.

    Supported variables:
      - STORY_AGENT_COMFYUI_URL
      - COMFYUI_URL
    """
    raw = (
        os.getenv("STORY_AGENT_COMFYUI_URL", "").strip()
        or os.getenv("COMFYUI_URL", "").strip()
    )
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw.rstrip("/")


def _normalize_comfyui_candidate(raw_url: str) -> tuple[str, str]:
    """Return (base_url, api_base) for an explicit candidate URL.

    Examples:
      http://127.0.0.1:8000      -> (http://127.0.0.1:8000, http://127.0.0.1:8000)
      http://127.0.0.1:8000/api  -> (http://127.0.0.1:8000, http://127.0.0.1:8000/api)
    """
    url = raw_url.rstrip("/")
    if url.endswith("/api"):
        return url[:-4], url
    return url, url


def comfyui_api_base() -> str:
    """Return the ComfyUI API base (includes /api prefix for the Desktop App)."""
    global _comfyui_api
    if _comfyui_api:
        return _comfyui_api
    # trigger full detection
    get_comfyui_url()
    configured = _configured_comfyui_url()
    if configured:
        return _comfyui_api or _normalize_comfyui_candidate(configured)[1]
    return _comfyui_api or (_comfyui_url or "http://127.0.0.1:8188")


# ─────────────────────────────────────────────────────────────────────────────
# Low-level probes
# ─────────────────────────────────────────────────────────────────────────────

def _probe_comfyui(port: int, timeout: float = 2.0) -> Optional[str]:
    """
    Return the API base URL if ComfyUI responds on this port, else None.
    Handles both:
      • ComfyUI CLI  → /queue
      • ComfyUI App  → /api/queue
    """
    base = f"http://127.0.0.1:{port}"
    for prefix in ("", "/api"):
        try:
            r = requests.get(f"{base}{prefix}/queue", timeout=timeout)
            if r.status_code == 200:
                body = r.json()
                if "queue_running" in body or "queue_pending" in body:
                    return f"{base}{prefix}"   # API base including prefix
        except Exception:
            pass
    return None


def _probe_comfyui_url(raw_url: str, timeout: float = 2.0) -> Optional[tuple[str, str]]:
    """Probe an explicit ComfyUI URL and return (base_url, api_base) if live."""
    base, api = _normalize_comfyui_candidate(raw_url)
    candidates = []
    if api.endswith("/api"):
        candidates.append(api)
    else:
        candidates.extend((api, f"{api}/api"))
    for candidate in candidates:
        try:
            r = requests.get(f"{candidate}/queue", timeout=timeout)
            if r.status_code == 200:
                body = r.json()
                if "queue_running" in body or "queue_pending" in body:
                    return base, candidate
        except Exception:
            pass
    return None


def _probe_acestep(port: int, timeout: float = 2.0) -> bool:
    """True if ACEStep /health endpoint responds on this port."""
    try:
        r = requests.get(f"http://127.0.0.1:{port}/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        pass
    return False


def _comfyui_queue_busy(comfyui_api: str, timeout: float = 3.0) -> bool:
    """Return True if ComfyUI has running or pending jobs.
    comfyui_api is the API base (already includes /api if needed).
    """
    try:
        r = requests.get(f"{comfyui_api}/queue", timeout=timeout)
        if r.status_code == 200:
            q = r.json()
            running = q.get("queue_running", [])
            pending = q.get("queue_pending", [])
            return bool(running) or bool(pending)
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Scanners
# ─────────────────────────────────────────────────────────────────────────────

def _scan_comfyui() -> Optional[tuple[str, str]]:
    """Returns (base_url, api_base) or None. e.g. ('http://127.0.0.1:8000', 'http://127.0.0.1:8000/api')"""
    configured = _configured_comfyui_url()
    if configured:
        explicit = _probe_comfyui_url(configured)
        if explicit:
            base, api_base = explicit
            suffix = " [App /api]" if api_base.endswith("/api") else " [CLI]"
            print(f"[ServicePorts] ComfyUI found via COMFYUI_URL={configured}{suffix}")
            return base, api_base
        print(f"[ServicePorts] COMFYUI_URL 已配置但当前不可达: {configured}")
        return None
    for port in COMFYUI_PORTS:
        api_base = _probe_comfyui(port)
        if api_base:
            base = f"http://127.0.0.1:{port}"
            suffix = " [App /api]" if api_base.endswith("/api") else " [CLI]"
            print(f"[ServicePorts] ComfyUI found on port {port}{suffix}")
            return base, api_base
    return None


def _scan_acestep() -> Optional[str]:
    for port in ACESTEP_PORTS:
        if _probe_acestep(port):
            print(f"[ServicePorts] ACEStep API found on port {port}")
            return f"http://127.0.0.1:{port}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Auto-launchers
# ─────────────────────────────────────────────────────────────────────────────

def _launch_comfyui() -> Optional[str]:
    """Start ComfyUI in the background and wait up to 60 s."""
    try:
        from core.comfyui_env import COMFYUI_DIR, resolve_comfyui_python, comfyui_main_py
    except ImportError:
        print("[ServicePorts] Cannot import comfyui_env — skipping ComfyUI auto-launch")
        return None

    venv_python = resolve_comfyui_python()
    main_py = comfyui_main_py()
    if not venv_python.exists() or not main_py.exists():
        print(f"[ServicePorts] ComfyUI not found at {COMFYUI_DIR} — skipping auto-launch")
        return None

    # Clean up stale source-tree ComfyUI CLI processes started on the wrong port
    # (for example an older 8000 listener) so this project owns a single CLI target.
    try:
        pattern = shlex.quote(f"{COMFYUI_DIR}/.venv/bin/python3 main.py")
        subprocess.run(
            f"pkill -f {pattern}",
            shell=True,
            timeout=10,
            check=False,
        )
        time.sleep(1)
    except Exception as e:
        print(f"[ServicePorts] Warning: failed to clean stale ComfyUI CLI processes: {e}")

    configured = _configured_comfyui_url()
    port = COMFYUI_PORTS[0]
    if configured:
        try:
            parsed = urlparse(configured)
            if parsed.hostname in {"127.0.0.1", "localhost", None} and parsed.port:
                port = parsed.port
        except Exception:
            pass
    logfile = "/tmp/comfyui_auto.log"
    cli_db = COMFYUI_DIR / "user" / "comfyui_cli.db"
    cmd = (
        f"cd {COMFYUI_DIR} && nohup {venv_python} main.py "
        f"--listen 127.0.0.1 --port {port} "
        f"--database-url sqlite:///{cli_db} "
        f"> {logfile} 2>&1 &"
    )
    print(f"[ServicePorts] Launching ComfyUI on port {port} — log: {logfile}")
    subprocess.run(cmd, shell=True, timeout=10)

    for _ in range(30):
        time.sleep(2)
        result = _scan_comfyui()
        if result:
            return result[0]  # base url

    print(f"[ServicePorts] ComfyUI launch timed out. Check {logfile}")
    return None


def _wait_comfyui_idle(comfyui_api: str, max_wait: int = 600, poll: int = 10) -> None:
    """
    Block until ComfyUI queue is empty or max_wait seconds pass.
    comfyui_api is the API base (already includes /api prefix if needed).
    """
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if not _comfyui_queue_busy(comfyui_api):
            return
        print(f"[ServicePorts] ComfyUI queue busy — waiting {poll}s before launching ACEStep…")
        time.sleep(poll)
    print("[ServicePorts] ComfyUI idle wait timed out; launching ACEStep anyway")


def _launch_acestep() -> Optional[str]:
    """
    Start ACEStep REST API server in the background and wait up to 120 s.

    Uses the existing .venv inside ~/ACE-Step-1.5 with PYTHONPATH — avoids
    `uv run` which triggers cross-platform dependency resolution and tries to
    download Windows-only flash-attn wheels (useless on macOS).
    """
    if not ACESTEP_DIR.exists():
        print(f"[ServicePorts] ACE-Step-1.5 dir not found: {ACESTEP_DIR}")
        return None

    # ── timing: wait for ComfyUI to finish any active renders ────────────────
    result = _scan_comfyui()
    if result:
        _wait_comfyui_idle(result[1])  # pass api_base

    port = ACESTEP_PORTS[0]
    logfile = "/tmp/acestep_auto.log"

    # Prefer the .venv shipped with ACE-Step-1.5 (python3.12, all deps installed
    # by prior `uv sync`).  Set PYTHONPATH so `import acestep` resolves from
    # the source tree without needing a proper editable-install.
    # This completely avoids `uv run` and its cross-platform dep resolution.
    if ACESTEP_VENV_PYTHON.exists():
        python_exe = ACESTEP_VENV_PYTHON
        cmd = (
            f"cd {ACESTEP_DIR} && "
            f"ACESTEP_LM_BACKEND=mlx "
            f"TOKENIZERS_PARALLELISM=false "
            f"PYTHONPATH={ACESTEP_DIR} "
            f"nohup {python_exe} "
            f"  {ACESTEP_DIR}/acestep/api_server.py "
            f"  --host 127.0.0.1 --port {port} "
            f"> {logfile} 2>&1 &"
        )
    else:
        # Fallback: uv run (may hit flash-attn issue on first sync)
        print("[ServicePorts] ACE-Step .venv not found — falling back to uv run")
        cmd = (
            f"cd {ACESTEP_DIR} && "
            f"ACESTEP_LM_BACKEND=mlx "
            f"TOKENIZERS_PARALLELISM=false "
            f"nohup uv run --no-sync acestep-api "
            f"  --host 127.0.0.1 --port {port} "
            f"> {logfile} 2>&1 &"
        )

    print(f"[ServicePorts] Launching ACEStep API on port {port} — log: {logfile}")
    subprocess.run(cmd, shell=True, timeout=10)

    # Model loading takes ~60–120 s (MLX compilation on first run)
    print("[ServicePorts] Waiting for ACEStep models to initialise (up to 120 s)…")
    for _ in range(60):
        time.sleep(2)
        url = _scan_acestep()
        if url:
            print(f"[ServicePorts] ACEStep ready at {url}")
            # ── warm-up: trigger DiT + LM model loading ────────────────────
            # ACEStep uses lazy loading by default; POST /v1/init forces it
            # to load all weights into memory immediately so the first music
            # request doesn't time out waiting for model init.
            print("[ServicePorts] Warm-up ACEStep: triggering model init via /v1/init …")
            try:
                r = requests.post(
                    f"{url}/v1/init",
                    json={"init_llm": True, "lm_model_path": "acestep-5Hz-lm-1.7B"},
                    timeout=300,   # allow up to 5 min for first MLX compilation
                )
                if r.status_code == 200:
                    d = r.json()
                    print(f"[ServicePorts] ACEStep init OK — "
                          f"DiT={d.get('models_initialized')} "
                          f"LM={d.get('llm_initialized')}")
                else:
                    print(f"[ServicePorts] ACEStep /v1/init returned {r.status_code}: {r.text[:200]}")
            except Exception as e:
                print(f"[ServicePorts] ACEStep warm-up failed (non-fatal): {e}")
            return url

    print(f"[ServicePorts] ACEStep launch timed out. Check {logfile}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_comfyui_url(auto_launch: bool = True) -> str:
    """Return the live ComfyUI base URL (without /api prefix).

    Use comfyui_api_base() to get the URL with correct API prefix for HTTP calls.
    Scans COMFYUI_PORTS on first call; caches the result.
    """
    global _comfyui_url, _comfyui_api
    with _lock:
        if _comfyui_url:
            explicit = _probe_comfyui_url(_comfyui_url)
            if explicit:
                _comfyui_url, _comfyui_api = explicit
                return _comfyui_url
            # A prior auto_launch=False call may have cached a fallback URL that
            # was never actually live. Allow a later call with auto_launch=True
            # to recover by re-scanning / launching instead of returning stale cache.
            if not auto_launch:
                return _comfyui_url
            _comfyui_url = None
            _comfyui_api = None
        result = _scan_comfyui()
        if not result and auto_launch:
            launched = _launch_comfyui()
            if launched:
                result = (launched, launched)  # CLI launched, no /api prefix
        if result:
            _comfyui_url, _comfyui_api = result
        else:
            configured = _configured_comfyui_url()
            fallback = (
                _normalize_comfyui_candidate(configured)[0]
                if configured
                else f"http://127.0.0.1:{COMFYUI_PORTS[0]}"
            )
            _comfyui_url = fallback
            _comfyui_api = (
                _normalize_comfyui_candidate(configured)[1]
                if configured
                else fallback
            )
        return _comfyui_url


def get_acestep_url(auto_launch: bool = True) -> str:
    """Return the live ACEStep REST API base URL.

    Scans ACESTEP_PORTS on first call; caches the result.
    If not found and auto_launch=True, waits for ComfyUI to be idle,
    then starts the ACEStep server automatically.
    Falls back to http://127.0.0.1:8001 if everything fails.
    """
    global _acestep_url
    with _lock:
        if _acestep_url:
            return _acestep_url
        url = _scan_acestep()
        if not url and auto_launch:
            url = _launch_acestep()
        _acestep_url = url or f"http://127.0.0.1:{ACESTEP_PORTS[0]}"
        return _acestep_url


def invalidate_cache() -> None:
    """Clear cached URLs so the next call re-scans all ports."""
    global _comfyui_url, _comfyui_api, _acestep_url
    with _lock:
        _comfyui_url = None
        _comfyui_api = None
        _acestep_url = None


def comfyui_online() -> bool:
    """Quick non-caching check — does not auto-launch."""
    return bool(_scan_comfyui())


def acestep_online() -> bool:
    """Quick non-caching check — does not auto-launch."""
    return bool(_scan_acestep())


def comfyui_status_dict() -> dict:
    """Status dict for UI display (non-caching)."""
    result = _scan_comfyui()
    if result:
        base, api = result
        mode = "App" if api.endswith("/api") else "CLI"
        return {"online": True, "url": base, "api_base": api, "mode": mode}
    return {"online": False, "url": "", "api_base": "", "mode": ""}


def acestep_status_dict() -> dict:
    """Status dict for UI display (non-caching)."""
    url = _scan_acestep()
    return {"online": bool(url), "url": url or ""}
