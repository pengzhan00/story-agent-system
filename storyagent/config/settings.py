"""Settings loader.

Resolution order (later wins):
  1. packaged defaults   storyagent/config/defaults/*.toml
  2. user overrides      <config_dir>/*.toml   (config_dir = $STORY_AGENT_CONFIG_DIR or <home>/config)
  3. environment         STORY_AGENT_HOME, STORY_AGENT_LOCALE, STORY_AGENT_<SERVICE>_URL,
                         STORY_AGENT_LLM_UNLOAD_POLICY, STORY_AGENT_COMPLIANCE, STORY_AGENT_LEGACY_DB
  4. per project         ``projects.settings`` JSON (compliance, models.<ref> params) — applied by
                         ``compliance_for`` / ``media.params.resolve_params``

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

from storyagent.domain import MediaKind, QualityTier

ENV_HOME = "STORY_AGENT_HOME"
ENV_CONFIG_DIR = "STORY_AGENT_CONFIG_DIR"
ENV_LOCALE = "STORY_AGENT_LOCALE"
ENV_LEGACY_DB = "STORY_AGENT_LEGACY_DB"
ENV_UNLOAD = "STORY_AGENT_LLM_UNLOAD_POLICY"
ENV_COMPLIANCE = "STORY_AGENT_COMPLIANCE"

DEFAULT_LOCALE = "zh-CN"
UNLOAD_POLICIES = ("never", "ask", "auto")
EXPLICIT_LABEL_MODES = ("off", "overlay", "both")
LLM_BACKEND_KINDS = ("ollama", "llamacpp", "openai_compat")
_CONFIG_FILES = ("services", "models", "delivery", "compliance", "recipes", "llm")
_VALID_KINDS = frozenset(k.value for k in MediaKind)
_VALID_TIERS = frozenset(t.value for t in QualityTier)
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """Raised for invalid configuration; message is developer-facing."""


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    url: str
    health_path: str = "/"
    probe_only: bool = False
    enabled: bool = True

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
    prompt_profile: str = "plain"
    timeout_sec: int = 3600
    params: Mapping[str, Any] = field(default_factory=dict)
    tiers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)  # e.g. train_template


@dataclass(frozen=True)
class ComplianceConfig:
    enabled: bool = True
    implicit_metadata: bool = True
    explicit_label: str = "off"
    explicit_label_key: str = "compliance.label.aigc"
    require_voice_consent: bool = True
    require_restricted_license_ack: bool = True


@dataclass(frozen=True)
class RecipeStep:
    kind: str
    model: str = ""  # empty => default model for the kind
    tier: str = ""  # empty => decided by the caller
    slot: str = "main"
    when: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Recipe:
    name: str
    label_key: str
    steps: tuple[RecipeStep, ...]


@dataclass(frozen=True)
class LLMBackend:
    name: str
    kind: str
    url: str
    service: str = ""  # linked [services.<name>] when the URL comes from there
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMRole:
    name: str
    backend: str
    model: str
    params: Mapping[str, Any] = field(default_factory=dict)


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
    compliance: ComplianceConfig
    recipes: Mapping[str, Recipe]
    llm_backends: Mapping[str, LLMBackend]
    llm_roles: Mapping[str, LLMRole]
    healthcheck_required: tuple[str, ...]
    healthcheck_min_free_gib: float = 50.0
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

    def recipe(self, name: str) -> Recipe:
        try:
            return self.recipes[name]
        except KeyError:
            raise ConfigError(f"unknown recipe: {name!r}") from None

    def llm_role(self, role: str) -> LLMRole:
        """Role routing with fallback to the ``default`` role."""
        return self.llm_roles.get(role) or self.llm_roles["default"]

    @property
    def llm_unload_policy(self) -> str:
        return str(self.runtime["llm_unload_policy"])

    @property
    def strict_params(self) -> bool:
        return bool(self.runtime.get("strict_params", False))

    def delivery_profile(self, name: str | None = None) -> Mapping[str, Any]:
        profiles = self.delivery["profiles"]
        name = name or self.delivery["default"]["profile"]
        try:
            return profiles[name]
        except KeyError:
            raise ConfigError(f"unknown delivery profile: {name!r}") from None

    def compliance_for(self, project_settings: Mapping[str, Any] | None = None) -> ComplianceConfig:
        """Global config, overridden by ``projects.settings['compliance']``.

        The environment switch ``STORY_AGENT_COMPLIANCE`` was folded into the global value at load time;
        a project may still opt out/in explicitly (the project is the more specific scope).
        """
        override = (project_settings or {}).get("compliance")
        if not override:
            return self.compliance
        known = {f for f in ComplianceConfig.__dataclass_fields__}
        unknown = set(override) - known
        if unknown:
            raise ConfigError(f"unknown project compliance keys: {sorted(unknown)}")
        merged = {**self.compliance.__dict__, **override}
        return _build_compliance(merged)


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


def deep_merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


_MODEL_KNOWN = {
    "kind", "provider", "version", "family", "heavy", "est_memory_gib", "license_tag",
    "commercial_ok", "attribution", "prompt_profile", "timeout_sec", "params", "tiers",
}


def _build_models(raw: Mapping[str, Any]) -> dict[str, ModelSpec]:
    out: dict[str, ModelSpec] = {}
    for ref, spec in raw.get("models", {}).items():
        for required in ("kind", "provider"):
            if required not in spec:
                raise ConfigError(f"model {ref!r} is missing {required!r}")
        if spec["kind"] not in _VALID_KINDS:
            raise ConfigError(f"model {ref!r}: unknown kind {spec['kind']!r}")
        tiers = spec.get("tiers", {})
        bad_tiers = set(tiers) - _VALID_TIERS
        if bad_tiers:
            raise ConfigError(f"model {ref!r}: unknown tiers {sorted(bad_tiers)}")
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
            prompt_profile=str(spec.get("prompt_profile", "plain")),
            timeout_sec=int(spec.get("timeout_sec", 3600)),
            params=dict(spec.get("params", {})),
            tiers={t: dict(v) for t, v in tiers.items()},
            extra={k: v for k, v in spec.items() if k not in _MODEL_KNOWN},
        )
    return out


def _build_compliance(raw: Mapping[str, Any]) -> ComplianceConfig:
    cfg = ComplianceConfig(
        enabled=bool(raw.get("enabled", True)),
        implicit_metadata=bool(raw.get("implicit_metadata", True)),
        explicit_label=str(raw.get("explicit_label", "off")),
        explicit_label_key=str(raw.get("explicit_label_key", "compliance.label.aigc")),
        require_voice_consent=bool(raw.get("require_voice_consent", True)),
        require_restricted_license_ack=bool(raw.get("require_restricted_license_ack", True)),
    )
    if cfg.explicit_label not in EXPLICIT_LABEL_MODES:
        raise ConfigError(f"compliance.explicit_label must be one of {EXPLICIT_LABEL_MODES}")
    return cfg


def _parse_switch(name: str, value: str) -> bool:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ConfigError(f"{name} must be on/off (got {value!r})")


def _build_recipes(
    raw: Mapping[str, Any], models: Mapping[str, ModelSpec], defaults: Mapping[str, str]
) -> dict[str, Recipe]:
    out: dict[str, Recipe] = {}
    for name, spec in raw.get("recipes", {}).items():
        steps: list[RecipeStep] = []
        for i, st in enumerate(spec.get("steps", [])):
            where = f"recipe {name!r} step {i + 1}"
            kind = st.get("kind", "")
            if kind not in _VALID_KINDS:
                raise ConfigError(f"{where}: unknown kind {kind!r}")
            model = st.get("model", "")
            if model:
                if model not in models:
                    raise ConfigError(f"{where}: unknown model {model!r}")
                if models[model].kind != kind:
                    raise ConfigError(f"{where}: model {model!r} is {models[model].kind!r}, not {kind!r}")
            elif kind not in defaults:
                raise ConfigError(f"{where}: no model given and no default model for {kind!r}")
            tier = st.get("tier", "")
            if tier and tier not in _VALID_TIERS:
                raise ConfigError(f"{where}: unknown tier {tier!r}")
            steps.append(
                RecipeStep(kind, model, tier, st.get("slot", "main"), st.get("when", ""), dict(st.get("params", {})))
            )
        if not steps:
            raise ConfigError(f"recipe {name!r} has no steps")
        out[name] = Recipe(name, spec.get("label_key", f"recipe.{name}"), tuple(steps))
    return out


def _build_llm(
    raw: Mapping[str, Any], services: Mapping[str, ServiceConfig]
) -> tuple[dict[str, LLMBackend], dict[str, LLMRole]]:
    backends: dict[str, LLMBackend] = {}
    for name, spec in raw.get("llm", {}).get("backends", {}).items():
        kind = spec.get("kind", "")
        if kind not in LLM_BACKEND_KINDS:
            raise ConfigError(f"llm backend {name!r}: kind must be one of {LLM_BACKEND_KINDS}")
        service = spec.get("service", "")
        if service:
            if service not in services:
                raise ConfigError(f"llm backend {name!r}: unknown service {service!r}")
            url = services[service].url
        else:
            url = str(spec.get("url", "")).rstrip("/")
            if not url:
                raise ConfigError(f"llm backend {name!r}: needs `service` or `url`")
        options = {k: v for k, v in spec.items() if k not in ("kind", "service", "url")}
        backends[name] = LLMBackend(name, kind, url, service, options)
    roles: dict[str, LLMRole] = {}
    for name, spec in raw.get("llm", {}).get("roles", {}).items():
        if spec.get("backend") not in backends:
            raise ConfigError(f"llm role {name!r}: unknown backend {spec.get('backend')!r}")
        if not spec.get("model"):
            raise ConfigError(f"llm role {name!r}: model is required")
        roles[name] = LLMRole(name, spec["backend"], spec["model"], dict(spec.get("params", {})))
    if "default" not in roles:
        raise ConfigError("llm.roles.default is required")
    return backends, roles


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    home = Path(env[ENV_HOME]).expanduser() if env.get(ENV_HOME) else default_home()
    config_dir = (
        Path(env[ENV_CONFIG_DIR]).expanduser() if env.get(ENV_CONFIG_DIR) else home / "config"
    )

    merged = {
        name: deep_merge(_read_default(name), _read_override(config_dir, name))
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
            enabled=bool(spec.get("enabled", True)),
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
        runtime["llm_unload_policy"] = env[ENV_UNLOAD]
    if runtime.get("llm_unload_policy") not in UNLOAD_POLICIES:
        raise ConfigError(
            f"llm_unload_policy must be one of {UNLOAD_POLICIES}, got {runtime.get('llm_unload_policy')!r}"
        )

    delivery = merged["delivery"]
    if delivery["default"]["profile"] not in delivery["profiles"]:
        raise ConfigError(f"default delivery profile {delivery['default']['profile']!r} not defined")

    compliance_raw = dict(merged["compliance"].get("compliance", {}))
    if env.get(ENV_COMPLIANCE):
        compliance_raw["enabled"] = _parse_switch(ENV_COMPLIANCE, env[ENV_COMPLIANCE])
    compliance = _build_compliance(compliance_raw)

    tools_cfg = merged["services"].get("tools", {})
    health_cfg = merged["services"].get("healthcheck", {})
    required = tuple(health_cfg.get("required", ()))
    known_entries = set(services) | {f"tool.{t}" for t in tools_cfg}
    unknown_required = [r for r in required if r not in known_entries]
    if unknown_required:
        raise ConfigError(f"[healthcheck] required lists unknown entries: {unknown_required}")

    backends, roles = _build_llm(merged["llm"], services)
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
        compliance=compliance,
        recipes=_build_recipes(merged["recipes"], models, defaults),
        llm_backends=backends,
        llm_roles=roles,
        healthcheck_required=required,
        healthcheck_min_free_gib=float(health_cfg.get("min_free_gib", 50)),
        legacy_db_path=Path(legacy).expanduser() if legacy else None,
    )


_cached: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    """Process-wide settings (env is read once; tests pass ``reload=True``)."""
    global _cached
    if _cached is None or reload:
        _cached = load_settings()
    return _cached
