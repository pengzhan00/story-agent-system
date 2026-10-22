"""Shot grammar configuration: the framing scale and the rules of the storyboard check (``grammar.toml``)."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SEVERITIES = ("error", "warning", "info")
RULES = ("establishing", "jump_size", "size_leap", "coverage", "dialogue_close", "speaker_in_frame", "axis_cross",
         "single_action", "monotone_pace")
_SEP = re.compile(r"[\s_\-]+")


def _norm(text: str) -> str:
    return _SEP.sub("-", text.strip().lower()).strip("-")


@dataclass(frozen=True)
class GrammarRule:
    id: str
    severity: str
    enabled: bool = True
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Grammar:
    enabled: bool = True
    sizes: tuple[str, ...] = ()
    aliases: Mapping[str, str] = field(default_factory=dict)       # normalised alias or name -> size name
    rules: Mapping[str, GrammarRule] = field(default_factory=dict)

    def rank(self, text: str) -> int | None:
        """Position of a framing text on the scale (0 = widest); None when it is not recognised."""
        size = self.aliases.get(_norm(str(text)))
        return self.sizes.index(size) if size in self.sizes else None

    def rule(self, rule_id: str) -> GrammarRule | None:
        r = self.rules.get(rule_id)
        return r if self.enabled and r is not None and r.enabled else None


def build_grammar(raw: Mapping[str, Any], error: type[Exception]) -> Grammar:
    cfg = raw.get("grammar", {})
    sizes = tuple(str(s) for s in cfg.get("sizes", {}).get("order", ()))
    if not sizes or len(set(sizes)) != len(sizes):
        raise error("grammar.sizes.order must list each framing size once")
    aliases: dict[str, str] = {_norm(s): s for s in sizes}
    for size, names in cfg.get("sizes", {}).get("aliases", {}).items():
        if size not in sizes:
            raise error(f"grammar.sizes.aliases: unknown size {size!r}")
        for name in names:
            aliases[_norm(str(name))] = size
    rules: dict[str, GrammarRule] = {}
    for rule_id, spec in cfg.get("rules", {}).items():
        if rule_id not in RULES:
            raise error(f"grammar.rules: unknown rule {rule_id!r}; known: {list(RULES)}")
        severity = str(spec.get("severity", "warning"))
        if severity not in SEVERITIES:
            raise error(f"grammar.rules.{rule_id}: severity must be one of {list(SEVERITIES)}")
        params = {k: v for k, v in spec.items() if k not in ("severity", "enabled")}
        for key in ("max_size", "fix_size", "min_size"):
            if key in params and params[key] not in sizes:
                raise error(f"grammar.rules.{rule_id}.{key}: unknown size {params[key]!r}")
        rules[rule_id] = GrammarRule(rule_id, severity, bool(spec.get("enabled", True)), params)
    return Grammar(bool(cfg.get("enabled", True)), sizes, aliases, rules)
