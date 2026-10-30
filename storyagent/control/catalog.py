"""Versioned capability descriptors derived from the installed model configuration."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from storyagent.config import Settings
from storyagent.workshop import MODULES, output_media


class ControlError(ValueError):
    def __init__(self, detail: str, code: str = "control_invalid", status: int = 422):
        super().__init__(detail)
        self.code, self.status = code, status


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def catalog(settings: Settings) -> list[dict[str, Any]]:
    result = []
    for module in MODULES:
        for spec in module.kinds:
            bindings = []
            for ref, model in sorted(settings.models.items()):
                if model.kind != spec.kind.value:
                    continue
                descriptor = {
                    "model_ref": ref,
                    "provider": model.provider,
                    "version": model.version,
                    "params": model.params,
                    "tiers": model.tiers,
                    "extra": model.extra,
                }
                ports: dict[str, str] = {"main": output_media(spec.kind)}
                if spec.kind.value == "audio.separate":
                    ports["accompaniment"] = "audio"
                if model.provider == "comfy":
                    from storyagent.media.adapters.comfy import load_template
                    from storyagent.media.contracts import AdapterError

                    try:
                        template = load_template(
                            str(model.extra.get("workflow", "")), settings.config_dir / "workflows"
                        )
                    except AdapterError:
                        template = {"missing": True}
                    descriptor["template"] = template
                    for port, declaration in template.get("outputs", {}).items():
                        ports[port] = {"images": "image", "audio": "audio", "videos": "video", "gifs": "video"}.get(
                            declaration.get("type", "images"), ""
                        )
                if model.provider in settings.services:
                    descriptor["runtime_url"] = settings.service(model.provider).url
                bindings.append(
                    {
                        "id": ref,
                        "digest": digest(descriptor),
                        "provider": model.provider,
                        "model_ref": ref,
                        "version": model.version,
                        "verification": "configured",
                        "heavy": model.heavy,
                        "memory_gib": model.est_memory_gib,
                        "outputs": ports,
                    }
                )
            bindings.sort(key=lambda b: (b["id"] != settings.default_models.get(spec.kind.value), b["id"]))
            result.append(
                {
                    "id": spec.kind.value,
                    "schema_version": 1,
                    "module": module.id,
                    "label_key": f"media.kind.{spec.kind.value}",
                    "inputs": [
                        {"name": f.name, "type": f.type, "media": f.media, "required": f.required} for f in spec.inputs
                    ],
                    "outputs": {
                        "main": output_media(spec.kind),
                        **({"accompaniment": "audio"} if spec.kind.value == "audio.separate" else {}),
                    },
                    "bindings": bindings,
                }
            )
    return result


def descriptor(settings: Settings, kind: str) -> dict[str, Any]:
    found = next((c for c in catalog(settings) if c["id"] == kind), None)
    if found is None:
        raise ControlError(f"Unknown capability: {kind}")
    return found
