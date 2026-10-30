"""Form and genre rules for the writing agents: data in kinds.toml / genres.toml, phrased by agents.craft."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from storyagent.agents.craft import NONE, shot_bounds, writing_vars
from storyagent.agents.registry import load_specs
from storyagent.agents.runner import AgentRunner
from storyagent.config import ConfigError, load_settings
from storyagent.workflow.shotplan import estimate_speech_sec

WRITERS = ("screenwriter.season", "screenwriter.outline", "screenwriter.adapt", "screenwriter.shots",
           "reviewer.script", "workshop.story", "workshop.script")


@pytest.fixture()
def settings(tmp_path: Path):
    return load_settings({"STORY_AGENT_HOME": str(tmp_path)})


def test_genre_text_in_any_language_selects_the_notes(settings) -> None:
    g = settings.genres
    assert [x.name for x in g.match("都市情感")] == ["romance"]
    assert [x.name for x in g.match("Dark Comedy")] == ["comedy"]
    assert [x.name for x in g.match("古装 复仇")] == ["historical", "revenge"]
    assert g.match("") == [] and g.match("documentary") == [] and g.guidance_for("documentary") == ""
    assert len(g.match("romance mystery comedy horror")) == g.max_matches       # never a wall of notes


def test_every_kind_has_rules_that_fit_what_the_shot_plan_can_carry(settings) -> None:
    assert settings.kinds
    for name, kind in settings.kinds.items():
        assert kind.writing, name
        # the longest line, spoken at about 4.5 characters a second, has to fit the longest shot
        spoken = estimate_speech_sec("好" * kind.dialogue_chars) + 0.5
        assert spoken <= kind.shot_sec.max, (name, spoken, kind.shot_sec)


def test_forms_differ_and_the_house_style_is_not_everywhere(settings) -> None:
    runner = AgentRunner(settings, router=None, specs=load_specs(settings))     # type: ignore[arg-type]
    systems = {}
    for kind in ("drama_series", "feature_film", "advert"):
        v = writing_vars(settings, kind, "romance", vertical=False)
        v.update(language="English", premise="p", genre="romance", tone="t", acts="3", scenes_per_act="2",
                 characters="[]", plan="", title="T", act_title="A", scene="{}", shots_per_scene="3", previous="",
                 feedback="", current="")
        systems[kind], _ = runner.render("screenwriter.shots", v)
    assert len(set(systems.values())) == 3
    assert "cliffhanger" in systems["drama_series"] and "call to action" in systems["advert"]
    assert "three acts" in systems["feature_film"]
    # no single genre/form is written into the prompt text itself any more
    for name in ("screenwriter.season", "screenwriter.outline", "screenwriter.shots", "workshop.story"):
        spec = load_specs(settings)[name]
        assert "vertical" not in spec.system.lower() and "short drama" not in spec.system.lower(), name


def test_writer_prompts_render_completely_for_every_kind(settings) -> None:
    specs = load_specs(settings)
    runner = AgentRunner(settings, router=None, specs=specs)                    # type: ignore[arg-type]
    for kind in list(settings.kinds) + [None]:
        base = {k: "x" for name in WRITERS for k in specs[name].inputs}
        base.update(writing_vars(settings, kind, "horror", vertical=True))
        for name in WRITERS:
            system, prompt = runner.render(name, base)
            assert not re.search(r"\$[a-z_]+", system + prompt), (kind, name)


def test_a_workshop_text_without_a_kind_gets_no_form_rules(settings) -> None:
    v = writing_vars(settings, None, "drama")
    assert v["genre_guidance"] == NONE and v["frame_note"] == NONE
    assert v["form_guidance"] == settings.kinds["custom"].writing           # the neutral kind


def test_shot_length_bounds_follow_the_kind_and_unknown_kinds_fall_back(settings) -> None:
    assert shot_bounds(settings, "advert") == (1.0, 6.0)
    assert shot_bounds(settings, "feature_film") == (3.0, 15.0)
    assert shot_bounds(settings, "no_such_kind") == shot_bounds(settings, "drama_series") == (2.0, 8.0)
    assert shot_bounds(settings, "") == (2.0, 8.0)                          # a project without a kind: the default


def test_a_kind_from_an_older_config_without_the_new_keys_still_loads(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "kinds.toml").write_text(
        '[kinds.mini]\npipeline = "vertical_drama"\nseasons = false\nask = []\n'
        '[kinds.mini.episodes]\nmin = 1\ndefault = 1\nmax = 1\n'
        '[kinds.mini.episode_sec]\nmin = 5\ndefault = 20\nmax = 60\n', encoding="utf-8")
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path)})
    assert s.kinds["mini"].writing == "" and s.kinds["mini"].dialogue_chars == 25
    assert (s.kinds["mini"].shot_sec.min, s.kinds["mini"].shot_sec.max) == (2.0, 6.0)


def test_bad_values_are_refused_with_the_reason(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "genres.toml").write_text('[genres.x]\naliases = []\nguidance = "g"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="aliases"):
        load_settings({"STORY_AGENT_HOME": str(tmp_path)})
    (cfg / "genres.toml").write_text('[genres.x]\naliases = ["a"]\nguidance = ""\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="guidance"):
        load_settings({"STORY_AGENT_HOME": str(tmp_path)})
    (cfg / "genres.toml").unlink()
    (cfg / "kinds.toml").write_text('[kinds.drama_series]\ndialogue_chars = 2\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="dialogue_chars"):
        load_settings({"STORY_AGENT_HOME": str(tmp_path)})
