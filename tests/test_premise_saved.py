"""A premise a run is started with becomes the project's story setting when the project has none."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from storyagent.workflow.stages import StageError, _premise


class _Projects:
    def __init__(self) -> None:
        self.updates: list[tuple[int, dict[str, Any]]] = []

    def update(self, pid: int, **changes: Any) -> None:
        self.updates.append((pid, changes))


def _ctx(params: dict[str, Any], description: str) -> Any:
    return SimpleNamespace(params=params, project=SimpleNamespace(description=description), pid=7,
                           projects=_Projects())


def test_a_given_premise_is_kept_as_the_project_description() -> None:
    ctx = _ctx({"premise": " 一个女刀客 "}, "")
    assert _premise(ctx) == "一个女刀客"
    assert ctx.projects.updates == [(7, {"description": "一个女刀客"})]


def test_an_existing_description_is_not_overwritten() -> None:
    ctx = _ctx({"premise": "other"}, "mine")
    assert _premise(ctx) == "other" and ctx.projects.updates == []
    assert _premise(_ctx({}, "mine")) == "mine"


def test_no_premise_at_all_is_an_error() -> None:
    with pytest.raises(StageError):
        _premise(_ctx({}, ""))
