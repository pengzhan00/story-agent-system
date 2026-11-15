"""A rewrite of a scene is a proposal first: listed, read side by side with what is there, then taken or left.

Before this, a revision (the review's notes, the editor's own words, the logic reader's findings) wrote the new
shots straight over the old ones: nobody saw what changed, and a rewrite that lost a good line could not be taken
back. Now every rewrite of a written scene is kept here with the shots it replaces:

* ``pending``: written, not in the script. ``decide(accept=...)`` puts it in, ``discard`` drops it.
* ``applied``: in the script (a run to the film applies the logic reader's rewrites itself, so it does not stop
  for a person). ``decide(revert=...)`` puts the old shots back.
* ``discarded`` / ``reverted``: kept for the record.

A proposal is only applied (or taken back) while the scene still reads as it did when the proposal was written:
shots a person edited meanwhile are never overwritten, the proposal is reported as ``conflict`` instead.

Stored with the workflow's small results (``workflow_data`` key ``revisions:<episode>``), newest first, the last
``KEEP`` entries. The shots are the script's own structures; ``script_text.shots_text`` renders them for reading.
"""
from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any

from storyagent.story import StoryStore
from storyagent.util import new_id, now_iso

from .state import WorkflowState

KEEP = 60
PENDING, APPLIED, DISCARDED, REVERTED = "pending", "applied", "discarded", "reverted"
# what a shot says and shows: the fields a rewrite changes (cards and numbers added later are not part of it)
CONTENT = ("action", "start_pose", "end_pose", "narration", "expression", "dialogue", "camera_angle",
           "camera_movement")


def essence(shots: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The shots reduced to what a rewrite is about, to tell whether a scene still reads the same."""
    return [{k: shot.get(k) or "" for k in CONTENT} for shot in shots or []]


def _key(episode: int) -> str:
    return f"revisions:{episode}"


def listed(state: WorkflowState, project_id: int, episode: int) -> list[dict[str, Any]]:
    """Every proposal of the episode, newest first."""
    return list(state.get_data(project_id, _key(episode), []) or [])


def pending(state: WorkflowState, project_id: int, episode: int) -> list[dict[str, Any]]:
    return [r for r in listed(state, project_id, episode) if r["status"] == PENDING]


def propose(
    state: WorkflowState, project_id: int, episode: int, *, scene: str, source: str, notes: str,
    before: Sequence[dict[str, Any]], after: Sequence[dict[str, Any]], status: str = PENDING,
) -> dict[str, Any] | None:
    """Keep a rewrite of ``scene`` ("act.scene"). ``source``: ``review`` (the review's notes a person ticked),
    ``own`` (their own words), ``logic`` (the logic reader of the script stage). A rewrite that changes nothing is
    not kept; a pending older proposal for the same scene is replaced by the newer one."""
    if essence(before) == essence(after):
        return None
    entry = {"id": new_id()[:12], "episode": episode, "scene": scene, "source": source, "status": status,
             "notes": notes, "before": list(before), "after": list(after), "created_at": now_iso(),
             "decided_at": None}
    rest = [r for r in listed(state, project_id, episode) if not (r["scene"] == scene and r["status"] == PENDING)]
    state.put_data(project_id, _key(episode), [entry, *rest][:KEEP])
    return entry


def _scene(script: dict[str, Any] | None, key: str) -> tuple[int, int, dict[str, Any]] | None:
    act_no, _, scene_no = key.partition(".")
    for act in (script or {}).get("acts") or []:
        for scene in act.get("scenes", []):
            if str(act.get("number")) == act_no and str(scene.get("number")) == scene_no:
                return int(act["number"]), int(scene["number"]), scene
    return None


def decide(
    store: StoryStore, state: WorkflowState, project_id: int, episode: int, *, accept: Collection[str] = (),
    discard: Collection[str] = (), revert: Collection[str] = (),
) -> dict[str, list[str]]:
    """Take (``accept``), leave (``discard``) or take back (``revert``) proposals by id. Returns the scenes that
    were ``applied``, ``discarded``, ``reverted``, and the ``conflicts``: proposals whose scene no longer reads as
    it did when they were written (nothing is overwritten for those; they are discarded)."""
    out: dict[str, list[str]] = {"applied": [], "discarded": [], "reverted": [], "conflicts": []}
    entries = listed(state, project_id, episode)
    for entry in reversed(entries):                      # oldest first: a later rewrite of a scene builds on it
        ident, status = entry["id"], entry["status"]
        swap = ((entry["before"], entry["after"], APPLIED) if ident in accept and status == PENDING else
                (entry["after"], entry["before"], REVERTED) if ident in revert and status == APPLIED else None)
        if swap is None:
            if ident in discard and status == PENDING:
                entry.update(status=DISCARDED, decided_at=now_iso())
                out["discarded"].append(entry["scene"])
            continue
        expected, wanted, done = swap
        found = _scene(store.get_script(project_id, episode), entry["scene"])
        if found is None or essence(found[2].get("shots")) != essence(expected):
            entry.update(status=DISCARDED, decided_at=now_iso())
            out["conflicts"].append(entry["scene"])
            continue
        store.update_script_scene(project_id, episode, found[0], found[1], {"shots": wanted})
        entry.update(status=done, decided_at=now_iso())
        out["applied" if done == APPLIED else "reverted"].append(entry["scene"])
    state.put_data(project_id, _key(episode), entries)
    return out
