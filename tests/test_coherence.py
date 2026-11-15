"""The story holds together: the outline is read before the shots are written and sent back with its holes, every
scene is written knowing the whole story before it, and the written script is read once more for story logic."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from storyagent.workflow import coherence

from .story_kit import NAMES, StoryClient, make_engine, new_project


def _outline(people: list[list[str]], **extra: Any) -> dict[str, Any]:
    scenes = [{"number": n, "location": "Office", "characters": who, "beat": f"beat {n}"}
              for n, who in enumerate(people, start=1)]
    return {"title": "T", "synopsis": "s", "acts": [{"number": 1, "scenes": scenes}], **extra}


# ---- the plain checks -------------------------------------------------------------------------------------
def test_a_reviewers_place_is_read_as_act_and_scene() -> None:
    assert coherence.scene_of("2.1") == "2.1"
    assert coherence.scene_of("2.1.3") == "2.1"
    assert coherence.scene_of("Act 2 / Scene 1 / shot 3") == "2.1"
    assert coherence.scene_of("第2幕第1场") == "2.1"
    assert coherence.scene_of("act 3, scene 12") == "3.12"
    assert coherence.scene_of("whole") is None and coherence.scene_of("") is None and coherence.scene_of(None) is None


def test_a_designed_person_who_is_never_on_screen_is_a_serious_problem() -> None:
    found = coherence.outline_problems(_outline([["Lin Wan"], ["Lin Wan"]]), NAMES)
    assert [(i["code"], i["severity"]) for i in found] == [("unused_character", "high")]
    assert "Zhao Heng" in found[0]["problem"]
    # named in a beat is being in the scene; an outline that lists nobody anywhere is not judged on this
    spoken_of = _outline([["Lin Wan"], ["Lin Wan"]])
    spoken_of["acts"][0]["scenes"][1]["beat"] = "Zhao Heng pushes the file across the desk."
    assert coherence.outline_problems(spoken_of, NAMES) == []
    assert coherence.outline_problems(_outline([[], []]), NAMES) == []
    # an episode of a series need not use everyone
    assert coherence.outline_problems(_outline([["Lin Wan"]]), NAMES, must_use_all=False) == []


def test_a_stranger_in_a_scene_and_an_empty_beat_are_reported() -> None:
    outline = _outline([NAMES, [*NAMES, "A Clerk"]])
    outline["acts"][0]["scenes"][0]["beat"] = " "
    codes = {(i["code"], i["where"]) for i in coherence.outline_problems(outline, NAMES)}
    assert codes == {("empty_beat", "1.1"), ("unknown_character", "1.2")}
    assert not [i for i in coherence.outline_problems(outline, NAMES, closed_cast=False)
                if i["code"] == "unknown_character"]       # a later episode may bring new people in


def test_what_is_planted_must_be_paid_off_in_a_later_scene() -> None:
    threads = [{"setup": "the unsigned parcel", "setup_scene": "1.1", "payoff": "her handwriting", "payoff_scene": ""},
               {"setup": "the scar", "setup_scene": "1.2", "payoff": "shown", "payoff_scene": "1.1"},
               {"setup": "the key", "setup_scene": "1.1", "payoff": "opens the box", "payoff_scene": "1.2"}]
    found = coherence.outline_problems(_outline([NAMES, NAMES], threads=threads), NAMES)
    assert [(i["code"], i["where"]) for i in found] == [("thread_open", "1.1"), ("thread_order", "1.1")]
    assert "unsigned parcel" in found[0]["problem"]


def test_the_story_so_far_keeps_every_scene_and_shortens_the_oldest_first() -> None:
    acts = [{"number": 1, "scenes": [
        {"number": n, "location": "Pier", "time_of_day": "night", "characters": NAMES, "beat": f"beat {n} " + "x" * 900}
        for n in range(1, 9)]}]
    scenes = coherence.scene_list({"acts": acts})
    assert coherence.story_so_far(scenes, 0) == "(this is the first scene)"
    text = coherence.story_so_far(scenes, 8, "Earlier episodes: one")
    lines = text.split("\n")
    assert lines[0] == "Earlier episodes: one" and len(lines) == 9          # nothing dropped
    assert all(line.startswith(f"1.{n} [Pier, night] Lin Wan, Zhao Heng: beat {n}")
               for n, line in enumerate(lines[1:], start=1))
    assert len(lines[1]) < 200 and len(lines[-1]) > 900                     # the last scenes are kept whole


def test_the_last_lines_before_a_scene_are_the_newest_in_spoken_order() -> None:
    def shot(*lines: str) -> dict[str, Any]:
        return {"dialogue": [{"character": "A", "line": text} for text in lines]}

    scenes = coherence.scene_list({"acts": [{"number": 1, "scenes": [
        {"number": 1, "shots": [shot("one", "two"), shot("three")]},
        {"number": 2, "shots": [shot(), shot("four", "five")]}, {"number": 3}]}]})
    assert coherence.last_lines(scenes, 2, 3) == "A: three\nA: four\nA: five"
    assert coherence.last_lines(scenes, 0) == "(nothing has been said yet)"


# ---- the script stage --------------------------------------------------------------------------------------
class Reader(StoryClient):
    """A story client whose two readers can be told what to find; it tells them apart by their prompt."""

    def __init__(self) -> None:
        super().__init__()
        self.outline_findings: list[list[dict[str, Any]]] = []     # one list of issues per reading, then none
        self.script_findings: list[list[dict[str, Any]]] = []
        self.outline_scores: list[float] = []
        self.cast_in_scenes: list[list[str]] = []                  # who is in the scenes of each outline written
        self.readings: list[str] = []

    def _outline(self, prompt: str) -> dict[str, Any]:
        out = super()._outline(prompt)
        n = self.calls.count("outline") - 1
        out["title"] = f"Rain Night v{n + 1}"
        out["facts"] = ["Zhao Heng sent the parcel himself.", "Lin Wan was his partner until the fire."]
        out["threads"] = [{"setup": "the parcel", "setup_scene": "1.1", "payoff": "he admits it",
                           "payoff_scene": "2.2"}]
        if n < len(self.cast_in_scenes):
            for act in out["acts"]:
                for scene in act["scenes"]:
                    scene["characters"] = self.cast_in_scenes[n]
        return out

    def _review(self, prompt: str) -> dict[str, Any]:
        outline = "The outline:" in prompt
        self.readings.append("outline" if outline else "script")
        queue = self.outline_findings if outline else self.script_findings
        issues = queue.pop(0) if queue else []
        score = self.outline_scores.pop(0) if outline and self.outline_scores else 8
        return {"score": score, "summary": "read", "issues": issues}


HOLE = {"severity": "high", "where": "1.2", "problem": "nobody is shown sending the parcel",
        "fix": "show Zhao Heng posting it in 1.1"}


def _run_script(tmp_path: Path, client: Reader, **params: Any) -> tuple[Any, dict[str, Any]]:
    engine, _, _ = make_engine(tmp_path, client)
    pid = new_project(engine)
    for stage in ("analyze", "cast"):
        engine.run_stage(pid, stage, {})
    return engine, engine.run_stage(pid, "script", {"detail": False, **params})


def test_an_outline_without_holes_is_read_once_and_kept(tmp_path: Path) -> None:
    client = Reader()
    engine, out = _run_script(tmp_path, client)
    assert client.calls.count("outline") == 1 and client.readings == ["outline", "script"]
    assert out["story_check"]["outline"] == {"score": 8.0, "rounds": 0, "rewritten": [], "open": [], "open_high": 0}
    assert out["story_check"]["script"]["rewritten"] == []
    assert engine.state.get_data(1, "story:1")["facts"][0] == "Zhao Heng sent the parcel himself."
    assert engine.state.get_data(1, "story_check:1") == out["story_check"]


def test_an_outline_with_a_hole_goes_back_to_the_writer_with_the_notes(tmp_path: Path) -> None:
    client = Reader()
    client.outline_findings = [[HOLE, {"severity": "low", "where": "2.1", "problem": "a detail"}]]
    engine, out = _run_script(tmp_path, client)
    assert client.calls.count("outline") == 2 and client.readings[:2] == ["outline", "outline"]
    rewrite = [p for p, c in zip(client.prompts, client.calls, strict=True) if c == "outline"][1]
    assert "[1.2] nobody is shown sending the parcel -> show Zhao Heng posting it in 1.1" in rewrite
    assert "a detail" not in rewrite                  # low notes are not worth a rewrite
    assert '"title": "Rain Night v1"' in rewrite      # the writer is given the outline the notes are about
    assert engine.store.get_script(1, 1)["title"] == "Rain Night v2"
    assert out["story_check"]["outline"]["rounds"] == 1 and out["story_check"]["outline"]["open_high"] == 0


def test_a_rewrite_that_made_things_worse_is_thrown_away(tmp_path: Path) -> None:
    client = Reader()
    worse = [HOLE, {**HOLE, "where": "2.1", "problem": "now she is in two places"}]
    client.outline_findings = [[HOLE], worse, worse]
    engine, out = _run_script(tmp_path, client)
    assert client.calls.count("outline") == 3                       # two rounds, then it stops
    assert engine.store.get_script(1, 1)["title"] == "Rain Night v1"
    # what is still open is kept for a person to see, with the scene it is in
    assert out["story_check"]["outline"]["open_high"] == 1 and out["story_check"]["outline"]["rounds"] == 2
    assert out["story_check"]["outline"]["open"][0]["where"] == "1.2"


def test_a_person_of_the_cast_left_out_of_every_scene_sends_the_outline_back(tmp_path: Path) -> None:
    client = Reader()
    client.cast_in_scenes = [[NAMES[0]]]                # the first outline forgets Zhao Heng, the second does not
    engine, out = _run_script(tmp_path, client)
    rewrite = [p for p, c in zip(client.prompts, client.calls, strict=True) if c == "outline"][1]
    assert "Zhao Heng is in the cast but appears in no scene" in rewrite
    assert out["story_check"]["outline"]["rounds"] == 1 and out["story_check"]["outline"]["open"] == []


def test_every_scene_is_written_knowing_the_story_before_it(tmp_path: Path) -> None:
    client = Reader()
    _run_script(tmp_path, client)
    shots = [p for p, c in zip(client.prompts, client.calls, strict=True) if c == "shots"]
    assert len(shots) == 4
    assert "(this is the first scene)" in shots[0] and "(nothing has been said yet)" in shots[0]
    last = shots[3]                                                  # scene 2.2 knows 1.1, 1.2 and 2.1
    for where in ("1.1 [", "1.2 [", "2.1 ["):
        assert where in last
    assert "beat 1.1" in last and "Lin Wan: You are late." in last
    assert "- Zhao Heng sent the parcel himself." in last and "- Lin Wan was his partner until the fire." in last


def test_a_scene_with_a_serious_slip_is_written_again_with_the_readers_notes(tmp_path: Path) -> None:
    client = Reader()
    slip = {"severity": "high", "where": "1.2.1", "problem": "she names the sender before 2.2 reveals him",
            "fix": "she only asks who sent it"}
    client.script_findings = [[slip, {"severity": "medium", "where": "2.1", "problem": "a small slip"},
                               {"severity": "high", "where": "whole", "problem": "no scene to rewrite"}]]
    # as a run to the film does it: the rewrite goes into the script at once (tests/test_revisions.py: the other way)
    engine, out = _run_script(tmp_path, client, review_apply="auto")
    shots = [p for p, c in zip(client.prompts, client.calls, strict=True) if c == "shots"]
    assert len(shots) == 5                                           # four scenes and the one written again
    assert "[1.2.1] she names the sender before 2.2 reveals him -> she only asks who sent it" in shots[4]
    assert '"line": "You are late."' in shots[4]                     # with the shots it has now
    assert client.readings == ["outline", "script", "script"]        # read again after the rewrite
    check = out["story_check"]["script"]
    assert check["rewritten"] == ["1.2"] and check["rounds"] == 1 and check["open"] == []


def test_the_checks_can_be_switched_off_and_a_resume_does_not_read_again(tmp_path: Path) -> None:
    client = Reader()
    engine, out = _run_script(tmp_path, client, story_check=False)
    assert client.readings == [] and out["story_check"] == {}
    engine.run_stage(1, "script", {"detail": False})                 # nothing to write: nothing to read
    assert client.readings == []
    engine.run_stage(1, "script", {"detail": False, "story_check": True})      # asked for: the script is read
    assert client.readings == ["script"]


def test_a_later_episode_is_told_how_the_one_before_ended_and_what_it_fixed(tmp_path: Path) -> None:
    client = Reader()
    engine, _ = _run_script(tmp_path, client)
    engine.run_stage(1, "script", {"detail": False, "episode": 2})
    outline = [p for p, c in zip(client.prompts, client.calls, strict=True) if c == "outline"][-1]
    assert "This is episode 2 of a series." in outline
    assert 'Episode 1 "Rain Night v1": Two people, one case. It ended with: beat 2.2' in outline
    assert "Zhao Heng sent the parcel himself." in outline
    first_scene = [p for p, c in zip(client.prompts, client.calls, strict=True) if c == "shots"][4]
    assert "Earlier episodes (already shown" in first_scene and "(this is the first scene)" not in first_scene
