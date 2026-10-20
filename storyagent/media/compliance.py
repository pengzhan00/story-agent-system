"""AIGC compliance: consent / licence gating and provenance records.

Default ON, optional (global config, ``STORY_AGENT_COMPLIANCE``, or per project). Provenance
*recording* is an engineering feature and always happens; only the gates and labels are switchable.
Embedding provenance/labels into delivered files is done at export time (compose).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from storyagent import __version__
from storyagent.config import Settings
from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.util import now_iso

from .contracts import JobRecord, ValidationIssue

PROVENANCE_SCHEMA = "storyagent.provenance.v1"


def check_job(
    settings: Settings, job: JobRecord, project_settings: Mapping[str, Any] | None = None
) -> list[ValidationIssue]:
    cfg = settings.compliance_for(project_settings)
    if not cfg.enabled:
        return []
    issues: list[ValidationIssue] = []
    model = settings.models.get(job.model_ref)
    if (
        model is not None
        and not model.commercial_ok
        and cfg.require_restricted_license_ack
        and not (project_settings or {}).get("allow_restricted_licenses")
    ):
        issues.append(
            ValidationIssue(ErrorCode.LICENSE_ACK_REQUIRED, f"restricted licence: {model.license_tag}",
                            {"model": job.model_ref})
        )
    voice_ref = job.inputs.get("voice_ref") or {}
    if (
        job.kind in (MediaKind.AUDIO_TTS, MediaKind.AUDIO_CONVERT)
        and voice_ref.get("audio")
        and cfg.require_voice_consent
        and not voice_ref.get("consent")
    ):
        issues.append(ValidationIssue(ErrorCode.CONSENT_REQUIRED, "voice reference without consent"))
    if job.kind is MediaKind.TRAIN_LORA and cfg.require_likeness_consent and not job.inputs.get("consent"):
        issues.append(ValidationIssue(ErrorCode.CONSENT_REQUIRED, "training on a likeness without consent"))
    return issues


def build_provenance(
    settings: Settings, job: JobRecord, project_settings: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Full, local provenance stored with every asset. Exports use a redacted view (no prompts)."""
    model = settings.models.get(job.model_ref)
    cfg = settings.compliance_for(project_settings)
    return {
        "schema": PROVENANCE_SCHEMA,
        "aigc": True,
        "generator": {"app": "storyagent", "version": __version__},
        "job_id": job.id,
        "created_at": now_iso(),
        "kind": job.kind.value,
        "tier": job.tier.value,
        "provider": job.provider,
        "model": {
            "ref": job.model_ref,
            "version": model.version if model else "",
            "family": model.family if model else "",
            "license_tag": job.license_tag,
            "commercial_ok": job.commercial_ok,
            "attribution": model.attribution if model else "",
        },
        "seed": job.seed,
        "params": job.params,
        "spec": {"id": job.spec_id, "version": job.spec_version},
        "inputs": job.inputs,  # may contain prompts: stripped for exports
        "compliance": {
            "enabled": cfg.enabled,
            "implicit_metadata": cfg.implicit_metadata,
            "explicit_label": cfg.explicit_label,
        },
    }


def redact_for_export(provenance: Mapping[str, Any]) -> dict[str, Any]:
    """What may travel with a delivered file: no prompts/inputs, no local parameters."""
    keep = ("schema", "aigc", "generator", "created_at", "kind", "provider", "compliance")
    out = {k: provenance[k] for k in keep if k in provenance}
    model = provenance.get("model", {})
    out["model"] = {k: model.get(k) for k in ("ref", "version", "family", "license_tag", "attribution")}
    return out
