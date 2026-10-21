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


def orient_size(params: Mapping[str, Any], frame: tuple[int, int], multiple: int) -> dict[str, Any]:
    """The same pixel budget (``width * height``) in the shape of the project's frame, rounded to ``multiple``.

    The picture and video models list their sizes once; the frame (the delivery profile's width x height, chosen when
    the project starts) decides whether that is a vertical, a landscape or a square picture, so every picture of a
    project has one direction. Parameters without both a width and a height are returned unchanged.
    """
    out = dict(params)
    w, h = params.get("width"), params.get("height")
    fw, fh = frame
    if not (isinstance(w, int | float) and isinstance(h, int | float) and w > 0 and h > 0 and fw > 0 and fh > 0):
        return out
    area = float(w) * float(h)
    width = (area * fw / fh) ** 0.5

    def snap(x: float) -> int:
        return max(multiple, int(round(x / multiple)) * multiple)

    out["width"], out["height"] = snap(width), snap(width * fh / fw)
    return out


def size_multiple(kind: str) -> int:
    """Pictures are made on a 16 px grid, video on a 32 px grid."""
    return 32 if kind.startswith("video.") else 16


def resolve_params(
    settings: Settings,
    model_ref: str,
    tier: QualityTier | str,
    *,
    project_settings: Mapping[str, Any] | None = None,
    job_params: Mapping[str, Any] | None = None,
    adapter_defaults: Mapping[str, Any] | None = None,
    frame: tuple[int, int] | None = None,
) -> dict[str, Any]:
    spec = settings.model(model_ref)
    tier_key = QualityTier(tier).value
    base: dict[str, Any] = {}
    for layer in (adapter_defaults or {}, spec.params, spec.tiers.get(tier_key, {})):
        base = deep_merge(base, layer)
    if frame:                                   # the shape comes from the project; explicit sizes below still win
        base = orient_size(base, frame, size_multiple(spec.kind))
    out = base
    for layer in (((project_settings or {}).get("models") or {}).get(model_ref, {}), job_params or {}):
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
