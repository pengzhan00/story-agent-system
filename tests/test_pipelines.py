"""Pipeline plans and kinds of work: loading and validation, one plan per project, and what follows from the plan."""
from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.brief import estimate, resolve_brief
from storyagent.config import ConfigError, Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.domain import MediaKind, QualityTier
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger, NewAsset
from storyagent.media.specs import SpecStore
from storyagent.projects import ProjectStateError, ProjectStore
from storyagent.workflow.media import up_to_date

CORE = """
"image.generate" = "flux2-klein-base-4b"
"video.generate" = "ltx-2.5-video"
"audio.tts" = "cosyvoice3-0.5b"
"audio.music" = "acestep-1.5-turbo"
"compose.assemble" = "ffmpeg-assemble"
"""
ALT_MODEL = '[models."alt-img"]\nkind = "image.generate"\nprovider = "fake"\ntimeout_sec = 30\n'


def home_with(tmp_path: Path, **files: str) -> dict[str, str]:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (home / "config" / f"{name}.toml").write_text(text)
    return {"STORY_AGENT_HOME": str(home)}


def plan_toml(name: str = "mine", models: str = CORE, extra: str = "", profile: str = "series_vertical_720") -> str:
    return (f'[pipelines.{name}]\ndelivery_profile = "{profile}"\ntier = "draft"\n{extra}\n'
            f"[pipelines.{name}.models]\n{models}")


def test_the_shipped_plans_and_kinds_load_and_point_at_each_other(tmp_path: Path) -> None:
    s = load_settings(home_with(tmp_path))
    assert {"vertical_drama", "landscape_drama", "tv_series", "advert", "feature_film"} <= set(s.pipelines)
    assert {"drama_series", "drama_single", "tv_series", "advert", "feature_film", "custom"} <= set(s.kinds)
    assert s.default_pipeline == "vertical_drama" and s.default_kind == "drama_series"
    for kind in s.kinds.values():
        assert kind.pipeline in s.pipelines
    for plan in s.pipelines.values():
        assert plan.delivery_profile in s.delivery["profiles"]
        eff = s.for_pipeline(plan.name)
        assert eff.default_models["video.generate"] == plan.models["video.generate"]


def test_a_plan_changes_models_frame_tier_workflow_edit_and_mix_for_its_projects_only(tmp_path: Path) -> None:
    s = load_settings(home_with(tmp_path))
    vertical, advert, tv = s.for_pipeline("vertical_drama"), s.for_pipeline("advert"), s.for_pipeline("tv_series")
    assert vertical.delivery_profile(None)["height"] > vertical.delivery_profile(None)["width"]
    assert advert.delivery_profile(None)["width"] == 1920 and advert.workflow["tier"] == "final"
    assert advert.workflow["video_share"] == 1.0 and advert.delivery["edit"]["title_card"] is False
    assert tv.delivery["mix"]["loudnorm_i"] == -23.0 and tv.delivery["edit"]["recap_card"] is True
    assert s.delivery["mix"]["loudnorm_i"] == -16.0 and s.workflow["video_share"] == 0.2     # global: untouched


def test_model_params_of_a_plan_merge_into_the_model_and_its_tiers(tmp_path: Path) -> None:
    extra = ('[pipelines.mine.model_params."ltx-2.5-video"]\nstage1_steps = 99\n'
             '[pipelines.mine.model_params."ltx-2.5-video".tiers.preview]\nstage2_steps = 7\n')
    s = load_settings(home_with(tmp_path, pipelines=plan_toml(extra=extra))).for_pipeline("mine")
    ltx = s.model("ltx-2.5-video")
    assert ltx.params["stage1_steps"] == 99 and ltx.params["frames"] == 121        # merged, not replaced
    assert ltx.tiers["preview"]["stage2_steps"] == 7 and ltx.tiers["preview"]["stage1_steps"] == 12


