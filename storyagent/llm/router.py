"""Role -> (backend, model) routing, structured output with validation + repair, and the call audit log."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from storyagent.config import Settings
from storyagent.content import digest_text, project_rating
from storyagent.db import Database

from .backends import ChatResult, LLMBackendClient, build_client
from .errors import LLMError
from .jsonfix import parse_object
from .schema import validate

log = logging.getLogger(__name__)

TRANSCRIPT_CAP = 200_000     # characters kept per prompt / answer in generation_logs (a safety cap, not a summary)
JSON_RULES = (
    "Respond with ONE valid JSON object and nothing else: no markdown, no code fences, no commentary. "
    "Keep every string on one line (use \\n for line breaks)."
)


class LLMRouter:
    """``router.json("writer", system, prompt, schema=...)`` -> validated dict.

    Backends are created lazily from ``settings.llm_backends``; tests inject ``clients``.
    """

    def __init__(
        self, settings: Settings, db: Database | None = None, *, clients: Mapping[str, LLMBackendClient] | None = None,
        on_call: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.settings, self.db, self.on_call = settings, db, on_call
        self.heavy_wait_sec = float(settings.runtime.get("llm_wait_for_heavy_sec", 900))
        self._clients: dict[str, LLMBackendClient] = dict(clients or {})
        self._models: dict[str, list[str]] = {}

    # -- plumbing -----------------------------------------------------------------------------
    def client(self, backend: str) -> LLMBackendClient:
        if backend not in self._clients:
            spec = self.settings.llm_backends.get(backend)
            if spec is None:
                raise LLMError("no_backend", f"unknown LLM backend {backend!r}", backend=backend)
            self._clients[backend] = build_client(spec)
        return self._clients[backend]

    def resolve(self, role: str, override_model: str = "") -> tuple[str, str, dict[str, Any]]:
        r = self.settings.llm_role(role)
        return r.backend, override_model or r.model, dict(r.params)

    def available(self, backend: str, refresh: bool = False) -> list[str]:
        if refresh or backend not in self._models:
            self._models[backend] = self.client(backend).list_models()
        return self._models[backend]

    def check_role(self, role: str) -> tuple[str, str]:
        """Fail early with a useful message when the model a role names is not installed on its backend."""
        backend, model, _ = self.resolve(role)
        have = self.available(backend, refresh=True)
        if have and model not in have and not any(m.split(":")[0] == model.split(":")[0] and ":" not in model
                                                  for m in have):
            raise LLMError("model_missing", f"{model} is not available on {backend}", model=model, backend=backend,
                           available=", ".join(have[:8]))
        return backend, model

    def _log(
        self, role: str, backend: str, project_id: int | None, prompt: str, res: ChatResult | None, *,
        system: str = "", label: str = "",
    ) -> None:
        rec = {
            "project_id": project_id, "agent": role, "model": res.model if res else "",
            "prompt": prompt[:TRANSCRIPT_CAP],
            "response": (res.text if res else "")[:TRANSCRIPT_CAP], "tokens_in": res.tokens_in if res else 0,
            "tokens_out": res.tokens_out if res else 0, "duration_ms": res.duration_ms if res else 0,
            "system": system[:TRANSCRIPT_CAP], "label": label,
        }
        if self._keeps_no_text(project_id):          # a restricted rating: a digest and the length, not the words
            for key in ("prompt", "response", "system"):
                rec[key] = digest_text(str(rec[key]))
        if self.on_call:
            self.on_call({**rec, "backend": backend})
        if self.db is not None:
            try:
                self.db.conn.execute(
                    "INSERT INTO generation_logs(project_id, agent, model, prompt, response, tokens_in, tokens_out,"
                    " duration_ms, system, label) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rec["project_id"], rec["agent"], rec["model"], rec["prompt"], rec["response"], rec["tokens_in"],
                     rec["tokens_out"], rec["duration_ms"], rec["system"], rec["label"]),
                )
            except Exception:  # the audit log must never break generation  # noqa: BLE001
                log.warning("could not write generation_logs", exc_info=True)

    def _keeps_no_text(self, project_id: int | None) -> bool:
        if self.db is None or project_id is None:
            return False
        try:
            return not self.settings.content.policy(project_rating(self.db, project_id)).log_prompts
        except Exception:  # the audit log must never break generation  # noqa: BLE001
            return False

    def _heavy_jobs_running(self) -> int:
        if self.db is None:
            return 0
        row = self.db.conn.execute(
            "SELECT COUNT(*) AS n FROM media_jobs WHERE status='running' AND heavy=1").fetchone()
        return int(row["n"])

    def _wait_for_heavy(self) -> None:
        """A model that fits next to a light job does not fit next to a heavy one: hold the call (or refuse it)."""
        if self._heavy_jobs_running() == 0:
            return
        deadline = time.monotonic() + max(self.heavy_wait_sec, 0.0)
        while self._heavy_jobs_running() > 0:
            if time.monotonic() >= deadline:
                raise LLMError("heavy_busy", "a heavy media job is running; the language model must wait for it")
            time.sleep(min(2.0, max(deadline - time.monotonic(), 0.05)))

    # -- calls --------------------------------------------------------------------------------
    def chat(
        self, role: str, prompt: str, *, system: str = "", project_id: int | None = None, model: str = "",
        schema: dict[str, Any] | None = None, label: str = "", **opts: Any,
    ) -> ChatResult:
        self._wait_for_heavy()
        backend, model_name, params = self.resolve(role, model)
        merged = {"temperature": 0.7, "max_tokens": 4096, **params, **opts}
        res = self.client(backend).chat(
            model_name, system, prompt, temperature=float(merged["temperature"]),
            max_tokens=int(merged["max_tokens"]), num_ctx=merged.get("num_ctx"), schema=schema,
            timeout=merged.get("timeout"),
        )
        self._log(role, backend, project_id, prompt, res, system=system, label=label)
        return res

    def json(
        self, role: str, prompt: str, *, system: str = "", schema: dict[str, Any] | None = None,
        project_id: int | None = None, attempts: int = 3, **opts: Any,
    ) -> dict[str, Any]:
        """A validated JSON object. Sloppy-but-fixable output is repaired locally; invalid output is sent back to the
        model once per attempt together with what was wrong. ``LLMError('bad_output')`` after ``attempts``."""
        sys_prompt = (system + "\n\n" if system else "") + JSON_RULES
        opts.setdefault("temperature", 0.3)
        current, last_problem, last_text = prompt, "", ""
        for attempt in range(attempts):
            res = self.chat(role, current, system=sys_prompt, project_id=project_id, schema=schema, **opts)
            last_text = res.text
            obj = parse_object(res.text)
            if obj is None:
                last_problem = "the output was not a JSON object" + (" (it was cut off by the token limit)"
                                                                      if res.truncated else "")
            elif schema is None:
                return obj
            else:
                obj, problems = validate(obj, schema)
                if not problems:
                    return obj
                last_problem = "; ".join(problems[:6])
                if res.truncated and attempt == attempts - 1:
                    break
            log.info("llm json attempt %d for %s failed: %s", attempt + 1, role, last_problem)
            current = (f"{prompt}\n\nYour previous answer was rejected: {last_problem}.\n"
                       f"Answer again with the complete, valid JSON object.")
        raise LLMError("bad_output", last_problem, role=role, detail=last_problem, raw=last_text[:400])

    def text(self, role: str, prompt: str, *, system: str = "", project_id: int | None = None, **opts: Any) -> str:
        return self.chat(role, prompt, system=system, project_id=project_id, **opts).text


def schema_hint(schema: dict[str, Any]) -> str:
    """Compact rendering of a schema for the prompt (models without constrained decoding need to see it)."""
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
