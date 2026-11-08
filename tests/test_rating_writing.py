"""A rating can carry notes on how detailed to write (content.toml `writing` / `visual`); they reach the agents."""
from __future__ import annotations

import re
from pathlib import Path

from storyagent.agents.craft import NONE, writing_vars
from storyagent.agents.registry import load_specs
from storyagent.agents.runner import AgentRunner
from storyagent.config import load_settings


def test_the_notes_of_a_rating_reach_the_writers_and_the_prompt_writer(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "content.toml").write_text(
        '[content.ratings.restricted]\nwriting = "WRITE-DETAIL-NOTE"\nvisual = "VISUAL-DETAIL-NOTE"\n',
        encoding="utf-8")
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path)})
    plain = writing_vars(s, "drama_series", "", rating="general")
    adult = writing_vars(s, "drama_series", "", rating="restricted")
    assert "WRITE-DETAIL-NOTE" not in plain["form_guidance"] and plain["rating_visual"] == NONE
    assert adult["form_guidance"].endswith("WRITE-DETAIL-NOTE")                    # after the form's own rules
    assert s.kinds["drama_series"].writing.strip() in adult["form_guidance"]
    assert adult["rating_visual"] == "VISUAL-DETAIL-NOTE"
    assert writing_vars(s, "drama_series", "")["rating_visual"] == NONE            # no rating given: nothing added

    specs = load_specs(s)
    runner = AgentRunner(s, router=None, specs=specs)                              # type: ignore[arg-type]
    base = {k: "x" for k in specs["shot.visual"].inputs}
    base.update(adult)
    system, prompt = runner.render("shot.visual", base)
    assert "VISUAL-DETAIL-NOTE" in prompt and not re.search(r"\$[a-z_]+", system + prompt)
