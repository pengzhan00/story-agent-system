from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.config import ConfigError, load_settings


def env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"STORY_AGENT_HOME": str(tmp_path / "home"), **extra}


def test_defaults(tmp_path: Path) -> None:
    s = load_settings(env(tmp_path))
    assert s.home == tmp_path / "home"
    assert s.db_path == tmp_path / "home" / "storyagent.db"
    assert s.service("comfy").url == "http://127.0.0.1:8188"
    assert s.service("acestep").health_url == "http://127.0.0.1:8001/v1/models"
    assert s.default_model("video.generate").ref == "ltx-2.3-22b-distilled"
    assert s.llm_unload_policy == "never"
    assert s.locale == "zh-CN"


def test_fastvideo_never_uses_nodus_port(tmp_path: Path) -> None:
    s = load_settings(env(tmp_path))
    assert s.service("fastvideo").url != s.service("nodus").url
    assert s.service("nodus").probe_only is True


def test_env_overrides(tmp_path: Path) -> None:
    s = load_settings(
        env(
            tmp_path,
            STORY_AGENT_COMFY_URL="http://10.0.0.5:9999/",
            STORY_AGENT_LOCALE="en",
            STORY_AGENT_LLM_UNLOAD_POLICY="ask",
        )
    )
    assert s.service("comfy").url == "http://10.0.0.5:9999"
    assert s.locale == "en"
    assert s.llm_unload_policy == "ask"


def test_user_override_file_merges_deeply(tmp_path: Path) -> None:
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "models.toml").write_text('[defaults]\n"audio.music" = "acestep-1.5-xl-sft"\n')
    (cfg / "services.toml").write_text('[services.comfy]\nurl = "http://127.0.0.1:1234"\n')
    s = load_settings(env(tmp_path))
    assert s.default_model("audio.music").ref == "acestep-1.5-xl-sft"  # swap model = config only
    assert s.service("comfy").url == "http://127.0.0.1:1234"
    assert s.service("comfy").health_path == "/system_stats"  # untouched sibling key survives
    assert s.default_model("audio.tts").ref == "cosyvoice2-0.5b"


def test_restricted_license_flows_from_registry(tmp_path: Path) -> None:
    m = load_settings(env(tmp_path)).model("minimax-h3")
    assert m.commercial_ok is False
    assert m.attribution == "MiniMax H3"
    assert m.heavy is True


def test_invalid_policy_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="llm_unload_policy"):
        load_settings(env(tmp_path, STORY_AGENT_LLM_UNLOAD_POLICY="yolo"))


def test_unknown_default_ref_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "models.toml").write_text('[defaults]\n"image.generate" = "nope"\n')
    with pytest.raises(ConfigError, match="unknown model ref"):
        load_settings(env(tmp_path))


def test_bad_toml_reports_file(tmp_path: Path) -> None:
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "delivery.toml").write_text("not = [valid")
    with pytest.raises(ConfigError, match="delivery.toml"):
        load_settings(env(tmp_path))


def test_tool_path_expands_user(tmp_path: Path) -> None:
    s = load_settings(env(tmp_path))
    p = s.tool_path("h3", "binary")
    assert p is not None and p.is_absolute() and "~" not in str(p)
    assert s.tool_path("ffmpeg", "binary", ) is not None  # configured default; falls back to PATH at use
    assert s.tool_path("h3", "nonexistent_key") is None  # empty/missing => auto-detect


def test_media_dir_layout(tmp_path: Path) -> None:
    s = load_settings(env(tmp_path))
    assert s.project_media_dir("demo", "video", 7) == s.home / "projects/demo/media/video/7"


def test_delivery_profile(tmp_path: Path) -> None:
    s = load_settings(env(tmp_path))
    p = s.delivery_profile()
    assert (p["width"], p["height"]) == (720, 1280)
    with pytest.raises(ConfigError):
        s.delivery_profile("nope")


def test_default_must_match_model_kind(tmp_path: Path) -> None:
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "models.toml").write_text('[defaults]\n"video.generate" = "minimax-h3"\n')  # that's video.av
    with pytest.raises(ConfigError, match="is a 'video.av' model"):
        load_settings(env(tmp_path))


def test_unknown_kind_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "models.toml").write_text('[models.x]\nkind = "video"\nprovider = "comfy"\n')
    with pytest.raises(ConfigError, match="unknown kind"):
        load_settings(env(tmp_path))


def test_one_family_many_runtimes(tmp_path: Path) -> None:
    s = load_settings(env(tmp_path))
    h3, fv = s.model("minimax-h3"), s.model("fastvideo-fasth3")
    assert h3.family == fv.family == "minimax-h3"
    assert h3.provider != fv.provider
    assert h3.commercial_ok is fv.commercial_ok is False
