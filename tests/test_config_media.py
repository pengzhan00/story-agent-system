from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.config import ConfigError, load_settings


def home_with(tmp_path: Path, **files: str) -> dict[str, str]:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    for name, text in files.items():
        (home / "config" / f"{name}.toml").write_text(text)
    return {"STORY_AGENT_HOME": str(home)}


# ---- compliance -----------------------------------------------------------------------------------
def test_compliance_defaults_on(tmp_path: Path) -> None:
    c = load_settings(home_with(tmp_path)).compliance
    assert c.enabled and c.implicit_metadata and c.require_voice_consent and c.explicit_label == "off"


@pytest.mark.parametrize(("raw", "expected"), [("off", False), ("0", False), ("false", False),
                                                ("on", True), ("1", True), ("TRUE", True)])
def test_compliance_env_switch(tmp_path: Path, raw: str, expected: bool) -> None:
    s = load_settings({**home_with(tmp_path), "STORY_AGENT_COMPLIANCE": raw})
    assert s.compliance.enabled is expected


def test_compliance_env_rejects_garbage(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="STORY_AGENT_COMPLIANCE"):
        load_settings({**home_with(tmp_path), "STORY_AGENT_COMPLIANCE": "maybe"})


def test_compliance_file_and_project_override_layers(tmp_path: Path) -> None:
    s = load_settings(home_with(tmp_path, compliance='[compliance]\nexplicit_label = "overlay"\n'))
    assert s.compliance.explicit_label == "overlay" and s.compliance.enabled
    off = s.compliance_for({"compliance": {"enabled": False}})
    assert not off.enabled and off.explicit_label == "overlay"  # untouched keys inherit
    assert s.compliance_for(None) is s.compliance and s.compliance_for({}) is s.compliance
    # a project can opt IN when the global switch is off
    g_off = load_settings({**home_with(tmp_path / "b"), "STORY_AGENT_COMPLIANCE": "off"})
    assert g_off.compliance_for({"compliance": {"enabled": True}}).enabled


def test_compliance_validation(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="explicit_label"):
        load_settings(home_with(tmp_path, compliance='[compliance]\nexplicit_label = "banner"\n'))
    s = load_settings(home_with(tmp_path / "b"))
    with pytest.raises(ConfigError, match="unknown project compliance keys"):
        s.compliance_for({"compliance": {"nope": 1}})


# ---- models ---------------------------------------------------------------------------------------
def test_model_params_tiers_and_template_are_loaded(tmp_path: Path) -> None:
    s = load_settings(home_with(tmp_path))
    ltx = s.model("ltx-2.5-video")
    assert ltx.tiers and ltx.params["frame_rate"] == 24 and ltx.timeout_sec > 0 and ltx.prompt_profile
    trainer = s.default_model("train.lora")
    assert trainer.heavy and trainer.provider == "lora_train"
    tpl = trainer.extra["train_template"]
    assert tpl["training_loop"]["num_epochs"] == 60 and len(tpl["lora_layers"]["targets"]) == 3
    assert tpl["lora_layers"]["targets"][0]["blocks"] == {"start": 0, "end": 5}


def test_unknown_tier_rejected(tmp_path: Path) -> None:
    bad = '[models.x]\nkind="video.generate"\nprovider="fake"\n[models.x.tiers.ultra]\nwidth=1\n'
    with pytest.raises(ConfigError, match="unknown tiers"):
        load_settings(home_with(tmp_path, models=bad))


def test_user_can_add_a_model_with_custom_params(tmp_path: Path) -> None:
    mine = ('[models.mine]\nkind="audio.music"\nprovider="acestep"\n[models.mine.params]\nduration=45\n'
            '[defaults]\n"audio.music"="mine"\n')
    s = load_settings(home_with(tmp_path, models=mine))
    assert s.default_model("audio.music").params == {"duration": 45}


# ---- recipes --------------------------------------------------------------------------------------
def test_default_recipes(tmp_path: Path) -> None:
    s = load_settings(home_with(tmp_path))
    assert {"quick_draft", "standard_shot", "av_shot", "avatar_shot"} <= set(s.recipes)
    steps = s.recipe("standard_shot").steps
    assert [x.kind for x in steps] == ["image.generate", "video.generate", "audio.tts"]
    assert steps[0].slot == "keyframe" and steps[2].when == "shot.has_dialogue"
    assert s.recipe("quick_draft").steps[0].tier == "draft"
    with pytest.raises(ConfigError):
        s.recipe("nope")


@pytest.mark.parametrize(("toml", "msg"), [
    ('[recipes.r]\n[[recipes.r.steps]]\nkind = "bogus"\n', "unknown kind"),
    ('[recipes.r]\n[[recipes.r.steps]]\nkind = "video.generate"\nmodel = "nope"\n', "unknown model"),
    ('[recipes.r]\n[[recipes.r.steps]]\nkind = "video.generate"\nmodel = "cosyvoice2-0.5b"\n', "not 'video.generate'"),
    ('[recipes.r]\n[[recipes.r.steps]]\nkind = "video.generate"\ntier = "ultra"\n', "unknown tier"),
    ('[recipes.r]\nlabel_key = "x"\n', "no steps"),
    ('[recipes.r]\n[[recipes.r.steps]]\nkind = "video.enhance"\n', "no default model"),
])
def test_recipe_validation(tmp_path: Path, toml: str, msg: str) -> None:
    with pytest.raises(ConfigError, match=msg):
        load_settings(home_with(tmp_path, recipes=toml))


