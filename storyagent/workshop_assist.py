"""The AI assistant of a workshop module: it designs the prompt, lyrics, text and parameters of the module's form.

It reads three things: the standing requirements the author wrote for this module (``workshop_assist``, one text per
module), what the author asks this time, and the wiki passages that bear on the model (retrieved by the caller, who
also holds the visibility rules). It proposes; nothing is run or saved: the page shows the proposal and the author
applies it to the form. A proposal is checked against the form before it is returned: only text fields the module
really has are filled, only parameters the model's adapter understands are set, each value coerced to its type and
held to its range, and what had to be dropped is reported with the reason.
"""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from storyagent.agents.runner import AgentRunner
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.llm import LLMRouter
from storyagent.media.params import validate_params
from storyagent.util import now_iso
from storyagent.wiki.ask import Passage, excerpts_text
from storyagent.workshop import MODULES, Workshop, WorkshopError, kind_spec

FILLABLE = ("text", "longtext", "workkind")          # form fields the assistant may write (never uploads or switches)
MAX_FIELD_CHARS = 8000
MAX_REQUIREMENTS_CHARS = 4000
STYLES = {
    "natural_en": "natural English sentences: subject, setting, light, camera and mood",
    "cinematic_en": "cinematic English: shot size, camera move, light and colour, in plain sentences",
    "plain": "plain text",
    "music_tags": "comma-separated style tags in the prompt (genre, mood, instruments, tempo, voice); the lyrics go in "
                  "the lyrics field with section marks such as [verse] and [chorus]",
    "ltx_av": "one English paragraph for the picture, then `AUDIO:` for the sound design and `DIALOGUE in "
              "<language>:` for the spoken line, kept verbatim",
    "h3_structured": "blocks named Scene, Action, Camera, Look and Audio",
}


@dataclass(frozen=True)
class Dropped:
    name: str
    part: str            # input | param
    reason: str          # not_fillable | unknown_param | bad_value | unknown_kind
    detail: str = ""


def module_ids() -> list[str]:
    return [m.id for m in MODULES]


def _module(module: str) -> Any:
    for m in MODULES:
        if m.id == module:
            return m
    raise WorkshopError("workshop_unknown_module", module)


