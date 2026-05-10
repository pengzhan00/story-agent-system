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
DEFAULT_MODEL = "qwen3:8b"        # 快速阶段（场景/角色/音乐词）
CREATIVE_MODEL = "qwen3.6:35b"   # 创作阶段（导演/编剧/审稿）
DETAIL_MODEL = "qwen3.6:35b"
OLLAMA_TIMEOUT_SEC = 7200

STAGE_MODEL_DEFAULTS = {
    "director": CREATIVE_MODEL,
    "writer":   CREATIVE_MODEL,
    "character": DEFAULT_MODEL,
    "scene":    DEFAULT_MODEL,
    "art":      DEFAULT_MODEL,
    "music":    DEFAULT_MODEL,
    "sound":    DEFAULT_MODEL,
    "review": CREATIVE_MODEL,
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


def _is_thinking_model(model_name: str) -> bool:
    """Returns True for models that default to thinking mode (qwen3.x, deepseek-r1, etc.)."""
    lower = (model_name or "").lower()
    return any(k in lower for k in ("qwen3", "deepseek-r1", "qwq", "r1"))


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks produced by reasoning models."""
    import re
    # Greedy removal of one or more think blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def generate(
    prompt: str,
    system: str = "",
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    project_id: int = 0,
    agent_type: str = "",
    stream: bool = False,
    num_ctx: int = 8192,
) -> str:
    """
    Generate text using Ollama.
    Returns the response text.
    Logs to generation_logs automatically.
    """
    actual_model = _pick_model(model)

    is_thinking = _is_thinking_model(actual_model)

    # For thinking/reasoning models, disable chain-of-thought via both mechanisms:
    # 1. Ollama top-level "think": false  (works in Ollama ≥0.6.5 for qwen3/qwq/deepseek-r1)
    # 2. /no_think in system prompt       (chat-template fallback)
    # Both together are more reliable than either alone.
    effective_system = system
    if is_thinking:
        effective_system = "/no_think\n" + (system or "")

    payload = {
        "model": actual_model,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": num_ctx,
        }
    }
    if is_thinking:
        payload["think"] = False   # Ollama native no-think flag

    if effective_system:
        payload["system"] = effective_system

    start = time.time()
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json=payload,
            timeout=OLLAMA_TIMEOUT_SEC,
        )
        duration = int((time.time() - start) * 1000)

        if resp.status_code != 200:
            raise RuntimeError(f"Ollama returned {resp.status_code}: {resp.text}")

        result = resp.json()
        response_text = _strip_think_tags(result.get("response", ""))

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
        raise RuntimeError(f"Ollama timeout after {OLLAMA_TIMEOUT_SEC}s (model: {actual_model})")
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
    num_ctx: int = 8192,
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
            num_ctx=num_ctx,
            agent_type=agent_type,
        )
        cleaned = _clean_json_candidate(text)
        parsed = _try_parse_json_variants(cleaned)
        if parsed is not None:
            return parsed

        if attempt < 2:
            continue

        repaired = _repair_json_via_model(
            raw_text=cleaned,
            model=model,
            project_id=project_id,
            agent_type=agent_type,
        )
        parsed = _try_parse_json_variants(repaired)
        if parsed is not None:
            return parsed

        # Last resort: close uncomplete JSON (truncation recovery)
        truncation_fixed = _close_truncated_json(cleaned)
        if truncation_fixed != cleaned:
            parsed = _try_parse_json_variants(truncation_fixed)
            if parsed is not None:
                return parsed

        raise ValueError(f"Failed to parse JSON after 3 attempts. Raw: {cleaned[:500]}")


def _sanitize_control_chars(s: str) -> str:
    """Replace literal control characters inside JSON string literals with their escape sequences.

    LLMs sometimes emit raw \\n / \\t inside JSON string values, which makes
    json.loads raise 'Invalid control character'.  This walks the JSON character
    by character, only replacing control chars that appear *inside* quoted
    strings (where they are illegal) and leaving the structural whitespace
    (outside strings) untouched.
    """
    _CTRL_MAP: dict[str, str] = {
        "\n": "\\n", "\r": "\\r", "\t": "\\t",
        "\b": "\\b", "\f": "\\f",
    }
    result: list[str] = []
    in_string = False
    escape = False
    for ch in s:
        if escape:
            result.append(ch)
            escape = False
            continue
        if in_string:
            if ch == "\\":
                result.append(ch)
                escape = True
            elif ch == '"':
                result.append(ch)
                in_string = False
            elif ch in _CTRL_MAP:
                result.append(_CTRL_MAP[ch])
            elif ord(ch) < 0x20:
                result.append(f"\\u{ord(ch):04x}")
            else:
                result.append(ch)
        else:
            if ch == '"':
                in_string = True
            result.append(ch)
    return "".join(result)


def _clean_json_candidate(text: str) -> str:
    text = (text or "").strip()
    # Strip <think>...</think> blocks (reasoning models like qwen3.6, qwen3.5, deepseek-r1)
    text = _strip_think_tags(text)
    # Strip markdown code fences
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    # Sanitize control characters inside JSON string values
    text = _sanitize_control_chars(text)
    return text.strip()


def _extract_first_balanced_json(text: str) -> str:
    if not text:
        return ""
    start = None
    open_char = ""
    close_char = ""
    for idx, ch in enumerate(text):
        if ch in "{[":
            start = idx
            open_char = ch
            close_char = "}" if ch == "{" else "]"
            break
    if start is None:
        return ""

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return text[start:].strip()


def _close_truncated_json(text: str) -> str:
    """
    Close an incomplete JSON string caused by max_tokens truncation.
    Tracks three cursor positions and backs up to the last safe comma/bracket
    so we never produce a partial key/value pair.
    """
    if not text:
        return text
    start = next((i for i, c in enumerate(text) if c in "{["), None)
    if start is None:
        return text

    stack: list[str] = []          # expected closing chars (live)
    in_string = False
    escape = False
    # Track safe_pos + safe_stack together: the stack snapshot AT safe_pos.
    # This prevents inflating the closing sequence when a new `[` or `{` is
    # opened AFTER the last safe point (e.g. truncated right after "shots": [).
    safe_pos = start
    safe_stack: list[str] = []

    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
                safe_pos = i + 1           # position AFTER the closing bracket
                safe_stack = list(stack)   # snapshot after the pop
                if not stack:
                    return text[start:i + 1]  # already fully valid
        elif ch == "," and stack:
            safe_pos = i                   # BEFORE the comma (avoids trailing-comma bug)
            safe_stack = list(stack)       # snapshot at this depth

    if not stack:
        return text[start:]

    # Truncated: cut to safe_pos and close using the stack at that point
    closing = "".join(reversed(safe_stack))
    return text[start:safe_pos] + closing


def _try_parse_json_variants(text: str) -> Optional[dict]:
    candidates = []
    cleaned = _clean_json_candidate(text)
    if cleaned:
        candidates.append(cleaned)
    extracted = _extract_first_balanced_json(cleaned)
    if extracted and extracted not in candidates:
        candidates.append(extracted)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _repair_json_via_model(
    raw_text: str,
    model: str,
    project_id: int = 0,
    agent_type: str = "",
) -> str:
    repair_prompt = (
        "Convert the following text into one valid JSON object. "
        "Return ONLY raw JSON. Do not add commentary, markdown, or code fences.\n\n"
        f"{raw_text[:6000]}"
    )
    repair_system = (
        "You repair malformed JSON outputs. "
        "Preserve the original meaning, fill only obvious structural gaps, "
        "and respond with exactly one valid JSON object."
    )
    return _clean_json_candidate(generate(
        prompt=repair_prompt,
        system=repair_system,
        model=model,
        temperature=0.0,
        max_tokens=4096,
        project_id=project_id,
        agent_type=f"{agent_type}_json_repair" if agent_type else "json_repair",
    ))
