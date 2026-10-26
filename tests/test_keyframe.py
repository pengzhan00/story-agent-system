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
    assert out.startswith("East Asian Chinese people, a man pours tea")
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


def test_skin_that_glistens_is_rewritten_but_other_liquids_are_left_alone() -> None:
    out = keyframe.clean("her body glistening slightly with heat-induced sheen, his skin glistening under the lamp, "
                         "liquid glistening on her back")
    assert "with a faint natural flush" in out and "lit by the lamp" in out and "glistening on her back" in out


def test_people_are_named_by_their_own_description_not_by_the_order_they_are_listed() -> None:
    from storyagent.workflow.seed import _character_identities

    woman = {"prompt_template": "A young Chinese woman with delicate features, soft black hair. She wears a blouse."}
    man = {"prompt_template": "A handsome East Asian male, 25 years old, sharp jaw. He wears a white shirt."}
    chars = {"Lin": woman, "Gu": man, "Mei": woman}
    assert _character_identities({"characters": ["Gu"]}, chars).startswith("the man is a handsome East Asian male")
    both = _character_identities({"characters": ["Gu", "Lin"]}, chars)
    assert both.startswith("the man is a handsome") and "the woman is a young Chinese woman" in both
    twice = _character_identities({"characters": ["Lin", "Mei"]}, chars)
    assert "the first woman is" in twice and "the second woman is" in twice
