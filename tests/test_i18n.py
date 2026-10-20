from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from storyagent.i18n import LocaleError, check_catalogs, get_catalog


def write(d: Path, name: str, data: dict[str, str]) -> None:
    (d / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")


def test_repo_locales_are_consistent() -> None:
    assert check_catalogs() == []


def test_lookup_and_params(tmp_path: Path) -> None:
    write(tmp_path, "en", {"a": "Hello {name}", "b": "plain"})
    write(tmp_path, "zh-CN", {"a": "你好 {name}", "b": "朴素"})
    assert get_catalog("zh-CN", tmp_path).t("a", name="世界") == "你好 世界"
    assert get_catalog("en", tmp_path).t("b") == "plain"


def test_falls_back_to_english_then_key(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    write(tmp_path, "en", {"only_en": "English"})
    write(tmp_path, "zh-CN", {})
    cat = get_catalog("zh-CN", tmp_path)
    assert cat.t("only_en") == "English"
    with caplog.at_level(logging.WARNING):
        assert cat.t("nope") == "nope"
    assert "missing i18n key" in caplog.text


def test_missing_param_does_not_raise(tmp_path: Path) -> None:
    write(tmp_path, "en", {"a": "Hi {name}"})
    assert get_catalog("en", tmp_path).t("a") == "Hi {name}"


def test_check_detects_missing_key_and_placeholder_drift(tmp_path: Path) -> None:
    write(tmp_path, "en", {"a": "x {n}", "b": "y"})
    write(tmp_path, "zh-CN", {"a": "x {m}"})
    problems = check_catalogs(tmp_path)
    assert any("zh-CN: missing key b" in p for p in problems)
    assert any(p.startswith("a: placeholder mismatch") for p in problems)


def test_unknown_locale(tmp_path: Path) -> None:
    write(tmp_path, "en", {})
    with pytest.raises(LocaleError):
        get_catalog("fr", tmp_path)


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_every_enum_value_has_a_label(locale: str) -> None:
    from storyagent.domain import JobStatus, MediaKind, QualityTier, SubjectType
    from storyagent.domain.errors import ErrorCode, WaitReason

    cat = get_catalog(locale)
    enums = (
        ("status", JobStatus), ("media.kind", MediaKind), ("media.tier", QualityTier),
        ("spec.subject", SubjectType), ("error", ErrorCode), ("wait", WaitReason),
    )
    for prefix, enum in enums:
        for member in enum:
            key = f"{prefix}.{member.value}"
            assert cat.messages.get(key), f"{locale}: missing label {key}"
            if prefix == "error":
                assert cat.messages.get(f"{key}.remediation"), f"{locale}: missing remediation {key}"