@pytest.mark.parametrize(("toml", "message"), [
    (plan_toml(models=CORE.replace('"audio.music" = "acestep-1.5-turbo"\n', "")), "models missing"),
    (plan_toml(models=CORE + '"audio.sfx" = "no-such-model"\n'), "unknown model"),
    (plan_toml(models=CORE.replace("ltx-2.5-video", "cosyvoice3-0.5b")), "is a 'audio.tts' model"),
    (plan_toml(models=CORE + '"bogus.kind" = "ltx-2.5-video"\n'), "unknown media kind"),
    (plan_toml(profile="no_profile"), "unknown delivery profile"),
    (plan_toml(extra='requires = ["no.such.service"]'), "requires lists unknown"),
    (plan_toml().replace('tier = "draft"', 'tier = "ultra"'), "unknown tier"),
    (plan_toml(extra='[pipelines.mine.model_params."no-model"]\nx = 1\n'), "model_params for unknown model"),
])
def test_a_plan_that_does_not_hold_together_is_refused_with_the_reason(tmp_path: Path, toml: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(home_with(tmp_path, pipelines=toml))


def test_kinds_are_checked_and_can_be_added(tmp_path: Path) -> None:
    ok = ('[kinds.documentary]\npipeline = "advert"\nseasons = false\nask = ["episode_sec"]\n'
          '[kinds.documentary.episodes]\nmin = 1\ndefault = 1\nmax = 1\n'
          '[kinds.documentary.episode_sec]\nmin = 60\ndefault = 600\nmax = 3600\n')
    s = load_settings(home_with(tmp_path, kinds=ok))
    assert s.kind("documentary").pipeline == "advert" and s.kind("documentary").episode_sec.default == 600
    for bad, message in ((ok.replace('"advert"', '"nope"'), "unknown pipeline"),
                         (ok.replace('["episode_sec"]', '["colour"]'), "unknown questions"),
                         (ok.replace("default = 600", "default = 5"), "min <= default <= max")):
        with pytest.raises(ConfigError, match=message):
            load_settings(home_with(tmp_path / "b", kinds=bad))


def test_the_brief_takes_defaults_from_the_kind_and_holds_to_its_ranges(settings: Settings) -> None:
    b = resolve_brief(settings, "drama_series", {"seasons": 2, "episodes_per_season": 10, "episode_sec": 90})
    assert (b["episodes_total"], b["total_sec"]) == (20, 1800)
    d = resolve_brief(settings, "drama_series")
    assert d["seasons"] == 1 and d["episodes_per_season"] == 12 and d["episode_sec"] == 120
    ad = resolve_brief(settings, "advert")
    assert ad["episodes_total"] == 1 and ad["episode_sec"] == 30
    for kind, answers in (("advert", {"seasons": 2}), ("drama_series", {"episodes_per_season": 1}),
                          ("advert", {"episode_sec": 9999}), ("drama_series", {"seasons": 0}),
                          ("drama_series", {"audience": "x" * 300})):
        with pytest.raises(ConfigError):
            resolve_brief(settings, kind, answers)


def test_creating_a_project_sets_plan_frame_brief_target_and_season_rows(settings: Settings, db: Database) -> None:
    from storyagent.story import StoryStore

    projects = ProjectStore(db, settings)
    p = projects.create("S", kind="drama_series", brief={"seasons": 2, "episodes_per_season": 8, "episode_sec": 90})
    assert p.pipeline == "vertical_drama" and p.kind == "drama_series" and p.aspect_ratio == "9:16"
    assert p.delivery_profile == "series_vertical_720" and p.settings["target_sec"] == 90
    assert p.brief["episodes_total"] == 16
    seasons = StoryStore(db).list_seasons(p.id)
    assert [(x["number"], x["planned_episodes"]) for x in seasons] == [(1, 8), (2, 8)]
    ad = projects.create("A", kind="advert")
    assert ad.pipeline == "advert" and ad.aspect_ratio == "16:9" and ad.settings["target_sec"] == 30
    assert projects.create("P", pipeline="tv_series").delivery_profile == "tv_landscape_1080"
    assert projects.create("L", delivery_profile="movie_landscape_720").pipeline == "landscape_drama"
    assert projects.create("D").pipeline == "vertical_drama"                           # nothing asked: the default plan
    with pytest.raises(ConfigError):
        projects.create("X", pipeline="advert", delivery_profile="series_vertical_720")   # the plan fixes the direction
    with pytest.raises(ConfigError):
        projects.create("Y", kind="no_such_kind")


def test_changing_the_brief_only_adds_seasons_and_episodes(settings: Settings, db: Database) -> None:
    from storyagent.story import StoryStore

    projects = ProjectStore(db, settings)
    p = projects.create("S", kind="drama_series", brief={"seasons": 1, "episodes_per_season": 6})
    p = projects.set_brief(p.id, {"seasons": 2, "episodes_per_season": 10, "episode_sec": 60})
    assert p.brief["episodes_total"] == 20 and p.settings["target_sec"] == 60
    assert [x["planned_episodes"] for x in StoryStore(db).list_seasons(p.id)] == [10, 10]
    p = projects.set_brief(p.id, {"seasons": 1, "episodes_per_season": 5})
    assert [x["planned_episodes"] for x in StoryStore(db).list_seasons(p.id)] == [10, 10]   # nothing is taken away


def test_changing_the_plan_follows_the_direction_rule(settings: Settings, db: Database) -> None:
    projects = ProjectStore(db, settings)
    p = projects.create("S", pipeline="landscape_drama")
    assert projects.update(p.id, pipeline="tv_series").delivery_profile == "tv_landscape_1080"
    assert projects.update(p.id, pipeline="vertical_drama").aspect_ratio == "9:16"       # nothing made yet: free
    db.conn.execute("INSERT INTO media_assets(id, project_id, kind, slot, path, sha256, bytes, version, is_current)"
                    " VALUES ('a1', ?, 'image.generate', 'main', 'x.png', 'h', 5, 1, 1)", (p.id,))
    with pytest.raises(ProjectStateError):
        projects.update(p.id, pipeline="advert")                     # a picture exists: no turning the frame around
    assert projects.get(p.id).pipeline == "vertical_drama"
    assert projects.update(p.id, pipeline="vertical_drama").pipeline == "vertical_drama"


# -- one plan per project: every job takes its models and parameters from it ----------------------------------------
def _two_plan_settings(tmp_path: Path) -> Settings:
    swapped = CORE.replace('"image.generate" = "flux2-klein-base-4b"', '"image.generate" = "alt-img"')
    alt = plan_toml("alt", models=swapped,
                    extra='[pipelines.alt.model_params."ltx-2.5-video"]\nstage1_steps = 99\n')
    return load_settings(home_with(tmp_path, models=ALT_MODEL, pipelines=alt))


def test_jobs_take_the_model_and_parameters_of_the_plan_of_their_project(tmp_path: Path) -> None:
    s = _two_plan_settings(tmp_path)
    db = Database(s.db_path)
    migrate(db)
    projects, ledger = ProjectStore(db, s), Ledger(db, s)
    a, b = projects.create("A"), projects.create("B", pipeline="alt")

    def job(project_id: int | None, kind: MediaKind) -> object:
        rec, _ = ledger.enqueue(MediaJob(kind, {"prompt": f"x{project_id}"}, project_id=project_id,
                                         tier=QualityTier.DRAFT))
        return rec

    assert job(a.id, MediaKind.IMAGE_GENERATE).model_ref == "flux2-klein-base-4b"          # type: ignore[attr-defined]
    assert job(b.id, MediaKind.IMAGE_GENERATE).model_ref == "alt-img"                      # type: ignore[attr-defined]
    assert job(b.id, MediaKind.VIDEO_GENERATE).params["stage1_steps"] == 99                # type: ignore[attr-defined]
    assert job(a.id, MediaKind.VIDEO_GENERATE).params["stage1_steps"] == 16                # type: ignore[attr-defined]
    assert job(None, MediaKind.IMAGE_GENERATE).model_ref == "flux2-klein-base-4b"          # type: ignore[attr-defined]
    legacy = db.conn.execute("INSERT INTO projects(slug, name) VALUES ('old', 'Old')").lastrowid
    assert job(legacy, MediaKind.IMAGE_GENERATE).model_ref == "flux2-klein-base-4b"        # type: ignore[attr-defined]
    assert ledger.settings_for(legacy) is s                           # no plan recorded: the global configuration


def test_an_asset_made_by_a_model_the_plan_no_longer_uses_is_outdated_but_an_import_is_not(tmp_path: Path) -> None:
    s = _two_plan_settings(tmp_path)
    db = Database(s.db_path)
    migrate(db)
    projects, ledger = ProjectStore(db, s), Ledger(db, s)
    specs = SpecStore(db, s, ledger)
    p = projects.create("A")
    portrait = specs.create(p.id, "character.portrait", "1", MediaKind.IMAGE_GENERATE, prompt="x")
    other = specs.create(p.id, "character.portrait", "2", MediaKind.IMAGE_GENERATE, prompt="y")
    job = specs.build_job(portrait, tier=QualityTier.DRAFT)
    rec, _ = ledger.enqueue(job)
    claimed = ledger.claim(rec.id, "t", 60)
    assert claimed is not None
    ledger.complete(rec.id, "t", [NewAsset("image.generate", "character.portrait:1", "a.png", sha256="h", bytes=3)])
    ledger.register_asset(NewAsset("image.generate", "character.portrait:2", "b.png", sha256="g", bytes=3),
                          project_id=p.id)
    ledger.db.conn.execute("UPDATE media_assets SET spec_id=?, spec_version=? WHERE slot=?",
                           (other.id, other.version, "character.portrait:2"))
    plan_a, plan_alt = s.for_pipeline("vertical_drama"), s.for_pipeline("alt")
    assert up_to_date(ledger, portrait, plan_a)
    assert not up_to_date(ledger, portrait, plan_alt)                    # made by flux, the other plan uses alt-img
    assert up_to_date(ledger, portrait)                                  # without a plan nothing is judged by model
    assert up_to_date(ledger, other, plan_alt)                           # an imported file is never judged by model


def test_the_estimate_counts_tasks_and_time_and_flags_a_budget(settings: Settings, db: Database) -> None:
    ledger = Ledger(db, settings)
    brief = resolve_brief(settings, "drama_series", {"seasons": 1, "episodes_per_season": 4, "episode_sec": 60})
    e = estimate(settings, ledger, brief)
    by_kind = {t["kind"]: t for t in e["tasks"]}
    assert e["pipeline"] == "vertical_drama" and e["episodes"] == 4 and e["shots"] == 4 * 15
    assert by_kind["compose.assemble"]["count"] == 4 and by_kind["audio.music"]["count"] == 4
    assert by_kind["image.edit"]["count"] == e["shots"] and by_kind["video.generate"]["count"] == 4 * 3
    assert e["total_sec"] == sum(t["total_sec"] for t in e["tasks"]) and e["hours"] > 0
    assert all(not t["known"] for t in e["tasks"]) and e["over_budget"] is False
    tight = settings.for_pipeline("vertical_drama")
    tight = type(tight)(**{**tight.__dict__, "workflow": {**tight.workflow, "video_budget_hours": 0.01}})
    assert estimate(tight, ledger, brief, "")["over_budget"] is True
    ad = estimate(settings, ledger, resolve_brief(settings, "advert"))
    assert {t["kind"]: t["count"] for t in ad["tasks"]}["video.generate"] == ad["shots"]    # advert: a clip per shot
