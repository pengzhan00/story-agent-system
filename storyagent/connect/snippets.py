"""Connector models written as text: check a ``[models.X]`` (and ``[services.Y]``) snippet and add it to the operator's
own ``models.toml`` / ``services.toml``. The web page "Tools" edits exactly this text; nothing is hidden from the file.

``check_snippet`` is a dry run: besides the structure of the connector table it builds the whole configuration with the
snippet applied in a scratch copy of the config folder, so what the real loader would refuse is reported here.
"""
from __future__ import annotations

import shutil
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storyagent.config import ConfigError, Settings, load_settings
from storyagent.domain import MediaKind

from .spec import GENERIC_KINDS
from .store import REF, ConnectError, without_table

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATES = ("cli", "http_sync", "http_async")
PROVIDERS = ("cli", "http", "comfy")
URL_KEYS = ("url_path", "b64_path", "path", "content")


@dataclass(frozen=True)
class Issue:
    level: str                                  # "error" | "warning"
    code: str                                   # i18n key suffix: connect.issue.<code>
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Checked:
    ref: str
    kind: str
    provider: str
    issues: list[Issue]

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)


def template(name: str) -> tuple[str, str]:
    """(models text, services text) of a starting point."""
    if name not in TEMPLATES:
        raise ConnectError("unknown_template", name=name)
    models = (TEMPLATE_DIR / f"{name}.models.toml").read_text("utf-8")
    services_file = TEMPLATE_DIR / f"{name}.services.toml"
    return models, services_file.read_text("utf-8") if services_file.is_file() else ""


def _parse(text: str, root: str, issues: list[Issue]) -> dict[str, Any]:
    try:
        data = tomllib.loads(text) if text.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        issues.append(Issue("error", "toml_syntax", {"file": root, "detail": str(exc)[:200]}))
        return {}
    extra = set(data) - {root}
    if extra:
        issues.append(Issue("error", "only_root", {"file": root, "root": root}))
    table = data.get(root, {})
    return dict(table) if isinstance(table, dict) else {}


def _structure(ref: str, model: Mapping[str, Any], known_services: set[str], settings: Settings,
               issues: list[Issue]) -> None:
    provider = str(model.get("provider", ""))
    if provider not in PROVIDERS:
        issues.append(Issue("error", "provider", {"provider": provider, "allowed": ", ".join(PROVIDERS)}))
        return
    try:
        kind = MediaKind(str(model.get("kind", "")))
        if kind not in GENERIC_KINDS:
            raise ValueError(kind)
    except ValueError:
        issues.append(Issue("error", "kind", {"kind": str(model.get("kind", ""))}))
    if not model.get("license_tag"):
        issues.append(Issue("warning", "license_empty"))
    conn = model.get("connector")
    if provider == "comfy":
        name = str(model.get("workflow", ""))
        from ..media.adapters.comfy import WORKFLOWS

        if not name or not any((d / f"{name}.json").is_file() for d in (settings.config_dir / "workflows", WORKFLOWS)):
            issues.append(Issue("error", "comfy_workflow", {"name": name}))
        return
    if not isinstance(conn, dict) or not conn:
        issues.append(Issue("error", "no_connector", {"ref": ref}))
        return
    if provider == "cli":
        argv = conn.get("argv")
        if not isinstance(argv, list) or not argv or not isinstance(argv[0], str):
            issues.append(Issue("error", "cli_argv"))
        return
    service = str(conn.get("service", ""))
    if not service or service not in known_services:
        issues.append(Issue("error", "http_service", {"service": service}))
    mode = str(conn.get("mode", "async"))
    submit, poll, result = conn.get("submit"), conn.get("poll"), conn.get("result")
    if mode not in ("sync", "async"):
        issues.append(Issue("error", "http_mode", {"mode": mode}))
    if not isinstance(submit, dict) or not submit.get("path"):
        issues.append(Issue("error", "http_submit"))
    elif mode == "async" and not (submit.get("id_path") and isinstance(poll, dict) and poll.get("path")):
        issues.append(Issue("error", "http_async"))
    if not isinstance(result, dict) or not any(k in result for k in URL_KEYS):
        issues.append(Issue("error", "http_result"))