def test_user_recipe_with_explicit_model_and_params(tmp_path: Path) -> None:
    toml = ('[recipes.mine]\n[[recipes.mine.steps]]\nkind = "video.av"\nmodel = "minimax-h3"\n'
            'tier = "draft"\nparams = { num_frames = 30 }\n')
    s = load_settings(home_with(tmp_path, recipes=toml))
    st = s.recipe("mine").steps[0]
    assert st.model == "minimax-h3" and st.params == {"num_frames": 30}


# ---- llm ------------------------------------------------------------------------------------------
def test_llm_roles_route_to_backends_with_default_fallback(tmp_path: Path) -> None:
    s = load_settings(home_with(tmp_path))
    assert s.llm_role("writer").model == "gemma4:31b" and s.llm_role("writer").backend == "ollama"
    assert s.llm_role("scene").model == s.llm_role("default").model == "gemma4:31b"  # unrouted role -> default
    assert s.llm_backends["ollama"].url == s.service("ollama").url  # one source of truth for the URL


def test_llm_backend_url_follows_service_override(tmp_path: Path) -> None:
    s = load_settings({**home_with(tmp_path), "STORY_AGENT_OLLAMA_URL": "http://10.1.1.1:1"})
    assert s.llm_backends["ollama"].url == "http://10.1.1.1:1"


def test_user_can_route_a_role_to_llamacpp(tmp_path: Path) -> None:
    llm = '[llm.roles.writer]\nbackend = "llamacpp"\nmodel = "qwen3-32b-q4"\nparams = { temperature = 0.7 }\n'
    svc = "[services.llamacpp]\nurl = \"http://127.0.0.1:8081\"\nenabled = true\n"
    s = load_settings(home_with(tmp_path, llm=llm, services=svc))
    r = s.llm_role("writer")
    assert (r.backend, r.model, r.params) == ("llamacpp", "qwen3-32b-q4", {"temperature": 0.7})
    assert s.llm_backends["llamacpp"].url == "http://127.0.0.1:8081"


@pytest.mark.parametrize(("toml", "msg"), [
    ('[llm.roles.x]\nbackend = "ghost"\nmodel = "m"\n', "unknown backend"),
    ('[llm.roles.x]\nbackend = "ollama"\n', "model is required"),
    ('[llm.backends.b]\nkind = "weird"\nurl = "http://x"\n', "kind must be one of"),
    ('[llm.backends.b]\nkind = "openai_compat"\n', "needs `service` or `url`"),
    ('[llm.backends.b]\nkind = "ollama"\nservice = "ghost"\n', "unknown service"),
])
def test_llm_validation(tmp_path: Path, toml: str, msg: str) -> None:
    with pytest.raises(ConfigError, match=msg):
        load_settings(home_with(tmp_path, llm=toml))


def test_openai_compat_backend_with_explicit_url(tmp_path: Path) -> None:
    llm = '[llm.backends.mlx]\nkind = "openai_compat"\nurl = "http://127.0.0.1:8090/"\nmodel_hint = "x"\n'
    s = load_settings(home_with(tmp_path, llm=llm))
    assert s.llm_backends["mlx"].url == "http://127.0.0.1:8090" and s.llm_backends["mlx"].options == {"model_hint": "x"}


# ---- runtime knobs --------------------------------------------------------------------------------
def test_runtime_knobs_have_defaults_and_can_be_overridden(tmp_path: Path) -> None:
    s = load_settings(home_with(tmp_path))
    assert s.strict_params is False and s.runtime["auto_adopt"] is True and s.runtime["lease_seconds"] == 60
    s2 = load_settings(home_with(tmp_path / "b", services="[runtime]\nstrict_params = true\nmax_light_jobs = 4\n"))
    assert s2.strict_params and s2.runtime["max_light_jobs"] == 4 and s2.runtime["lease_seconds"] == 60


# ---- api ------------------------------------------------------------------------------------------
def test_api_defaults_and_overrides(tmp_path: Path) -> None:
    api = load_settings(home_with(tmp_path)).api
    assert api["port"] == 8765 and api["host"] and api["cors_origins"] == ["http://localhost:5173"]
    s = load_settings(home_with(tmp_path / "b", services='[api]\nport = 9000\ncors_origins = []\n'))
    assert s.api["port"] == 9000 and s.api["cors_origins"] == [] and s.api["host"] == api["host"]


@pytest.mark.parametrize("toml", ['[api]\nport = 0\n', '[api]\nport = "x"\n', '[api]\nhost = ""\n',
                                  '[api]\ncors_origins = "http://a"\n'])
def test_api_validation(tmp_path: Path, toml: str) -> None:
    with pytest.raises(ConfigError, match=r"\[api\]"):
        load_settings(home_with(tmp_path, services=toml))
