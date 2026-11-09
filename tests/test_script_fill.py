"""A scene the outline gave a shot count is filled one shot at a time when the writer's first answer is short."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .story_kit import StoryClient, make_engine, new_project

STORY = ["analyze", "cast", "script"]


class AskingClient(StoryClient):
    """An outline that asks 4 shots for its first scene; the shot writer answers with one new move per call."""

    def __init__(self) -> None:
        super().__init__()
        self.moves = 0

    def _outline(self, prompt: str) -> dict[str, Any]:
        data = super()._outline(prompt)
        data["acts"][0]["scenes"][0]["shot_count"] = 4
        return data

    def _shots(self, prompt: str) -> dict[str, Any]:
        self.moves += 1
        return {"shots": [{"shot_number": 1, "camera_angle": "wide", "characters_present": ["x"],
                           "action": f"move {self.moves}", "dialogue": [], "duration_sec": 3}]}


def test_a_short_answer_is_filled_up_one_shot_at_a_time(tmp_path: Path) -> None:
    client = AskingClient()
    engine, _, _ = make_engine(tmp_path, client=client)
    pid = new_project(engine)
    for stage in STORY:
        engine.run_stage(pid, stage, {"visual_prompts": False})
    scenes = [sc for act in engine.store.get_script(pid, 1)["acts"] for sc in act["scenes"]]
    assert [len(sc["shots"]) for sc in scenes][0] == 4                      # 1 from the first answer + 3 asked for
    assert [sc["shots"][i]["action"] for sc in scenes[:1] for i in range(4)] == ["move 1", "move 2", "move 3", "move 4"]
    singles = [p for c, p in zip(client.calls, client.prompts, strict=True) if c == "shots" and "2 of 4" in p]
    assert singles and "move 1" in singles[0]                               # it is shown the shots written so far
    assert all(len(sc["shots"]) == 1 for sc in scenes[1:])                  # scenes without a count stay as written