def _join(base: str, addition: str) -> str:
    if not addition.strip():
        return base
    return (base.rstrip("\n") + "\n\n" if base.strip() else "") + addition.strip() + "\n"


def _apply(current_models: str, current_services: str, models: Mapping[str, Any], models_text: str,
           services: Mapping[str, Any], services_text: str) -> tuple[str, str]:
    """The two files with the snippet's tables replacing tables of the same name."""
    new_models, new_services = current_models, current_services
    for ref in models:
        new_models = without_table(new_models, "models", ref)[0]
    for name in services:
        new_services = without_table(new_services, "services", name)[0]
    return _join(new_models, models_text), _join(new_services, services_text)


def _read(path: Path) -> str:
    return path.read_text("utf-8") if path.is_file() else ""


def check_snippet(settings: Settings, models_text: str, services_text: str = "", *, force: bool = False) -> Checked:
    issues: list[Issue] = []
    models = _parse(models_text, "models", issues)
    services = _parse(services_text, "services", issues)
    ref, kind, provider = "", "", ""
    if len(models) != 1:
        if not any(i.code == "toml_syntax" for i in issues):
            issues.append(Issue("error", "one_model", {"n": len(models)}))
    else:
        [(ref, raw)] = models.items()
        model = raw if isinstance(raw, dict) else {}
        kind, provider = str(model.get("kind", "")), str(model.get("provider", ""))
        if not REF.match(ref):
            issues.append(Issue("error", "bad_ref", {"ref": ref}))
        mine = without_table(_read(settings.config_dir / "models.toml"), "models", ref)[1]
        if ref in settings.models and not mine:
            issues.append(Issue("error", "ref_packaged", {"ref": ref}))
        elif mine:
            issues.append(Issue("warning" if force else "error", "exists" if not force else "will_replace",
                                {"ref": ref}))
        _structure(ref, model, set(settings.services) | set(services), settings, issues)
    if not any(i.level == "error" for i in issues):
        issues += _load_check(settings, models_text, services_text, models, services)
    return Checked(ref, kind, provider, issues)


def _load_check(settings: Settings, models_text: str, services_text: str, models: Mapping[str, Any],
                services: Mapping[str, Any]) -> list[Issue]:
    """Apply the snippet to a scratch copy of the config folder and let the real loader have its say."""
    with tempfile.TemporaryDirectory(prefix="storyagent-check-") as tmp:
        home = Path(tmp)
        cfg = home / "config"
        if settings.config_dir.is_dir():
            shutil.copytree(settings.config_dir, cfg)
        else:
            cfg.mkdir()
        new_models, new_services = _apply(_read(cfg / "models.toml"), _read(cfg / "services.toml"), models,
                                          models_text, services, services_text)
        (cfg / "models.toml").write_text(new_models, "utf-8")
        (cfg / "services.toml").write_text(new_services, "utf-8")
        try:
            load_settings({"STORY_AGENT_HOME": str(home), "STORY_AGENT_CONFIG_DIR": str(cfg)})
        except (ConfigError, KeyError, ValueError, TypeError) as exc:
            return [Issue("error", "config_invalid", {"detail": str(exc)[:300]})]
    return []


def save_snippet(settings: Settings, models_text: str, services_text: str = "", *, force: bool = False) -> Checked:
    """Check, then write. Raises ``ConnectError("invalid")`` (with the issues) when the check finds an error."""
    checked = check_snippet(settings, models_text, services_text, force=force)
    if not checked.ok:
        raise ConnectError("invalid", issues=[i.code for i in checked.issues if i.level == "error"])
    issues: list[Issue] = []
    models = _parse(models_text, "models", issues)
    services = _parse(services_text, "services", issues)
    mfile, sfile = settings.config_dir / "models.toml", settings.config_dir / "services.toml"
    new_models, new_services = _apply(_read(mfile), _read(sfile), models, models_text, services, services_text)
    mfile.parent.mkdir(parents=True, exist_ok=True)
    mfile.write_text(new_models, "utf-8")
    if services_text.strip():
        sfile.write_text(new_services, "utf-8")
    return checked
