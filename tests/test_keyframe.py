"""Image-prompt guardrails: same ethnicity, natural skin, readable exposure; and who is in a shot."""
from __future__ import annotations

from storyagent.workflow import keyframe
from storyagent.workflow.shotplan import who_is_in_frame

NAMES = ["Lin", "Gu"]


def test_greasy_and_murky_words_are_rewritten() -> None:
    text = keyframe.clean("close-up, wet skin texture, macro details of sweat, deep shadows, Glistening body")
    assert "wet" not in text.lower() and "glistening" not in text.lower() and "shadows" not in text.lower()
    assert "natural matte skin" in text and "soft shadow falloff" in text


def test_a_chinese_project_names_the_people_and_ties_them_to_the_portraits() -> None:
    out = keyframe.image_prompt("a man pours tea.", language="zh-CN", with_portraits=True)
    assert out.startswith("a man pours tea, East Asian Chinese people")
    assert "reference portraits" in out and "natural matte skin" in out
    assert "East Asian" not in keyframe.image_prompt("a man pours tea", language="en")
    assert "reference portraits" not in keyframe.scene_prompt("an empty living room")


def test_the_negative_loses_what_hurts_and_gains_what_must_not_show() -> None:
    out = keyframe.negative_prompt("cool colors, dry skin, overly bright exposure, blurry", language="zh-CN")
    tokens = [t.strip() for t in out.split(",")]
    assert "dry skin" not in tokens and "overly bright exposure" not in tokens and "blurry" in tokens
    assert "oily skin" in tokens and "Caucasian" in tokens and tokens.count("oily skin") == 1
    assert "Caucasian" not in keyframe.negative_prompt("blurry", language="en")


def test_the_tool_guide_depends_on_whether_references_carry_the_faces() -> None:
    assert "\n" not in keyframe.guide()
    assert "Reference images carry identity" in keyframe.guide(with_references=True)
    assert "no reference images" in keyframe.guide()


def test_a_shot_that_lists_nobody_gets_its_speakers_and_the_names_it_mentions() -> None:
    dialogue = [{"character": "Gu", "line": "Do not move."}]
    assert who_is_in_frame({}, {}, dialogue, NAMES) == ["Gu"]
    assert who_is_in_frame({"narration": "Gu pulls Lin closer"}, {}, [], NAMES) == ["Lin", "Gu"]
    assert who_is_in_frame({"narration": "an empty room"}, {}, [], NAMES) == []
    assert who_is_in_frame({"characters_present": ["Lin"]}, {"characters": ["Gu"]}, dialogue, NAMES) == ["Lin"]
    assert who_is_in_frame({}, {"characters": ["Gu"]}, [], NAMES) == ["Gu"]


def test_a_fixed_seed_is_stable_per_project_and_key() -> None:
    assert keyframe.fixed_seed(4, "character:Lin") == keyframe.fixed_seed(4, "character:Lin")
    assert keyframe.fixed_seed(4, "character:Lin") != keyframe.fixed_seed(4, "character:Gu")
    assert keyframe.fixed_seed(4, "character:Lin") != keyframe.fixed_seed(5, "character:Lin")
    assert 0 <= keyframe.fixed_seed(4, "x") < 2_000_000_000


def test_anatomy_is_stated_in_the_prompt_and_the_negative() -> None:
    assert "flat muscular chest" in keyframe.scene_prompt("a room") and "gynecomastia" in keyframe.negative_prompt("")
