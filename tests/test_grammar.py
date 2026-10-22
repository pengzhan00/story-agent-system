"""Shot grammar: the rules, the repairs, the API and the CLI."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.config import ConfigError, Settings, load_settings
from storyagent.workflow.grammar import apply_fixes, check, run_check

from . import test_api as base_tests
from .story_kit import NAMES, make_engine, new_project
from .test_api import project

client = base_tests.client


@pytest.fixture
def grammar(tmp_path: Path):  # noqa: ANN201
    return load_settings({"STORY_AGENT_HOME": str(tmp_path / "h")}).grammar


def shot(i: int, size: str, *, scene: int = 1, who: tuple[str, ...] = ("Ann",), where: str = "Alley",
         dialogue: tuple[tuple[str, str], ...] = (), action: str = "", staging: dict[str, str] | None = None,
         seconds: float = 3.0) -> dict[str, Any]:
    return {"id": i, "episode_id": 1, "act_number": 1, "scene_number": scene, "shot_number": i, "location": where,
            "shot_type": size, "characters": list(who), "locked": False,
            "dialogue": [{"character": c, "line": line, "emotion": ""} for c, line in dialogue],
            "render_payload": {"camera": {"duration_sec": seconds}, "subject": {"action": action},
                               "staging": staging or {}}}


def rules_of(report) -> list[tuple[str, int]]:  # noqa: ANN001
    return [(v.rule, v.shot_id) for v in report.violations]


def test_sizes_are_recognised_by_name_alias_and_spelling(grammar) -> None:  # noqa: ANN001
    assert [grammar.rank(x) for x in ("Wide", "close up", "CU", "Close_Up", "ECU", "nonsense")] == [1, 5, 5, 5, 6, None]


def test_a_new_place_must_open_wide_and_the_repair_is_a_wide_shot(grammar) -> None:  # noqa: ANN001
    shots = [shot(1, "close-up"), shot(2, "wide", scene=2, where="Roof"), shot(3, "medium", scene=2, where="Roof")]
    report = check(shots, {1: 1}, grammar)
    assert rules_of(report) == [("establishing", 1)]
    [v] = report.violations
    assert v.fix == {"type": "size", "value": "wide"} and v.params["max_size"] == "full"


def test_the_same_place_does_not_need_a_second_establishing_shot(grammar) -> None:  # noqa: ANN001
    shots = [shot(1, "wide"), shot(2, "close-up", scene=2)]          # scene 2 is still the Alley
    assert rules_of(check(shots, {1: 1}, grammar)) == []


def test_near_identical_framing_is_a_jump_cut_and_a_leap_across_the_scale_is_noted(grammar) -> None:  # noqa: ANN001
    shots = [shot(1, "wide"), shot(2, "medium"), shot(3, "medium"), shot(4, "wide"), shot(5, "extreme-close-up")]
    report = check(shots, {1: 1}, grammar)
    assert ("jump_size", 3) in rules_of(report) and ("size_leap", 5) in rules_of(report)
    jump = next(v for v in report.violations if v.rule == "jump_size")
    assert jump.fix == {"type": "size", "value": "medium-close"}          # one step tighter
    assert next(v for v in report.violations if v.rule == "size_leap").severity == "info"


def test_a_scene_needs_more_than_one_framing_and_talk_needs_a_face(grammar) -> None:  # noqa: ANN001
    talk = (("Ann", "a"), ("Ann", "b"), ("Ann", "c"))
    shots = [shot(1, "medium", dialogue=talk), shot(2, "full"), shot(3, "full")]
    assert ("dialogue_close", 1) in rules_of(check(shots, {1: 1}, grammar))
    flat = [shot(1, "medium"), shot(2, "medium", who=("Bob",)), shot(3, "medium", who=("Ann", "Bob"))]
    assert ("coverage", 1) in rules_of(check(flat, {1: 1}, grammar))


def test_the_speaker_must_be_in_frame_and_only_known_characters_are_added(grammar) -> None:  # noqa: ANN001
    shots = [shot(1, "wide", dialogue=(("Bob", "hello"),)), shot(2, "wide", dialogue=(("Narrator", "x"),))]
    report = check(shots, {1: 1}, grammar, {"Ann", "Bob"})
    [v] = [v for v in report.violations if v.rule == "speaker_in_frame"]
    assert v.shot_id == 1 and v.severity == "error" and v.fix == {"type": "add_character", "value": "Bob"}


def test_opposite_screen_directions_cross_the_axis_and_one_action_per_shot(grammar) -> None:  # noqa: ANN001
    shots = [shot(1, "wide", staging={"screen_direction": "left"}),
             shot(2, "full", staging={"screen_direction": "right"}, action="She opens the door and then leaves")]
    report = check(shots, {1: 1}, grammar)
    assert ("axis_cross", 2) in rules_of(report) and ("single_action", 2) in rules_of(report)
    assert next(v for v in report.violations if v.rule == "axis_cross").fix["value"] == "left"


def test_a_run_of_equal_lengths_is_noted_once(grammar) -> None:  # noqa: ANN001
    sizes = ["wide", "medium", "close-up", "medium", "wide", "close-up"]
    report = check([shot(i + 1, s) for i, s in enumerate(sizes)], {1: 1}, grammar)
    assert [v.shot_id for v in report.violations if v.rule == "monotone_pace"] == [5]


def test_unknown_sizes_are_counted_not_judged_and_rules_can_be_switched_off(tmp_path: Path) -> None:
    home = tmp_path / "h"
    (home / "config").mkdir(parents=True)
    (home / "config" / "grammar.toml").write_text('[grammar.rules.establishing]\nenabled = false\n')
    s = load_settings({"STORY_AGENT_HOME": str(home)})
    report = check([shot(1, "dutch tilt"), shot(2, "close-up")], {1: 1}, s.grammar)
    assert report.unclassified == 1 and report.violations == []
    (home / "config" / "grammar.toml").write_text('[grammar.rules.nonsense]\nseverity = "info"\n')
    with pytest.raises(ConfigError):
        load_settings({"STORY_AGENT_HOME": str(home)})


def _planned(tmp_path: Path):  # noqa: ANN202
    engine, _, settings = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, ["analyze", "cast", "script", "scenes", "shotplan"], {"visual_prompts": False})
    return engine, settings, pid


def test_the_shot_plan_reports_the_grammar_and_a_repair_changes_the_shot(tmp_path: Path) -> None:
    engine, settings, pid = _planned(tmp_path)
    store = engine.store
    first = store.list_shots(pid)[0]
    store.update_shot(first["id"], {"shot_type": "close-up",
                                    "dialogue": [{"character": NAMES[1], "line": "Hello.", "emotion": ""}],
                                    "characters": [NAMES[0]]})
    before = run_check(store, pid, settings.grammar)
    assert any(v.rule == "establishing" and v.shot_id == first["id"] for v in before.violations)
    assert any(v.rule == "speaker_in_frame" and v.shot_id == first["id"] for v in before.violations)
    fixed, left, after = apply_fixes(store, pid, settings.grammar)
    assert {"shot_id": first["id"], "rule": "establishing"} in fixed
    row = store.get_shot(first["id"])
    assert row["shot_type"] == "wide" and row["render_payload"]["camera"]["shot_type"] == "wide"
    assert NAMES[1] in row["characters"]
    assert [c["name"] for c in row["render_payload"]["subject"]["characters"]].count(NAMES[1]) == 1
    assert not any(v.shot_id == first["id"] and v.rule in ("establishing", "speaker_in_frame")
                   for v in after.violations)


def test_a_locked_shot_is_left_alone(tmp_path: Path) -> None:
    engine, settings, pid = _planned(tmp_path)
    store = engine.store
    first = store.list_shots(pid)[0]
    store.update_shot(first["id"], {"shot_type": "extreme-close-up"})
    store.set_shot_lock(first["id"], True)
    fixed, left, _ = apply_fixes(store, pid, settings.grammar, rules=["establishing"])
    assert fixed == [] and left == [{"shot_id": first["id"], "rule": "establishing", "reason": "locked"}]
    assert store.get_shot(first["id"])["shot_type"] == "extreme-close-up"


def test_staging_from_the_script_reaches_the_payload_and_the_prompts() -> None:
    from storyagent.workflow.shotplan import build_payload

    payload = build_payload(
        scene={"location": "Alley"}, shot={"start_pose": "standing at the door", "end_pose": "sitting down",
                                          "gait": "slow steady steps", "camera_position": "low angle",
                                          "lens_mm": 85, "screen_direction": "left"},
        dialogue=[], location=None, characters={}, style={}, profile={}, scene_id="s", duration=3.0, previous="")
    assert payload["staging"] == {"start_pose": "standing at the door", "end_pose": "sitting down",
                                  "gait": "slow steady steps", "camera_position": "low angle", "lens_mm": "85",
                                  "screen_direction": "left"}


def test_grammar_over_http_check_and_fix(client: TestClient, fsettings: Settings) -> None:
    pid = project(client)["id"]
    empty = client.get(f"/api/projects/{pid}/grammar")
    assert empty.status_code == 200 and empty.json()["shots"] == 0 and empty.json()["counts"] == {
        "error": 0, "warning": 0, "info": 0}
    fixed = client.post(f"/api/projects/{pid}/grammar/fix", json={})
    assert fixed.status_code == 200 and fixed.json()["fixed"] == []
    assert client.get("/api/projects/9999/grammar").status_code == 404


def test_the_cli_checks_and_repairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    from storyagent.cli import main
    from storyagent.config import get_settings

    monkeypatch.setenv("STORY_AGENT_HOME", str(tmp_path / "cli"))
    monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
    get_settings(reload=True)
    try:
        assert main(["db", "migrate"]) == 0 and main(["project", "create", "G"]) == 0
        capsys.readouterr()
        assert main(["grammar", "check", "--project", "1"]) == 0
        assert "0 shots checked" in capsys.readouterr().out
        assert main(["grammar", "fix", "--project", "1", "--rule", "establishing"]) == 0
        assert "Repaired 0" in capsys.readouterr().out
        assert main(["grammar", "check", "--project", "99"]) == 1
    finally:
        monkeypatch.undo()
        get_settings(reload=True)
