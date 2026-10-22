"""Continuity ledger: costumes that change over the story, props with one fixed look, both reaching the shot payload."""
from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.story import StoryError, StoryNotFound
from storyagent.workflow.continuity import look_for, props_in

from .story_kit import NAMES, make_engine, new_project


def look(i: int, cid: int, ep: int, act: int, scene: int, costume: str = "c") -> dict[str, object]:
    return {"id": i, "character_id": cid, "from_episode": ep, "from_act": act, "from_scene": scene, "costume": costume,
            "label": ""}


def test_the_look_in_force_is_the_latest_one_that_has_started() -> None:
    looks = [look(1, 7, 1, 1, 1, "coat"), look(2, 7, 1, 2, 3, "uniform"), look(3, 7, 2, 1, 1, "dress"),
             look(4, 8, 1, 1, 1, "suit")]
    assert look_for(looks, 7, 1, 1, 1)["costume"] == "coat"
    assert look_for(looks, 7, 1, 2, 2)["costume"] == "coat"          # the uniform starts at act 2 scene 3
    assert look_for(looks, 7, 1, 2, 3)["costume"] == "uniform"
    assert look_for(looks, 7, 1, 9, 9)["costume"] == "uniform"
    assert look_for(looks, 7, 2, 1, 1)["costume"] == "dress" and look_for(looks, 7, 5, 1, 1)["costume"] == "dress"
    assert look_for(looks, 8, 3, 1, 1)["costume"] == "suit"
    assert look_for(looks, 9, 1, 1, 1) is None                        # a character without looks
    assert look_for([look(5, 7, 2, 1, 1)], 7, 1, 9, 9) is None         # not started yet
    tie = [look(1, 7, 1, 1, 1, "first"), look(2, 7, 1, 1, 1, "second")]
    assert look_for(tie, 7, 1, 1, 1)["costume"] == "second"            # same start: the newer row wins


def test_props_are_found_by_name_once_introduced() -> None:
    props = [{"name": "Order slip", "description": "a yellowed paper slip", "introduced_episode": 1},
             {"name": "Silver key", "description": "small silver key", "introduced_episode": 3}]
    texts = ["She picks up the order slip from the table", "", "the SILVER KEY glints"]
    assert props_in(props, texts, 1) == [{"name": "Order slip", "description": "a yellowed paper slip"}]
    assert [p["name"] for p in props_in(props, texts, 3)] == ["Order slip", "Silver key"]
    assert props_in(props, ["nothing here"], 3) == []


def test_the_cast_stage_makes_the_designed_costume_the_first_look_and_shots_carry_it(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, ["analyze", "cast", "script", "scenes", "shotplan"], {"visual_prompts": False})
    store = engine.store
    heroine = store.find_character(pid, NAMES[0])
    [first] = store.list_looks(pid, heroine["id"])
    assert (first["label"], first["costume"]) == ("default", "grey coat")
    assert (first["from_episode"], first["from_act"], first["from_scene"]) == (1, 1, 1)
    worn = {c["name"]: c["costume"] for s in store.list_shots(pid)
            for c in s["render_payload"]["subject"]["characters"]}
    assert worn[NAMES[0]] == "grey coat"
    engine.run(pid, ["cast"], {"force": True})
    assert len(store.list_looks(pid, heroine["id"])) == 1                 # a re-run does not pile up looks


def test_a_costume_change_and_a_prop_reach_the_shot_payload_after_the_shot_plan(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, ["analyze", "cast", "script", "scenes", "shotplan"], {"visual_prompts": False})
    store = engine.store
    hero = store.find_character(pid, NAMES[0])
    # the fake script has the heroine in shot 1 of each of the four scenes (act 1 scenes 1-2, act 2 scenes 1-2)
    store.create_look(pid, {"character_id": hero["id"], "label": "night shift", "costume": "orange safety vest",
                            "from_act": 2, "from_scene": 1})
    store.create_prop(pid, {"name": "pause", "description": "a heavy red steel door"})      # in "A pause." narration
    engine.run(pid, ["shotplan"], {})

    def worn(act: int, scene: int) -> str:
        shot = next(s for s in store.list_shots(pid)
                    if (s["act_number"], s["scene_number"], s["shot_number"]) == (act, scene, 1))
        return next(c["costume"] for c in shot["render_payload"]["subject"]["characters"] if c["name"] == NAMES[0])

    assert [worn(1, 1), worn(1, 2), worn(2, 1), worn(2, 2)] == ["grey coat", "grey coat", "orange safety vest",
                                                                  "orange safety vest"]
    by_number = {(s["act_number"], s["scene_number"], s["shot_number"]): s["render_payload"]["continuity"]["props"]
                 for s in store.list_shots(pid)}
    assert by_number[(1, 1, 2)] == [{"name": "pause", "description": "a heavy red steel door"}]
    assert by_number[(1, 1, 1)] == []                                    # the word is not in that shot


def test_looks_and_props_are_validated(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    other = new_project(engine, "Another story")
    engine.run(pid, ["analyze", "cast"], {})
    store = engine.store
    hero = store.find_character(pid, NAMES[0])
    with pytest.raises(StoryError, match="character_id"):
        store.create_look(pid, {"costume": "x"})
    with pytest.raises(StoryError, match="costume"):
        store.create_look(pid, {"character_id": hero["id"], "costume": "  "})
    with pytest.raises(StoryError, match="1 or more"):
        store.create_look(pid, {"character_id": hero["id"], "costume": "x", "from_episode": 0})
    with pytest.raises(StoryNotFound):
        store.create_look(other, {"character_id": hero["id"], "costume": "x"})      # another project's character
    with pytest.raises(StoryError, match="unknown"):
        store.create_look(pid, {"character_id": hero["id"], "costume": "x", "colour": "red"})
    store.create_prop(pid, {"name": "Slip", "description": "d"})
    with pytest.raises(StoryError, match="taken"):
        store.create_prop(pid, {"name": " Slip "})
    prop = store.create_prop(pid, {"name": "Key"})
    with pytest.raises(StoryError, match="taken"):
        store.update_prop(prop["id"], {"name": "Slip"})
    assert store.update_prop(prop["id"], {"description": "silver"})["description"] == "silver"
    store.create_prop(other, {"name": "Slip"})                                          # names are per project
    look_row = store.create_look(pid, {"character_id": hero["id"], "costume": "x"})
    store.delete_look(look_row["id"])
    store.delete_prop(prop["id"])
