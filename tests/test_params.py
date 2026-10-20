from __future__ import annotations

import pytest

from storyagent.config import ConfigError, Settings
from storyagent.domain import QualityTier
from storyagent.domain.errors import ErrorCode
from storyagent.media.params import resolve_params, validate_params


def test_layer_precedence(fsettings: Settings) -> None:
    base = resolve_params(fsettings, "fake-video", QualityTier.PREVIEW)
    assert base == {"width": 100, "fps": 8}  # model params; preview has no override
    assert resolve_params(fsettings, "fake-video", "draft")["width"] == 50  # tier beats model
    assert resolve_params(fsettings, "fake-video", "final")["width"] == 200
    proj = {"models": {"fake-video": {"width": 300, "extra": 1}}}
    r = resolve_params(fsettings, "fake-video", "draft", project_settings=proj)
    assert r["width"] == 300 and r["extra"] == 1  # project beats tier
    r = resolve_params(fsettings, "fake-video", "draft", project_settings=proj, job_params={"width": 7})
    assert r["width"] == 7  # job beats project
    r = resolve_params(fsettings, "fake-video", "final", adapter_defaults={"width": 1, "steps": 9})
    assert r["width"] == 200 and r["steps"] == 9  # adapter built-ins are the weakest layer


def test_nested_params_merge_deeply(fsettings: Settings) -> None:
    r = resolve_params(
        fsettings, "fake-video", "final",
        project_settings={"models": {"fake-video": {"lora": {"a": 1}}}}, job_params={"lora": {"b": 2}},
    )
    assert r["lora"] == {"a": 1, "b": 2}


def test_unknown_model(fsettings: Settings) -> None:
    with pytest.raises(ConfigError):
        resolve_params(fsettings, "nope", "final")


SCHEMA = {
    "width": {"type": "int", "min": 64, "max": 4096},
    "mode": {"type": "str", "choices": ["a", "b"]},
    "cfg": {"type": "float"},
    "flag": {"type": "bool"},
}


def test_validation_rules() -> None:
    issues, warns = validate_params({"width": 128, "mode": "a", "cfg": 3, "flag": True}, SCHEMA, strict=False)
    assert not issues and not warns  # int is acceptable for float
    issues, _ = validate_params({"width": 8}, SCHEMA, strict=False)
    assert "below the minimum" in issues[0].message and issues[0].code is ErrorCode.INVALID_PARAMS
    assert validate_params({"width": 9999}, SCHEMA, strict=False)[0]
    assert validate_params({"mode": "z"}, SCHEMA, strict=False)[0]
    assert validate_params({"width": "128"}, SCHEMA, strict=False)[0]
    assert validate_params({"width": True}, SCHEMA, strict=False)[0]  # bool is not an int here
    assert validate_params({"flag": 1}, SCHEMA, strict=False)[0]


def test_unknown_keys_warn_or_block() -> None:
    issues, warns = validate_params({"mystery": 1}, SCHEMA, strict=False)
    assert not issues and warns == ["unknown parameter 'mystery'"]
    issues, warns = validate_params({"mystery": 1}, SCHEMA, strict=True)
    assert issues and not warns


def test_adapter_without_schema_accepts_anything() -> None:
    assert validate_params({"anything": 1}, {}, strict=True) == ([], [])
