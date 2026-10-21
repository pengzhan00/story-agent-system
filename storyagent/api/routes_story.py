"""The story as data: characters, locations, scripts, shots, dialogue, music themes, sound effects.

Bodies are partial field dicts validated by ``StoryStore`` (unknown fields are a 422); every row belongs to a
project and is only reachable through it.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from storyagent.story import StoryNotFound

from .context import ctx_of
from .schemas import LockBody, SpeakerBody

router = APIRouter(prefix="/api/projects/{project_id}", tags=["story"])
Row = dict[str, Any]


def _owned(row: Row, project_id: int) -> Row:
    if row["project_id"] != project_id:
        raise StoryNotFound(f"unknown item {row['id']}")
    return row


def _crud(path: str, name: str, getter: str, lister: str, creator: str | None, updater: str | None,
          deleter: str | None) -> None:
    """Register list / create / get / patch / delete for one entity from the store's method names."""
    def store(request: Request) -> Any:
        return ctx_of(request).story

    def list_(project_id: int, request: Request) -> list[Row]:
        ctx_of(request).projects.get(project_id)
        rows: list[Row] = getattr(store(request), lister)(project_id)
        return rows

    list_.__name__ = f"list_{name}"
    router.get(f"/{path}", name=f"list_{name}")(list_)

    if creator:
        def create(project_id: int, body: Row, request: Request) -> Row:
            ctx_of(request).projects.get(project_id)
            created: Row = getattr(store(request), creator)(project_id, body)
            return created

        router.post(f"/{path}", status_code=201, name=f"create_{name}")(create)

    def get(project_id: int, item_id: int, request: Request) -> Row:
        return _owned(getattr(store(request), getter)(item_id), project_id)

    router.get(f"/{path}/{{item_id}}", name=f"get_{name}")(get)

    if updater:
        def patch(project_id: int, item_id: int, body: Row, request: Request) -> Row:
            _owned(getattr(store(request), getter)(item_id), project_id)
            updated: Row = getattr(store(request), updater)(item_id, body)
            return updated

        router.patch(f"/{path}/{{item_id}}", name=f"update_{name}")(patch)

    if deleter:
        def delete(project_id: int, item_id: int, request: Request) -> None:
            _owned(getattr(store(request), getter)(item_id), project_id)
            getattr(store(request), deleter)(item_id)

        router.delete(f"/{path}/{{item_id}}", status_code=204, name=f"delete_{name}")(delete)


_crud("characters", "character", "get_character", "list_characters", "create_character", "update_character",
      "delete_character")
_crud("locations", "location", "get_location", "list_locations", "create_location", "update_location",
      "delete_location")
_crud("music", "music", "get_music", "list_music", "create_music", "update_music", "delete_music")
_crud("sfx", "sfx", "get_sfx", "list_sfx", "create_sfx", None, "delete_sfx")


@router.get("/episodes")
def list_episodes(project_id: int, request: Request) -> list[Row]:
    ctx_of(request).projects.get(project_id)
    return ctx_of(request).story.list_episodes(project_id)


@router.patch("/episodes/{episode_id}")
def update_episode(project_id: int, episode_id: int, body: Row, request: Request) -> Row:
    story = ctx_of(request).story
    if all(e["id"] != episode_id for e in story.list_episodes(project_id)):
        raise StoryNotFound(f"unknown episode {episode_id}")
    return story.update_episode(episode_id, body)


@router.get("/seasons")
def list_seasons(project_id: int, request: Request) -> list[Row]:
    """Seasons (a project always has season 1) with how many episodes each holds."""
    ctx_of(request).projects.get(project_id)
    return ctx_of(request).story.list_seasons(project_id)


@router.patch("/seasons/{number}")
def update_season(project_id: int, number: int, body: Row, request: Request) -> Row:
    """Edit a season (title, summary, planned_episodes, target_sec); creates it when it does not exist yet."""
    ctx_of(request).projects.get(project_id)
    return ctx_of(request).story.update_season(project_id, number, body)


@router.get("/scripts")
def list_scripts(project_id: int, request: Request) -> list[Row]:
    ctx_of(request).projects.get(project_id)
    return ctx_of(request).story.list_scripts(project_id)


@router.get("/scripts/{episode_no}")
def get_script(project_id: int, episode_no: int, request: Request) -> Row:
    script = ctx_of(request).story.get_script(project_id, episode_no)
    if script is None:
        raise StoryNotFound(f"no script for episode {episode_no}")
    return script


@router.patch("/scripts/{episode_no}/acts/{act_number}/scenes/{scene_number}")
def update_scene(project_id: int, episode_no: int, act_number: int, scene_number: int, body: Row,
                 request: Request) -> Row:
    """Edit one scene of the script (title, description, dialogue ...). Shots are re-planned from it afterwards."""
    return ctx_of(request).story.update_script_scene(project_id, episode_no, act_number, scene_number, body)


@router.get("/shots")
def list_shots(project_id: int, request: Request, episode_id: int | None = None) -> list[Row]:
    ctx_of(request).projects.get(project_id)
    return ctx_of(request).story.list_shots(project_id, episode_id)


def _shot(request: Request, project_id: int, shot_id: int) -> Row:
    return _owned(ctx_of(request).story.get_shot(shot_id), project_id)


@router.get("/shots/{shot_id}")
def get_shot(project_id: int, shot_id: int, request: Request) -> Row:
    return _shot(request, project_id, shot_id)


@router.patch("/shots/{shot_id}")
def update_shot(project_id: int, shot_id: int, body: Row, request: Request) -> Row:
    """409 ``shot_locked`` when the shot is locked (unlock it first)."""
    _shot(request, project_id, shot_id)
    return ctx_of(request).story.update_shot(shot_id, body)


@router.put("/shots/{shot_id}/lock")
def lock_shot(project_id: int, shot_id: int, body: LockBody, request: Request) -> Row:
    _shot(request, project_id, shot_id)
    return ctx_of(request).story.set_shot_lock(shot_id, body.locked)


@router.delete("/shots/{shot_id}", status_code=204)
def delete_shot(project_id: int, shot_id: int, request: Request) -> None:
    _shot(request, project_id, shot_id)
    ctx_of(request).story.delete_shot(shot_id)


@router.get("/dialogue")
def list_dialogue(project_id: int, request: Request, episode_id: int | None = None) -> list[Row]:
    """Every spoken line; ``character_id`` is null when the speaker matches no character (reassign or create one)."""
    ctx_of(request).projects.get(project_id)
    return ctx_of(request).story.dialogue_lines(project_id, episode_id)


@router.put("/shots/{shot_id}/dialogue/{index}/speaker")
def reassign_speaker(project_id: int, shot_id: int, index: int, body: SpeakerBody, request: Request) -> Row:
    _shot(request, project_id, shot_id)
    return ctx_of(request).story.reassign_speaker(shot_id, index, body.character)


