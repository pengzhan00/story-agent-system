"""Settings loader.

Resolution order (later wins):
  1. packaged defaults   storyagent/config/defaults/*.toml
  2. user overrides      <config_dir>/*.toml   (config_dir = $STORY_AGENT_CONFIG_DIR or <home>/config)
  3. environment         STORY_AGENT_HOME, STORY_AGENT_LOCALE, STORY_AGENT_<SERVICE>_URL,
                         STORY_AGENT_OLLAMA_UNLOAD_POLICY, STORY_AGENT_LEGACY_DB

No other module may hard-code a path or port; ask ``Settings`` instead.
"""
from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from storyagent.domain import MediaKind

ENV_HOME = "STORY_AGENT_HOME"
ENV_CONFIG_DIR = "STORY_AGENT_CONFIG_DIR"
ENV_LOCALE = "STORY_AGENT_LOCALE"
ENV_LEGACY_DB = "STORY_AGENT_LEGACY_DB"
ENV_UNLOAD = "STORY_AGENT_OLLAMA_UNLOAD_POLICY"

DEFAULT_LOCALE = "zh-CN"
UNLOAD_POLICIES = ("never", "ask", "auto")
_CONFIG_FILES = ("services", "models", "delivery")
_VALID_KINDS = frozenset(k.value for k in MediaKind)


class ConfigError(ValueError):
    """Raised for invalid configuration; message is developer-facing."""


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    url: str
    health_path: str = "/"
    probe_only: bool = False

    @property
    def health_url(self) -> str:
        return self.url.rstrip("/") + self.health_path


@dataclass(frozen=True)
class ModelSpec:
    ref: str
    kind: str
    provider: str
    version: str = ""
    family: str = ""
    heavy: bool = False
    est_memory_gib: float = 0.0
    license_tag: str = ""
    commercial_ok: bool = True
    attribution: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    home: Path
    config_dir: Path
    locale: str
    services: Mapping[str, ServiceConfig]
    tools: Mapping[str, Mapping[str, str]]
    models: Mapping[str, ModelSpec]
    default_models: Mapping[str, str]
    delivery: Mapping[str, Any]
    runtime: Mapping[str, Any]
    legacy_db_path: Path | None = None

    # -- derived paths ----------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.home / "storyagent.db"

    @property
    def projects_dir(self) -> Path:
        return self.home / "projects"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    def project_media_dir(self, project_slug: str, kind: str, shot_id: int | str | None = None) -> Path:
        """projects/<proj>/media/<kind>[/<shot_id>] — the single artifact root."""
        base = self.projects_dir / project_slug / "media" / kind
        return base / str(shot_id) if shot_id is not None else base

    # -- lookups ----------------------------------------------------------
    def service(self, name: str) -> ServiceConfig:
        try:
            return self.services[name]
        except KeyError:
            raise ConfigError(f"unknown service: {name!r}") from None

    def tool_path(self, tool: str, key: str) -> Path | None:
        """Expanded path for ``[tools.<tool>] <key>``; ``None`` when unset/empty."""
        raw = self.tools.get(tool, {}).get(key, "")
        return Path(raw).expanduser() if raw else None

    def model(self, ref: str) -> ModelSpec:
        try:
            return self.models[ref]
        except KeyError:
            raise ConfigError(f"unknown model ref: {ref!r}") from None

    def default_model(self, kind: str) -> ModelSpec:
        try:
            return self.model(self.default_models[kind])
        except KeyError:
            raise ConfigError(f"no default model configured for kind {kind!r}") from None

    @property
    def ollama_unload_policy(self) -> str:
        return str(self.runtime["ollama_unload_policy"])

    def delivery_profile(self, name: str | None = None) -> Mapping[str, Any]:
        profiles = self.delivery["profiles"]
        name = name or self.delivery["default"]["profile"]
        try:
            return profiles[name]
        except KeyError:
            raise ConfigError(f"unknown delivery profile: {name!r}") from None


# ---------------------------------------------------------------------------
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_home() -> Path:
    """<repo>/data when running from a checkout, else ~/.storyagent."""
    root = _repo_root()
    if (root / "pyproject.toml").exists():
        return root / "data"
    return Path.home() / ".storyagent"


