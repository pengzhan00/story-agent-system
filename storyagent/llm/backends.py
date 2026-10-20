"""One client per backend kind. All of them speak ``chat(model, system, prompt, ...) -> ChatResult``.

* ``OllamaClient``       - ``/api/chat``; JSON-schema output through ``format``; ``think`` is switched off for
                           reasoning models (otherwise the answer is buried in a thinking block).
* ``OpenAICompatClient`` - ``/v1/chat/completions``; serves llama.cpp's ``llama-server`` (single model or router
                           mode) and any other OpenAI-compatible server; JSON-schema output through ``response_format``.
Clients never start or stop servers.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from storyagent.config import LLMBackend

from .errors import LLMError
from .jsonfix import strip_think

DEFAULT_TIMEOUT = 3600.0
_THINKING_HINTS = ("qwen3", "deepseek-r1", "qwq", "gpt-oss", "gemma4", "magistral")


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    truncated: bool = False  # the model stopped because it hit the token limit


class LLMBackendClient(Protocol):
    name: str

    def chat(
        self, model: str, system: str, prompt: str, *, temperature: float = 0.7, max_tokens: int = 4096,
        num_ctx: int | None = None, schema: dict[str, Any] | None = None, timeout: float | None = None,
    ) -> ChatResult: ...

    def list_models(self) -> list[str]: ...


def _http(error: requests.RequestException, url: str) -> LLMError:
    if isinstance(error, requests.Timeout):
        return LLMError("timeout", f"timeout talking to {url}", url=url)
    if isinstance(error, requests.ConnectionError):
        return LLMError("unreachable", f"cannot connect to {url}", url=url)
    return LLMError("backend_error", str(error), url=url)


class OllamaClient:
    def __init__(self, backend: LLMBackend, session: requests.Session | None = None) -> None:
        self.name, self.url, self.options = backend.name, backend.url.rstrip("/"), dict(backend.options)
        self._http = session or requests.Session()

    def list_models(self) -> list[str]:
        try:
            r = self._http.get(f"{self.url}/api/tags", timeout=10)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except (requests.RequestException, ValueError, KeyError) as exc:
            if isinstance(exc, requests.RequestException):
                raise _http(exc, self.url) from exc
            raise LLMError("backend_error", str(exc), url=self.url) from exc

    def _thinks(self, model: str) -> bool:
        hints = self.options.get("thinking_models") or _THINKING_HINTS
        return any(h in model.lower() for h in hints)

    def chat(
        self, model: str, system: str, prompt: str, *, temperature: float = 0.7, max_tokens: int = 4096,
        num_ctx: int | None = None, schema: dict[str, Any] | None = None, timeout: float | None = None,
    ) -> ChatResult:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens,
                        "num_ctx": num_ctx or int(self.options.get("num_ctx", 16384))},
        }
        if schema is not None:
            payload["format"] = schema
        keep = self.options.get("keep_alive")
        if keep is not None:
            payload["keep_alive"] = keep
        use_think = self._thinks(model) and self.options.get("disable_thinking", True)
        if use_think:
            payload["think"] = False
        t0 = time.monotonic()
        data = self._post(payload, timeout)
        if data is None and use_think:  # the server rejected `think` for this model: ask again without it
            payload.pop("think", None)
            data = self._post(payload, timeout)
        assert data is not None
        text = strip_think((data.get("message") or {}).get("content", ""))
        return ChatResult(
            text, model, int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0)),
            int((time.monotonic() - t0) * 1000), data.get("done_reason") == "length",
        )

    def _post(self, payload: dict[str, Any], timeout: float | None) -> dict[str, Any] | None:
        try:
            r = self._http.post(f"{self.url}/api/chat", json=payload, timeout=timeout or DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise _http(exc, self.url) from exc
        if r.status_code == 404 and "not found" in r.text.lower():
            raise LLMError("model_missing", r.text[:200], model=payload["model"], backend=self.name)
        if r.status_code == 400 and "think" in r.text.lower() and "think" in payload:
            return None
        if r.status_code != 200:
            raise LLMError("backend_error", f"HTTP {r.status_code}: {r.text[:300]}", url=self.url)
        try:
            data: dict[str, Any] = r.json()
        except ValueError as exc:
            raise LLMError("backend_error", "backend returned invalid JSON", url=self.url) from exc
        return data


class OpenAICompatClient:
    def __init__(self, backend: LLMBackend, session: requests.Session | None = None) -> None:
        self.name, self.url, self.options = backend.name, backend.url.rstrip("/"), dict(backend.options)
        self._http = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        key = self.options.get("api_key")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def list_models(self) -> list[str]:
        try:
            r = self._http.get(f"{self.url}/v1/models", headers=self._headers(), timeout=10)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except requests.RequestException as exc:
            raise _http(exc, self.url) from exc
        except (ValueError, KeyError) as exc:
            raise LLMError("backend_error", str(exc), url=self.url) from exc

    def chat(
        self, model: str, system: str, prompt: str, *, temperature: float = 0.7, max_tokens: int = 4096,
        num_ctx: int | None = None, schema: dict[str, Any] | None = None, timeout: float | None = None,
    ) -> ChatResult:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature,
                                   "max_tokens": max_tokens, "stream": False}
        if schema is not None:
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "output", "strict": False, "schema": schema}}
        t0 = time.monotonic()
        try:
            r = self._http.post(f"{self.url}/v1/chat/completions", json=payload, headers=self._headers(),
                                timeout=timeout or DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise _http(exc, self.url) from exc
        if r.status_code in (400, 404) and "model" in r.text.lower() and "not found" in r.text.lower():
            raise LLMError("model_missing", r.text[:200], model=model, backend=self.name)
        if r.status_code != 200:
            raise LLMError("backend_error", f"HTTP {r.status_code}: {r.text[:300]}", url=self.url)
        try:
            data = r.json()
            choice = data["choices"][0]
            text = strip_think(choice["message"].get("content") or "")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("backend_error", "unexpected response shape", url=self.url) from exc
        usage = data.get("usage") or {}
        return ChatResult(
            text, model, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)),
            int((time.monotonic() - t0) * 1000), choice.get("finish_reason") == "length",
        )


def build_client(backend: LLMBackend, session: requests.Session | None = None) -> LLMBackendClient:
    if backend.kind == "ollama":
        return OllamaClient(backend, session)
    return OpenAICompatClient(backend, session)  # llamacpp | openai_compat
