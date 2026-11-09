"""A scene gets the number of shots the outline asks for, and its freshly written shots are cleaned."""
from __future__ import annotations

import json
from pathlib import Path

from storyagent.workflow.stages import MAX_SHOT_FACTOR, _clean_shots, _flatten_json_text, _scene_shot_count

SPECS = Path(__file__).parent.parent / "storyagent" / "agents" / "specs"


def test_a_scene_without_the_hint_gets_the_usual_number() -> None:
    assert _scene_shot_count({}, 3) == 3
    assert _scene_shot_count({"shot_count": None}, 3) == 3
    assert _scene_shot_count({"shot_count": "x"}, 3) == 3


def test_the_hint_is_kept_between_one_and_a_multiple_of_the_usual_number() -> None:
    assert _scene_shot_count({"shot_count": 7}, 3) == 7
    assert _scene_shot_count({"shot_count": 40}, 3) == 3 * MAX_SHOT_FACTOR
    assert _scene_shot_count({"shot_count": 40}, 3, factor=2) == 6
    assert _scene_shot_count({"shot_count": 1}, 3) == 1


def test_json_written_out_as_text_loses_its_braces() -> None:
    raw = json.dumps({"left": "林岚 standing", "right": ["blade low", "weight on the back foot"]})
    assert _flatten_json_text(raw) == "林岚 standing, blade low, weight on the back foot"
    assert _flatten_json_text("plain words") == "plain words"
    assert _flatten_json_text("[not json") == "[not json"
    assert _flatten_json_text(3) == 3


def test_a_repeated_narration_is_kept_once_and_the_scene_line_is_not_copied() -> None:
    scene = {"narration": "晨风微凉。"}
    shots = [{"narration": "晨风微凉。"}, {"narration": "她决定留下。"}, {"narration": "她决定留下。"},
             {"narration": "她决定留下。", "start_pose": '{"a": "hands open"}'}, {"narration": ""}]
    kept = _clean_shots(shots, scene)
    assert [s["narration"] for s in kept] == ["", "她决定留下。", ""]       # the empty last shot is left out
    assert kept[2]["start_pose"] == "hands open"


def test_a_looping_writer_cannot_fill_a_scene_with_copies_or_empty_shots() -> None:
    copy = {"camera_movement": "pull back", "start_pose": "they walk out together"}
    shots = [dict(copy) for _ in range(6)] + [{"camera_angle": "Close-up", "camera_movement": "pull back"}] * 3
    shots.insert(1, {"action": "she drops the blade", "start_pose": "blade raised"})
    kept = _clean_shots(shots, {})
    assert [s.get("action") or s.get("start_pose") for s in kept] == ["they walk out together", "she drops the blade"]
    many = [{"action": f"move {i}"} for i in range(9)]
    assert len(_clean_shots(many, {}, cap=4)) == 4 and len(_clean_shots(many, {})) == 9


def test_the_outline_and_shot_prompts_ask_for_the_number_and_the_beats() -> None:
    outline = (SPECS / "screenwriter.outline.toml").read_text(encoding="utf-8")
    shots = (SPECS / "screenwriter.shots.toml").read_text(encoding="utf-8")
    assert "shot_count" in outline and "scene_type" in outline and '"shots_per_scene"' in outline
    assert "ONE move or ONE beat" in shots
    schema = json.loads((SPECS / "schemas" / "outline.json").read_text(encoding="utf-8"))
    scene = schema["properties"]["acts"]["items"]["properties"]["scenes"]["items"]["properties"]
    assert scene["shot_count"]["type"] == "integer" and scene["scene_type"]["type"] == "string"
