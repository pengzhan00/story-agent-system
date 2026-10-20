from __future__ import annotations

import json
import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from storyagent.config import Settings
from storyagent.llm import LLMError, LLMRouter

from .registry import AgentSpec, AgentSpecError, load_specs

# Human-readable names for the content-language codes (prompts are written in English).
LANGUAGE_NAMES = {"zh-CN": "Simplified Chinese", "zh-TW": "Traditional Chinese", "en": "English", "ja": "Japanese",
                  "ko": "Korean", "fr": "French", "de": "German", "es": "Spanish"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=1)


@dataclass(frozen=True)
class AgentResult:
    data: dict[str, Any]
    agent: str
    role: str
    prompt: str


class AgentRunner:
    def __init__(self, settings: Settings, router: LLMRouter, specs: Mapping[str, AgentSpec] | None = None) -> None:
        self.settings, self.router = settings, router
        self._fixed = dict(specs) if specs is not None else None

    @property
    def specs(self) -> dict[str, AgentSpec]:
        """Injected specs (tests), otherwise re-read from disk every time: an edited prompt applies to the very next
        call, also in a long-running worker."""
        return self._fixed if self._fixed is not None else load_specs(self.settings)

    def spec(self, name: str) -> AgentSpec:
        try:
            return self.specs[name]
        except KeyError:
            raise AgentSpecError(f"unknown agent {name!r}") from None

    def render(self, name: str, variables: Mapping[str, Any]) -> tuple[str, str]:
        spec = self.spec(name)
        missing = [k for k in spec.inputs if k not in variables]
        if missing:
            raise AgentSpecError(f"agent {name!r} needs {missing}")
        values = {k: render_value(variables[k]) for k in spec.inputs}
        return (string.Template(spec.system).safe_substitute(values),
                string.Template(spec.prompt).safe_substitute(values))

    def run(self, name: str, variables: Mapping[str, Any], *, project_id: int | None = None) -> AgentResult:
        spec = self.spec(name)
        system, prompt = self.render(name, variables)
        try:
            data = self.router.json(
                spec.role, prompt, system=system, schema=spec.schema, project_id=project_id, attempts=spec.attempts,
                temperature=spec.temperature, max_tokens=spec.max_tokens, label=name,
                **({"model": spec.model} if spec.model else {}),
            )
        except LLMError as exc:
            exc.params.setdefault("agent", name)
            raise
        return AgentResult(data, name, spec.role, prompt)
