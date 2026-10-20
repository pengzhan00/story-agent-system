"""A fake LLM that answers every agent from its output schema, so the story pipeline can run with no model."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from storyagent.agents import AgentRunner
from storyagent.config import Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.llm import ChatResult, LLMError, LLMRouter
from storyagent.workflow import WorkflowEngine

NAMES = ["Lin Wan", "Zhao Heng"]


class StoryClient:
    name = "story"

    def __init__(self) -> None:
        self.calls: list[str] = []          # which output shape was asked for, in order
        self.prompts: list[str] = []
        self.fail_on: dict[str, int] = {}   # shape -> fail when this many calls of that shape were made already
        self.unknown_speaker = False
        self.scenes_per_act = 2

    def list_models(self) -> list[str]:
        return ["fake-model"]

    @staticmethod
    def shape(schema: dict[str, Any] | None) -> str:
        keys = set((schema or {}).get("properties", {}))
        for marker, name in (("project_name", "plan"), ("primary_colors", "style"), ("acts", "outline"),
                             ("shots", "shots"), ("appearance", "character"), ("sound_effects", "sound"),
                             ("music_prompt", "music"), ("video_prompt", "visual"), ("score", "review"),
                             ("image_prompt", "location")):
            if marker in keys:
                return name
        return "?"

    def chat(self, model: str, system: str, prompt: str, **kw: Any) -> ChatResult:
        shape = self.shape(kw.get("schema"))
        self.calls.append(shape)
        self.prompts.append(prompt)
        if self.fail_on.get(shape) is not None and self.calls.count(shape) > self.fail_on[shape]:
            raise LLMError("unreachable", "fake outage", url="x")
        return ChatResult(__import__("json").dumps(getattr(self, f"_{shape}")(prompt)), model, 10, 20, 5)

    # -- canned answers ---------------------------------------------------------------------------
    def _plan(self, prompt: str) -> dict[str, Any]:
        return {"project_name": "Rain Night", "genre": "mystery", "tone": "tense", "summary": "A detective, a secret.",
                "characters": [{"name": n, "role": "lead", "brief": "x"} for n in NAMES],
                "locations": ["Office", "Pier"], "style_keywords": ["neon", "rain"]}

    def _style(self, prompt: str) -> dict[str, Any]:
        return {"name": "noir", "primary_colors": [{"name": "teal", "hex": "#088", "usage": "bg"}],
                "visual_style": "cinematic neon noir, rain", "negative_prompt": "blurry, text"}

    def _outline(self, prompt: str) -> dict[str, Any]:
        locs = ["Office", "Pier"]
        return {"title": "Rain Night", "synopsis": "Two people, one case.", "acts": [
            {"number": a, "title": f"Act {a}", "summary": "s", "scenes": [
                {"number": s, "location": locs[(a + s) % 2], "time_of_day": "night", "weather": "rain",
                 "characters": NAMES, "mood": "tense", "narration": "", "beat": f"beat {a}.{s}"}
                for s in range(1, self.scenes_per_act + 1)]} for a in (1, 2)]}

    def _shots(self, prompt: str) -> dict[str, Any]:
        who = "Nobody" if self.unknown_speaker else NAMES[0]
        return {"shots": [
            {"shot_number": 1, "camera_angle": "wide", "camera_movement": "push in", "characters_present": NAMES,
             "mood": "tense", "action": "walks in", "expression": "wary", "narration": "",
             "dialogue": [{"character": who, "line": "You are late.", "emotion": "cold"}], "duration_sec": 3,
             "sfx_cues": ["door"]},
            {"shot_number": 2, "camera_angle": "close-up", "camera_movement": "", "characters_present": [NAMES[1]],
             "mood": "tense", "action": "smiles", "expression": "calm", "narration": "A pause.",
             "dialogue": [], "duration_sec": 2, "sfx_cues": []}]}

    def _character(self, prompt: str) -> dict[str, Any]:
        return {"name": "x", "role": "lead", "age": "30", "gender": "f", "appearance": "short black hair, grey coat",
                "personality": "calm", "background": "b", "costume": "grey coat", "mannerism": "m",
                "voice_profile": "low, slow", "voice_sample_text": "It is raining again.", "relationships": [],
                "image_prompt": "a woman with short black hair in a grey coat",
                "prompt_template": "short black hair, grey coat"}

    def _location(self, prompt: str) -> dict[str, Any]:
        return {"name": "x", "description": "a rainy place", "lighting": "neon", "color_palette": "teal",
                "atmosphere": "wet", "weather": "rain", "key_elements": ["lamp"], "camera_notes": "wide",
                "image_prompt": "an empty rainy street at night", "prompt_template": "t {time_of_day}"}

    def _music(self, prompt: str) -> dict[str, Any]:
        return {"name": "Theme", "mood": "tense", "tempo": 90, "instruments": "piano, strings", "key_signature": "Dm",
                "description": "arc", "music_prompt": "cinematic, piano, strings, slow", "duration_hint_sec": 90,
                "lyrics": "[verse]\nrain falls\n[chorus]\nwe stay", "loopable": False}

    def _sound(self, prompt: str) -> dict[str, Any]:
        return {"sound_effects": [{"name": "rain", "category": "ambience", "description": "steady rain",
                                   "tags": "rain, ambience", "timing": "all"}]}

    def _visual(self, prompt: str) -> dict[str, Any]:
        return {"image_prompt": "a woman in a grey coat walks into a neon office",
                "video_prompt": "she walks in, slow push", "negative_prompt": "blurry"}

    def _review(self, prompt: str) -> dict[str, Any]:
        return {"score": 7, "summary": "fine", "issues": []}


def make_engine(
    tmp_path: Path, client: StoryClient | None = None, *, toml: str = "", services: str = ""
) -> tuple[WorkflowEngine, StoryClient, Settings]:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    if toml:
        (home / "config" / "models.toml").write_text(toml)
    if services:
        (home / "config" / "services.toml").write_text(services)
    settings = load_settings({"STORY_AGENT_HOME": str(home), "STORY_AGENT_LOCALE": "en"})
    db = Database(settings.db_path)
    migrate(db)
    client = client or StoryClient()
    router = LLMRouter(settings, db, clients={"ollama": client})
    engine = WorkflowEngine(settings, db, router, agents=AgentRunner(settings, router))
    return engine, client, settings


def new_project(engine: WorkflowEngine, premise: str = "A detective hunts a secret in the rain.") -> int:
    return engine.projects.create("Rain Night", description=premise).id
