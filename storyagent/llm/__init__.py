"""LLM access: backends (Ollama, OpenAI-compatible incl. llama.cpp), per-role routing, robust JSON output."""
from . import overrides
from .backends import ChatResult, LLMBackendClient, OllamaClient, OpenAICompatClient, build_client
from .errors import LLMError
from .router import LLMRouter

__all__ = [
    "ChatResult", "LLMBackendClient", "LLMError", "LLMRouter", "OllamaClient", "OpenAICompatClient", "build_client",
    "overrides",
]
