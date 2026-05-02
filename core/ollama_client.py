"""
Story Agent System — Unified Ollama API Client
Handles all LLM interactions with retry, logging, and model fallback.
"""
import json
import time
import requests
from typing import Optional, Generator
from .database import log_generation


OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"            # Fast, good Chinese
CREATIVE_MODEL = "qwen3:8b"           # For story generation
DETAIL_MODEL = "qwen3:8b"             # For detailed reasoning

STAGE_MODEL_DEFAULTS = {
    "director": DEFAULT_MODEL,
    "writer": CREATIVE_MODEL,
    "character": CREATIVE_MODEL,
    "scene": CREATIVE_MODEL,
    "art": DETAIL_MODEL,
    "music": DEFAULT_MODEL,
    "sound": DEFAULT_MODEL,
    "review": DETAIL_MODEL,
}

# Available models cache
_available_models: list[str] = []
_last_refresh_failed: bool = False


def refresh_models() -> list[str]:
    """Fetch available models from Ollama."""
    global _available_models, _last_refresh_failed
    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            _available_models = models
            _last_refresh_failed = False
            return models
    except requests.RequestException:
        pass
    # On failure, set flag so list_models() won't retry
    _last_refresh_failed = True
    return _available_models


def list_models() -> list[str]:
    if not _available_models:
        return refresh_models()
    return _available_models


def _pick_model(preferred: str) -> str:
    """Pick the closest available model. Fall back to DEFAULT_MODEL."""
    models = list_models()
    if not models:
        return DEFAULT_MODEL
    # Exact match
    if preferred in models:
        return preferred
    # Try prefix match
    for m in models:
        if m.startswith(preferred.split(":")[0]):
            return m
    # Fallback
    for fallback in [DEFAULT_MODEL, models[0]]:
        if fallback in models:
            return fallback
    return models[0]


def resolve_model_profile(selection: str | dict | None = None) -> dict[str, str]:
    """Resolve a UI/model selection into per-stage concrete models."""
    if isinstance(selection, dict):
        merged = dict(STAGE_MODEL_DEFAULTS)
        merged.update({k: v for k, v in selection.items() if v})
    else:
        base = selection or DEFAULT_MODEL
        merged = {stage: base for stage in STAGE_MODEL_DEFAULTS}
        merged["art"] = selection or DETAIL_MODEL
        merged["review"] = selection or DETAIL_MODEL

    return {stage: _pick_model(model_name) for stage, model_name in merged.items()}


def generate(
    prompt: str,
    system: str = "",
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    project_id: int = 0,
    agent_type: str = "",
    stream: bool = False,
) -> str:
    """
    Generate text using Ollama.
    Returns the response text.
    Logs to generation_logs automatically.
    """
    actual_model = _pick_model(model)

    payload = {
        "model": actual_model,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    }
    if system:
        payload["system"] = system

    start = time.time()
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json=payload,
            timeout=900,
        )
        duration = int((time.time() - start) * 1000)

        if resp.status_code != 200:
            raise RuntimeError(f"Ollama returned {resp.status_code}: {resp.text}")

        result = resp.json()
        response_text = result.get("response", "")

        # Log generation
        try:
            log_generation({
                "project_id": project_id,
                "agent_type": agent_type,
                "model": actual_model,
                "prompt": prompt[:500],
                "response": response_text[:2000],
                "tokens_in": result.get("prompt_eval_count", 0),
                "tokens_out": result.get("eval_count", 0),
                "duration_ms": duration,
            })
        except Exception:
            pass  # Logging failure shouldn't break generation

        return response_text

    except requests.Timeout:
        raise RuntimeError(f"Ollama timeout after 300s (model: {actual_model})")
    except requests.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {OLLAMA_BASE}. "
            "Is 'ollama serve' running?"
        )


def generate_json(
    prompt: str,
    system: str = "",
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,       # Lower temp for structured output
    max_tokens: int = 4096,
    project_id: int = 0,
    agent_type: str = "",
) -> dict:
    """
    Generate text and parse as JSON.
    Retries up to 3 times on parse failure.
    """
    system_prompt = (
        "You are a precise AI assistant. Respond with ONLY valid JSON. "
        "No markdown, no code fences, no explanations. Just raw JSON.\n" + system
    )

    for attempt in range(3):
        text = generate(
            prompt=prompt,
            system=system_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            project_id=project_id,
            agent_type=agent_type,
        )
        # Strip any markdown code fences
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if attempt < 2:
                continue
            raise ValueError(f"Failed to parse JSON after 3 attempts. Raw: {text[:500]}")
