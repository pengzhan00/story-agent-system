"""Layered parameter resolution and validation.

Pipelines are not uniform, so nothing is hard-wired. Precedence, low -> high:

    adapter built-ins  <  models.toml [params]  <  [tiers.<tier>]
                       <  projects.settings.models.<ref>  <  per-job params
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from storyagent.config import Settings, deep_merge
from storyagent.domain import QualityTier
from storyagent.domain.errors import ErrorCode

from .contracts import ValidationIssue

_TYPES: dict[str, tuple[type, ...]] = {
    "int": (int,),
    "float": (int, float),
    "str": (str,),
    "bool": (bool,),
    "list": (list, tuple),
    "dict": (dict,),
}


def resolve_params(
    settings: Settings,
    model_ref: str,
    tier: QualityTier | str,
    *,
    project_settings: Mapping[str, Any] | None = None,
    job_params: Mapping[str, Any] | None = None,
    adapter_defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    spec = settings.model(model_ref)
    tier_key = QualityTier(tier).value
    layers = [
        adapter_defaults or {},
        spec.params,
        spec.tiers.get(tier_key, {}),
        ((project_settings or {}).get("models") or {}).get(model_ref, {}),
        job_params or {},
    ]
    out: dict[str, Any] = {}
    for layer in layers:
        out = deep_merge(out, layer)
    return out


def validate_params(
    params: Mapping[str, Any],
    schema: Mapping[str, Mapping[str, Any]],
    *,
    strict: bool,
) -> tuple[list[ValidationIssue], list[str]]:
    """Return (blocking issues, warnings). Unknown keys warn, or block when ``strict``."""
    issues: list[ValidationIssue] = []
    warnings: list[str] = []
    for key, value in params.items():
        rule = schema.get(key)
        if rule is None:
            if schema:  # an adapter with no schema accepts anything silently
                msg = f"unknown parameter {key!r}"
                if strict:
                    issues.append(ValidationIssue(ErrorCode.INVALID_PARAMS, msg, {"detail": msg}))
                else:
                    warnings.append(msg)
            continue
        problem = _check(key, value, rule)
        if problem:
            issues.append(ValidationIssue(ErrorCode.INVALID_PARAMS, problem, {"detail": problem}))
    return issues, warnings


def _check(key: str, value: Any, rule: Mapping[str, Any]) -> str:
    typ = rule.get("type")
    if typ:
        allowed = _TYPES[typ]
        if isinstance(value, bool) and typ != "bool":
            return f"{key}: expected {typ}, got bool"
        if not isinstance(value, allowed):
            return f"{key}: expected {typ}, got {type(value).__name__}"
    if "min" in rule and value < rule["min"]:
        return f"{key}: {value} is below the minimum {rule['min']}"
    if "max" in rule and value > rule["max"]:
        return f"{key}: {value} is above the maximum {rule['max']}"
    if "choices" in rule and value not in rule["choices"]:
        return f"{key}: {value!r} is not one of {list(rule['choices'])}"
    return ""
