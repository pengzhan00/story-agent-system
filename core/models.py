"""
Story Agent System — Data Models
All asset types as Python dataclasses, serializable to/from SQLite.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List
from datetime import datetime, timezone
import json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


# ────────────────────────────────
#  Projects
# ────────────────────────────────

@dataclass
class Project:
    id: Optional[int] = None
    name: str = ""
    description: str = ""
    genre: str = ""                 # 玄幻 / 都市 / 仙侠 / ...
    status: str = "draft"           # draft | active | completed | archived
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


# ────────────────────────────────
#  Script / Story
# ────────────────────────────────

@dataclass
class Script:
    """A complete story script with multiple acts and metadata."""
    id: Optional[int] = None
    project_id: int = 0
    title: str = ""
    synopsis: str = ""              # 故事梗概
    acts: str = "[]"                # JSON: list of act objects
    total_scenes: int = 0
    word_count: int = 0
    status: str = "draft"           # draft | reviewing | finalized
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def get_acts(self) -> list:
        return json.loads(self.acts) if self.acts else []

    def set_acts(self, acts_list: list):
        self.acts = json.dumps(acts_list, ensure_ascii=False)
        self.total_scenes = sum(
            len(act.get("scenes", [])) for act in acts_list
        )


@dataclass
class Act:
    """One act / chapter of a story, containing multiple scenes."""
    number: int = 1
    title: str = ""
    summary: str = ""
    scenes: list = field(default_factory=list)  # list of Scene


@dataclass
class Scene:
    """A single scene — the atomic unit of production."""
    number: int = 1
    location: str = ""              # 场景名 (links to Scene asset)
    time_of_day: str = "白天"
    weather: str = "晴"
    mood: str = ""                  # 情绪基调
    characters: list = field(default_factory=list)  # character names
    dialogue: list = field(default_factory=list)    # list of DialogueLine
    narration: str = ""             # 旁白/动作描述
    camera_angle: str = "中景"      # 镜头角度建议
    bgm_mood: str = ""              # 配乐情绪建议

    def to_dict(self):
        return asdict(self)


@dataclass
class DialogueLine:
    character: str = ""
    line: str = ""
    emotion: str = "neutral"        # 情感指示
    action: str = ""                # 动作/表情指示


# ────────────────────────────────
#  Characters
# ────────────────────────────────

@dataclass
class Character:
    """Character asset — reusable across projects."""
    id: Optional[int] = None
    name: str = ""
    project_id: int = 0
    role: str = "主角"              # 主角 / 配角 / 反派 / ...
    age: str = ""
    gender: str = ""
    appearance: str = ""            # 外貌描述 (用于SDXL Prompt)
    personality: str = ""           # 性格特征
    background: str = ""            # 背景故事
    voice_profile: str = ""         # 音色描述
    relationships: str = "[]"       # JSON: list of {character, relation}
    lora_ref: str = ""              # LoRA文件名
    ip_ref_images: str = "[]"       # JSON: list of image paths
    prompt_template: str = ""       # 角色Prompt模板
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def get_relationships(self) -> list:
        return json.loads(self.relationships) if self.relationships else []

    def get_ip_refs(self) -> list:
        return json.loads(self.ip_ref_images) if self.ip_ref_images else []


# ────────────────────────────────
#  Scenes (as reusable asset)
# ────────────────────────────────

@dataclass
class SceneAsset:
    """Scene environment asset — reusable across projects."""
    id: Optional[int] = None
    name: str = ""
    project_id: int = 0
    description: str = ""           # 场景描述
    lighting: str = ""              # 光照方案
    color_palette: str = ""         # 色调方案
    atmosphere: str = ""            # 氛围描述
    ref_images: str = "[]"          # JSON: list of paths
    lora_ref: str = ""              # 场景LoRA
    prompt_template: str = ""       # 场景Prompt模板
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


# ────────────────────────────────
#  Music / Sound
# ────────────────────────────────

@dataclass
class MusicTheme:
    """Music asset — theme melodies and BGM."""
    id: Optional[int] = None
    project_id: int = 0
    name: str = ""
    type: str = "bgm"               # theme | bgm | sfx
    mood: str = ""                  # 情绪标签
    tempo: str = "中速"             # 速度
    instruments: str = ""           # 乐器描述
    key_signature: str = ""         # 调性
    description: str = ""
    file_path: str = ""             # 音频文件路径 (如果有)
    prompt_for_gen: str = ""        # 用于MusicGen/Suno的prompt
    created_at: str = field(default_factory=_now)


@dataclass
class SoundEffect:
    """Sound effect asset."""
    id: Optional[int] = None
    project_id: int = 0
    name: str = ""
    category: str = "环境"          # 环境 / 动作 / 情绪 / 过渡
    description: str = ""
    file_path: str = ""
    tags: str = ""                  # 逗号分隔标签
    created_at: str = field(default_factory=_now)


# ────────────────────────────────
#  Prompt Templates
# ────────────────────────────────

@dataclass
class PromptTemplate:
    """Reusable prompt template for any agent."""
    id: Optional[int] = None
    name: str = ""
    agent_type: str = ""            # screenwriter | character | scene | ...
    category: str = "通用"
    content: str = ""               # Template with {{variables}}
    variables: str = "[]"           # JSON: list of variable names
    description: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


# ────────────────────────────────
#  Generation Logs
# ────────────────────────────────

@dataclass
class GenerationLog:
    """Audit trail of every generation."""
    id: Optional[int] = None
    project_id: int = 0
    agent_type: str = ""
    model: str = ""
    prompt: str = ""
    response: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    created_at: str = field(default_factory=_now)


# ────────────────────────────────
#  Production pipeline entities
# ────────────────────────────────

@dataclass
class Episode:
    """Production episode / chapter container."""
    id: Optional[int] = None
    project_id: int = 0
    number: int = 1
    title: str = ""
    summary: str = ""
    status: str = "draft"           # draft | planned | rendering | reviewed | exported
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class Shot:
    """Atomic production shot used by storyboard and rendering."""
    id: Optional[int] = None
    project_id: int = 0
    episode_id: int = 0
    script_id: int = 0
    act_number: int = 1
    scene_number: int = 1
    shot_number: int = 1
    location: str = ""
    shot_type: str = "中景"
    mood: str = ""
    time_of_day: str = "白天"
    weather: str = "晴"
    characters: str = "[]"          # JSON list
    narration: str = ""
    dialogue: str = "[]"            # JSON list
    camera_notes: str = ""
    visual_prompt: str = ""
    render_payload: str = "{}"      # JSON dict
    status: str = "draft"           # draft | ready | rendering | rendered | approved | rejected
    locked: int = 0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class RenderJob:
    """Track individual render attempts for a shot."""
    id: Optional[int] = None
    project_id: int = 0
    shot_id: int = 0
    status: str = "queued"          # queued | running | completed | failed
    model_name: str = ""
    workflow_name: str = ""
    prompt_id: str = ""
    output_path: str = ""
    error: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