class Assist:
    def __init__(self, settings: Settings, db: Database, workshop: Workshop) -> None:
        self.settings, self.db, self.workshop = settings, db, workshop

    # -- the standing requirements ---------------------------------------------------------------------------------
    def requirements(self, module: str) -> dict[str, str]:
        _module(module)
        row = self.db.conn.execute("SELECT * FROM workshop_assist WHERE module=?", (module,)).fetchone()
        if row is None:
            return {"module": module, "requirements": "", "updated_at": "", "updated_by": ""}
        return {"module": module, "requirements": row["requirements"], "updated_at": row["updated_at"],
                "updated_by": row["updated_by"]}

    def save_requirements(self, module: str, text: str, by: str) -> dict[str, str]:
        _module(module)
        text = text.strip()
        if len(text) > MAX_REQUIREMENTS_CHARS:
            raise WorkshopError("assist_too_long", str(MAX_REQUIREMENTS_CHARS))
        with self.db.transaction() as conn:
            if text:
                conn.execute(
                    "INSERT INTO workshop_assist(module, requirements, updated_at, updated_by) VALUES (?,?,?,?) "
                    "ON CONFLICT(module) DO UPDATE SET requirements=excluded.requirements, "
                    "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                    (module, text, now_iso(), by))
            else:
                conn.execute("DELETE FROM workshop_assist WHERE module=?", (module,))
        return self.requirements(module)

    # -- what the model is told about the form ------------------------------------------------------------------------
    def _fields(self, kind: str, values: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        """The fields of this kind the assistant may write, and how they are listed to the model."""
        spec = kind_spec(kind)
        fillable = {f.name: f for f in spec.inputs if f.type in FILLABLE}
        lines = []
        for f in fillable.values():
            note = ", ".join(x for x in (f.type, "required" if f.required else "") if x)
            if f.type == "workkind":
                note += "; one of: " + ", ".join(self.settings.kinds)
            current = str(values.get(f.name) or "").strip()
            lines.append(f"- {f.name} ({note}): {current if current else '(empty)'}")
        return fillable, "\n".join(lines) or "(none)"

    def _params(self, kind: str, model_ref: str, tier: str,
                current: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any], str]:
        info = self.workshop.params_for(kind, model_ref, tier)
        schema: dict[str, dict[str, Any]] = {k: dict(v) for k, v in info["schema"].items()}
        effective = {**info["effective"], **dict(current)}
        lines = []
        for key, rule in schema.items():
            bits = [str(rule.get("type", "any"))]
            if "min" in rule or "max" in rule:
                bits.append(f"{rule.get('min', '')}..{rule.get('max', '')}")
            if "choices" in rule:
                bits.append("one of " + "|".join(str(c) for c in rule["choices"]))
            if rule.get("type") in ("list", "dict"):
                continue                                          # structured values are not set by the assistant
            shown = effective.get(key)
            lines.append(f"- {key}: {', '.join(bits)}; now {shown if shown is not None else '(model default)'}")
        return schema, effective, "\n".join(lines) or "(none)"

    @staticmethod
    def _coerce(value: str, rule: Mapping[str, Any]) -> Any:
        typ = rule.get("type")
        text = value.strip()
        if typ == "int":
            number = float(text)
            if number != int(number):
                raise ValueError("not a whole number")
            return int(number)
        if typ == "float":
            return float(text)
        if typ == "bool":
            if text.lower() in ("true", "yes", "on", "1"):
                return True
            if text.lower() in ("false", "no", "off", "0"):
                return False
            raise ValueError("not true or false")
        if typ in ("list", "dict"):
            raise ValueError("structured values are not set here")
        if "choices" in rule:                                      # choices of numbers arrive as text
            for choice in rule["choices"]:
                if str(choice) == text:
                    return choice
        return text

    # -- one proposal ------------------------------------------------------------------------------------------------
    def design(
        self, *, module: str, kind: str, model_ref: str, tier: str, values: Mapping[str, Any],
        current_params: Mapping[str, Any], request: str, language: str, found: Sequence[Passage], rating: str,
        router: LLMRouter,
    ) -> dict[str, Any]:
        mod = _module(module)
        if kind not in {k.kind.value for k in mod.kinds}:
            raise WorkshopError("workshop_unknown_kind", f"{module}: {kind}")
        model = self.settings.model(model_ref)
        if model.kind != kind:
            raise WorkshopError("workshop_wrong_model", f"{model_ref} is {model.kind}, not {kind}")
        fillable, fields_text = self._fields(kind, values)
        schema, effective, params_text = self._params(kind, model_ref, tier, current_params)
        started = time.monotonic()
        result = AgentRunner(self.settings, router).run("workshop.assist", {
            "language": language, "module": module, "kind": kind, "model": model_ref,
            "prompt_style": STYLES.get(model.prompt_profile, STYLES["plain"]),
            "fields": fields_text, "params": params_text,
            "requirements": self.requirements(module)["requirements"] or "(none)",
            "request": request.strip() or "(none)",
            "excerpts": excerpts_text(list(found)) if found else "(no wiki excerpt matched)",
        }, rating=rating)
        data = result.data
        dropped: list[Dropped] = []
        inputs: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in data.get("inputs", []) or []:
            name, text = str(item.get("name", "")).strip(), str(item.get("value", "")).strip()
            if not name or name in seen or not text:
                continue
            seen.add(name)
            field = fillable.get(name)
            if field is None:
                dropped.append(Dropped(name, "input", "not_fillable"))
            elif field.type == "workkind" and text not in self.settings.kinds:
                dropped.append(Dropped(name, "input", "unknown_kind", text))
            elif text != str(values.get(name) or "").strip():
                inputs.append({"name": name, "value": text[:MAX_FIELD_CHARS],
                               "current": str(values.get(name) or "")})
        params: list[dict[str, Any]] = []
        seen = set()
        for item in data.get("params", []) or []:
            name, raw = str(item.get("name", "")).strip(), str(item.get("value", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            rule = schema.get(name)
            if rule is None or rule.get("type") in ("list", "dict"):
                dropped.append(Dropped(name, "param", "unknown_param"))
                continue
            try:
                value = self._coerce(raw, rule)
            except ValueError as exc:
                dropped.append(Dropped(name, "param", "bad_value", f"{raw}: {exc}"))
                continue
            issues, _ = validate_params({name: value}, {name: rule}, strict=True)
            if issues:
                dropped.append(Dropped(name, "param", "bad_value", issues[0].message))
            elif value != effective.get(name):
                params.append({"name": name, "value": value, "current": effective.get(name)})
        cited = {int(n) for n in data.get("cited", []) if isinstance(n, int) and 1 <= n <= len(found)}
        return {
            "inputs": inputs, "params": params, "dropped": [d.__dict__ for d in dropped],
            "notes": str(data.get("notes", "")).strip(), "cited": sorted(cited),
            "seconds": round(time.monotonic() - started, 1),
        }
