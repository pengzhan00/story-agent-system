"""Agents = prompt + output schema + LLM role, as data. One runner executes any of them."""
from .registry import AgentSpec, AgentSpecError, load_specs
from .runner import AgentResult, AgentRunner

__all__ = ["AgentResult", "AgentRunner", "AgentSpec", "AgentSpecError", "load_specs"]