def _read_default(name: str) -> dict[str, Any]:
    text = resources.files("storyagent.config").joinpath("defaults", f"{name}.toml").read_text("utf-8")
    return tomllib.loads(text)


def _read_override(config_dir: Path, name: str) -> dict[str, Any]:
    path = config_dir / f"{name}.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc


def _deep_merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


_MODEL_KNOWN = {
    "kind", "provider", "version", "family", "heavy", "est_memory_gib",
    "license_tag", "commercial_ok", "attribution",
}


def _build_models(raw: Mapping[str, Any]) -> dict[str, ModelSpec]:
    out: dict[str, ModelSpec] = {}
    for ref, spec in raw.get("models", {}).items():
        for required in ("kind", "provider"):
            if required not in spec:
                raise ConfigError(f"model {ref!r} is missing {required!r}")
        if spec["kind"] not in _VALID_KINDS:
            raise ConfigError(f"model {ref!r}: unknown kind {spec['kind']!r}")
        out[ref] = ModelSpec(
            ref=ref,
            kind=spec["kind"],
            provider=spec["provider"],
            version=str(spec.get("version", "")),
            family=str(spec.get("family", ref)),
            heavy=bool(spec.get("heavy", False)),
            est_memory_gib=float(spec.get("est_memory_gib", 0.0)),
            license_tag=str(spec.get("license_tag", "")),
            commercial_ok=bool(spec.get("commercial_ok", True)),
            attribution=str(spec.get("attribution", "")),
            extra={k: v for k, v in spec.items() if k not in _MODEL_KNOWN},
        )
    return out


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    home = Path(env[ENV_HOME]).expanduser() if env.get(ENV_HOME) else default_home()
    config_dir = (
        Path(env[ENV_CONFIG_DIR]).expanduser() if env.get(ENV_CONFIG_DIR) else home / "config"
    )

    merged = {
        name: _deep_merge(_read_default(name), _read_override(config_dir, name))
        for name in _CONFIG_FILES
    }

    services: dict[str, ServiceConfig] = {}
    for name, spec in merged["services"].get("services", {}).items():
        url = env.get(f"STORY_AGENT_{name.upper()}_URL") or spec["url"]
        services[name] = ServiceConfig(
            name=name,
            url=url.rstrip("/"),
            health_path=spec.get("health_path", "/"),
            probe_only=bool(spec.get("probe_only", False)),
        )

    models_raw = merged["models"]
    models = _build_models(models_raw)
    defaults = dict(models_raw.get("defaults", {}))
    for kind, ref in defaults.items():
        if kind not in _VALID_KINDS:
            raise ConfigError(f"[defaults] unknown media kind {kind!r}")
        if ref not in models:
            raise ConfigError(f"[defaults] {kind} -> unknown model ref {ref!r}")
        if models[ref].kind != kind:
            raise ConfigError(f"[defaults] {kind} -> {ref!r} is a {models[ref].kind!r} model")

    runtime = dict(merged["services"].get("runtime", {}))
    if env.get(ENV_UNLOAD):
        runtime["ollama_unload_policy"] = env[ENV_UNLOAD]
    if runtime.get("ollama_unload_policy") not in UNLOAD_POLICIES:
        raise ConfigError(
            f"ollama_unload_policy must be one of {UNLOAD_POLICIES}, "
            f"got {runtime.get('ollama_unload_policy')!r}"
        )

    delivery = merged["delivery"]
    if delivery["default"]["profile"] not in delivery["profiles"]:
        raise ConfigError(f"default delivery profile {delivery['default']['profile']!r} not defined")

    legacy = env.get(ENV_LEGACY_DB)
    return Settings(
        home=home,
        config_dir=config_dir,
        locale=env.get(ENV_LOCALE) or DEFAULT_LOCALE,
        services=services,
        tools={k: dict(v) for k, v in merged["services"].get("tools", {}).items()},
        models=models,
        default_models=defaults,
        delivery=delivery,
        runtime=runtime,
        legacy_db_path=Path(legacy).expanduser() if legacy else None,
    )


_cached: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    """Process-wide settings (env is read once; tests pass ``reload=True``)."""
    global _cached
    if _cached is None or reload:
        _cached = load_settings()
    return _cached
