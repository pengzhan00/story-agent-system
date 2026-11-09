"""Shot cards: written for set-piece scenes, checked, turned into prompts, reviewed with the fixed descriptions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from storyagent.workflow import detail

from .story_kit import StoryClient, make_engine, new_project

VAGUE = ["entwined", "lost in passion", "缠绵"]


def _card(**changes: Any) -> dict[str, Any]:
    person = {"who": "the woman", "clothing": "torn shirt", "body_in_view": "bare shoulders", "pose": "kneeling",
              "limbs": "left hand on his chest", "face": "eyes closed", "skin": "sweat"}
    card: dict[str, Any] = {
        "summary": "s", "position_name": "p1", "people": [person], "contact": "her hand on his chest",
        "motion": {"main": "leans in", "rhythm": "slow", "secondary": "hair", "still": "bed"},
        "camera": {"size": "medium", "angle": "eye", "height": "low", "lens_mm": 50.0, "movement": "push",
                   "crop": "knees", "focus": "faces"},
        "light": "lamp", "setting": "bed", "sound": "breath", "start_frame": " ".join(["w"] * 70), "end_frame": "e",
        "video_motion": "she leans in", "continuity_out": "shirt torn", "negative": "extra limbs"}
    card.update(changes)
    return card


def _lint(card: dict[str, Any], used: list[str] | None = None, people: int = 1) -> list[str]:
    return detail.lint_card(card, people=people, vague=VAGUE, min_words=60, used=used or [])


def test_a_complete_card_has_no_problems() -> None:
    assert _lint(_card()) == []


def test_what_is_missing_or_vague_is_named() -> None:
    problems = _lint(_card(start_frame="too short", contact="", light="", people=[]), people=2)
    text = " | ".join(problems)
    assert "`light` is empty" in text and "`contact` is empty" in text and "`people` must have 2" in text
    assert "shorter than 60 words" in text
    assert any("vague words" in p and "entwined" in p for p in _lint(_card(video_motion="their limbs entwined")))
    assert any("缠绵" in p for p in _lint(_card(summary="两人缠绵")))


def test_a_position_is_not_repeated_a_third_time() -> None:
    assert _lint(_card(position_name="on top"), used=["on top", "on top"])
    assert not _lint(_card(position_name="on top"), used=["behind", "on top"])
    held = "woman prone man behind"
    assert _lint(_card(position_name=held + " deep thrust"), used=[held, held + " thrusting"])    # same arrangement


def test_everyone_the_shot_names_is_in_frame_even_when_the_list_says_one() -> None:
    names = ["Lin", "Lu"]
    shot = {"characters_present": ["Lu"], "action": "Lu grips Lin's wrist and drags her down"}
    assert detail._in_frame(shot, {"characters": names}, names) == ["Lin", "Lu"]
    assert detail._in_frame({"characters_present": ["Lu"], "action": "he turns"}, {}, names) == ["Lu"]
    assert detail._in_frame({"action": "a door"}, {"characters": ["Lin"]}, names) == ["Lin"]


def test_two_people_may_not_share_one_description() -> None:
    twin = {**_card()["people"][0], "who": "the man"}
    assert any("same `clothing` and `limbs`" in p for p in _lint(_card(people=[_card()["people"][0], twin]), people=2))
    other = {**twin, "clothing": "bare", "limbs": "hands on her hips"}
    assert _lint(_card(people=[_card()["people"][0], other]), people=2) == []


def test_the_prompts_come_from_the_card_word_for_word() -> None:
    built = detail.prompts_from_detail(_card(start_frame="the opening still", end_frame="the last still"), "film grain")
    assert "the opening still" in built["image_prompt"] and "left hand on his chest" in built["image_prompt"]
    assert "50mm lens" in built["image_prompt"] and built["image_prompt"].endswith("film grain")
    assert built["video_prompt"].startswith("Starting exactly from the opening frame: the opening still")
    assert "Ends on: the last still" in built["video_prompt"] and built["negative_prompt"] == "extra limbs"


def test_reviews_are_merged_with_the_lens_named() -> None:
    one = detail.merge_reviews([("story", {"score": 6, "summary": "ok", "issues": [{"problem": "a"}]})])
    assert one["summary"] == "ok" and one["issues"] == [{"problem": "a", "lens": "story"}]
    two = detail.merge_reviews([("story", {"score": 6, "summary": "a"}), ("rating", {"score": 8, "summary": "b",
                                                                                      "issues": [{"problem": "x"}]})])
    assert two["score"] == 7.0 and two["summary"] == "[story] a\n[rating] b"
    assert two["issues"] == [{"problem": "x", "lens": "rating"}]


class Detailing(StoryClient):
    """An outline whose first scene is an intimate one."""

    def _outline(self, prompt: str) -> dict[str, Any]:
        data = super()._outline(prompt)
        data["acts"][0]["scenes"][0]["scene_type"] = "intimate"
        return data


def _story(tmp_path: Path, client: StoryClient) -> tuple[Any, int]:
    engine, _, _ = make_engine(tmp_path, client=client)
    pid = new_project(engine)
    for stage in ("analyze", "cast", "script"):
        engine.run_stage(pid, stage, {"visual_prompts": False})
    return engine, pid


def test_only_the_set_piece_scenes_get_cards_and_the_anchors_are_written_once(tmp_path: Path) -> None:
    client = Detailing()
    engine, pid = _story(tmp_path, client)
    scenes = [sc for act in engine.store.get_script(pid, 1)["acts"] for sc in act["scenes"]]
    assert [bool(sh.get("detail")) for sh in scenes[0]["shots"]] == [True, True]
    assert all(not sh.get("detail") for sc in scenes[1:] for sh in sc["shots"])
    assert [sh["detail"].get("problems") for sh in scenes[0]["shots"]] == [None, None]
    assert client.calls.count("detail") == 2 and client.calls.count("anchor") >= 3       # people and the places
    assert detail.anchor_of(engine.state, pid, "character", "Lin Wan").startswith("fixed description")
    engine.run_stage(pid, "script", {"visual_prompts": False})                          # nothing is written twice
    assert client.calls.count("detail") == 2
    again = client.calls.count("anchor")
    engine.run_stage(pid, "script", {"visual_prompts": False, "redetail": True})
    assert client.calls.count("detail") == 4 and client.calls.count("anchor") == again


def test_a_card_with_problems_is_asked_again_and_then_kept_with_them_listed(tmp_path: Path) -> None:
    client = Detailing()
    client.card_words = 10                                    # always shorter than the 60 words asked for
    engine, pid = _story(tmp_path, client)
    shot = engine.store.get_script(pid, 1)["acts"][0]["scenes"][0]["shots"][0]
    assert client.calls.count("detail") == 2 * 3             # first answer + 2 asked again, for each of 2 shots
    assert any("shorter than 60 words" in p for p in shot["detail"]["problems"])
    fixing = [p for c, p in zip(client.calls, client.prompts, strict=True) if c == "detail"][1]
    assert "shorter than 60 words" in fixing                  # the second request names what was wrong


def test_the_keyframe_prompt_is_the_card_plus_the_fixed_descriptions(tmp_path: Path) -> None:
    engine, pid = _story(tmp_path, Detailing())
    for stage in ("scenes", "shotplan", "specs"):
        engine.run_stage(pid, stage, {})
    shot = next(s for s in engine.store.list_shots(pid) if s["render_payload"].get("detail"))
    spec = engine.spec_store.current(pid, "shot.keyframe", str(shot["id"]))
    assert spec is not None and "word word word" in spec.prompt
    assert "left hand on his chest" in spec.prompt and "extra limbs" in spec.negative_prompt
    video = engine.spec_store.current(pid, "shot.video", str(shot["id"]))
    assert video is not None and video.prompt.startswith("Starting exactly")


def test_the_review_reads_the_story_with_the_cards_through_several_lenses(tmp_path: Path) -> None:
    client = Detailing()
    engine, pid = _story(tmp_path, client)
    before = client.calls.count("review")
    review = engine.review_script(pid, 1)
    assert client.calls.count("review") - before == 3                  # story, continuity, producible (rating: general)
    assert review["summary"].splitlines()[0].startswith("[story]") and len(review["summary"].splitlines()) == 3
    seen = [p for c, p in zip(client.calls, client.prompts, strict=True) if c == "review"][-2:]
    assert all("position 1" in p and "limbs: left hand on his chest" in p for p in seen)      # they read the cards
    assert "fixed description" in seen[0]                                                     # and the fixed looks


def test_a_review_without_cards_stays_the_story_review(tmp_path: Path) -> None:
    client = StoryClient()
    engine, pid = _story(tmp_path, client)
    before = client.calls.count("review")
    review = engine.review_script(pid, 1)
    assert client.calls.count("review") - before == 1 and review["summary"] == "fine"


def test_the_fixed_block_uses_the_labels_of_the_prompts() -> None:
    class State:
        data = {"anchor:character:Lin": {"text": "broad shoulders, a scar on the left hip"},
                "anchor:location:Bedroom": {"text": "a 180 cm wide bed under the window"}}

        def get_data(self, pid: int, key: str, default: Any = None) -> Any:
            return self.data.get(key, default)

    chars = {"Lin": {"prompt_template": "A woman of 28 with long black hair."},
             "Lu": {"prompt_template": "A man of 32 with a short beard."}}
    block = detail.fixed_block(State(), 1, ["Lin", "Lu"], chars, "Bedroom")
    assert block == ("the woman, fixed details: broad shoulders, a scar on the left hip. "
                     "The place, fixed details: a 180 cm wide bed under the window.")


def test_a_person_with_a_fixed_description_is_not_described_twice() -> None:
    from storyagent.workflow.seed import _character_identities, labelled_identities

    chars = {"Lin": {"prompt_template": "Cinematic realism, gritty texture"},      # no usable identity of its own
             "Lu": {"prompt_template": "A man of 32 with a short beard."}}
    anchors = {"Lin": "A Chinese woman, 28 years old, with a slender build"}
    labels = labelled_identities(["Lin", "Lu"], chars, keep_missing=True, anchors=anchors)
    assert [(label, name) for label, name, _ in labels] == [("the woman", "Lin"), ("the man", "Lu")]
    block = _character_identities({"characters": ["Lin", "Lu"]}, chars, anchors)
    assert block == "the man is a man of 32 with a short beard."               # Lin is in the fixed block instead
