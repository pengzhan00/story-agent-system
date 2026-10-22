"""Delivery platform presets: frame, loudness and size limits an episode is re-encoded to (``platforms.toml``)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

REFRAMES = ("crop", "pad", "blur")
SUBTITLES = ("sidecar", "none")


@dataclass(frozen=True)
class Platform:
    name: str
    label_key: str
    note_key: str
    width: int
    height: int
    fps: float
    reframe: str
    loudnorm_i: float
    loudnorm_tp: float
    loudnorm_lra: float
    crf: int
    max_mb: float
    max_duration_sec: float
    audio_bitrate: str
    subtitles: str
    cover_sec: float
    trailer_sec: float

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class Platforms:
    default: str = ""
    items: Mapping[str, Platform] = field(default_factory=dict)

    def get(self, name: str | None) -> Platform:
        key = name or self.default
        if key not in self.items:
            raise KeyError(key)
        return self.items[key]


def build_platforms(raw: Mapping[str, Any], error: type[Exception]) -> Platforms:
    items: dict[str, Platform] = {}
    for name, spec in raw.get("platforms", {}).items():
        reframe, subtitles = str(spec.get("reframe", "pad")), str(spec.get("subtitles", "sidecar"))
        if reframe not in REFRAMES:
            raise error(f"platforms.{name}.reframe must be one of {list(REFRAMES)}")
        if subtitles not in SUBTITLES:
            raise error(f"platforms.{name}.subtitles must be one of {list(SUBTITLES)}")
        width, height = int(spec["width"]), int(spec["height"])
        if width < 2 or height < 2 or width % 2 or height % 2:
            raise error(f"platforms.{name}: width and height must be even and positive (H.264, yuv420p)")
        items[name] = Platform(
            name=name, label_key=str(spec.get("label_key", f"platform.{name}")),
            note_key=str(spec.get("note_key", f"platform.{name}.note")), width=width, height=height,
            fps=float(spec.get("fps", 24)), reframe=reframe, loudnorm_i=float(spec.get("loudnorm_i", -16)),
            loudnorm_tp=float(spec.get("loudnorm_tp", -1.5)), loudnorm_lra=float(spec.get("loudnorm_lra", 11)),
            crf=int(spec.get("crf", 20)), max_mb=float(spec.get("max_mb", 0)),
            max_duration_sec=float(spec.get("max_duration_sec", 0)),
            audio_bitrate=str(spec.get("audio_bitrate", "192k")), subtitles=subtitles,
            cover_sec=float(spec.get("cover_sec", 1.0)), trailer_sec=float(spec.get("trailer_sec", 0)))
    default = str(raw.get("default", {}).get("platform", ""))
    if not items or default not in items:
        raise error(f"platforms.toml [default] platform {default!r} is not defined")
    return Platforms(default, items)
