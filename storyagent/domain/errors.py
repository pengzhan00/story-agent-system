"""Error codes shared by adapters, the ledger, the API and the UI.

Each code maps to a terminal ``JobStatus`` and has two i18n keys:
``error.<code>``              what happened
``error.<code>.remediation``  what the operator should do next
Waiting reasons (non-terminal, the job stays queued) use ``wait.<reason>``.
"""
from __future__ import annotations

from enum import StrEnum

from .enums import JobStatus


class ErrorCode(StrEnum):
    SERVICE_UNREACHABLE = "service_unreachable"
    SERVICE_REJECTED = "service_rejected"
    WORKFLOW_FAILED = "workflow_failed"
    TOOL_FAILED = "tool_failed"
    TIMEOUT = "timeout"
    MISSING_MODEL = "missing_model"
    MISSING_TOOL = "missing_tool"
    INVALID_PARAMS = "invalid_params"
    INVALID_INPUT = "invalid_input"
    CONSENT_REQUIRED = "consent_required"
    LICENSE_ACK_REQUIRED = "license_ack_required"
    REMOTE_NOT_ALLOWED = "remote_not_allowed"      # the model runs in the cloud: data would leave this machine
    MISSING_CREDENTIALS = "missing_credentials"    # a cloud model whose API key is not in the environment
    OUTPUT_MISSING = "output_missing"
    OUTPUT_INVALID = "output_invalid"
    CANCELLED = "cancelled"
    WORKER_LOST = "worker_lost"
    DEPENDENCY_FAILED = "dependency_failed"  # the pipeline step this job waited for did not complete
    INTERNAL = "internal"

    @property
    def status(self) -> JobStatus:
        return _STATUS[self]


_STATUS: dict[ErrorCode, JobStatus] = {
    ErrorCode.SERVICE_UNREACHABLE: JobStatus.TRANSPORT_ERROR,
    ErrorCode.SERVICE_REJECTED: JobStatus.TRANSPORT_ERROR,
    ErrorCode.WORKFLOW_FAILED: JobStatus.WORKFLOW_ERROR,
    ErrorCode.TOOL_FAILED: JobStatus.WORKFLOW_ERROR,
    ErrorCode.TIMEOUT: JobStatus.TIMEOUT,
    ErrorCode.MISSING_MODEL: JobStatus.FAILED_VALIDATION,
    ErrorCode.MISSING_TOOL: JobStatus.FAILED_VALIDATION,
    ErrorCode.INVALID_PARAMS: JobStatus.FAILED_VALIDATION,
    ErrorCode.INVALID_INPUT: JobStatus.FAILED_VALIDATION,
    ErrorCode.CONSENT_REQUIRED: JobStatus.FAILED_VALIDATION,
    ErrorCode.LICENSE_ACK_REQUIRED: JobStatus.FAILED_VALIDATION,
    ErrorCode.REMOTE_NOT_ALLOWED: JobStatus.FAILED_VALIDATION,
    ErrorCode.MISSING_CREDENTIALS: JobStatus.FAILED_VALIDATION,
    ErrorCode.OUTPUT_MISSING: JobStatus.FAILED_VALIDATION,
    ErrorCode.OUTPUT_INVALID: JobStatus.FAILED_VALIDATION,
    ErrorCode.CANCELLED: JobStatus.CANCELLED,
    ErrorCode.WORKER_LOST: JobStatus.WORKFLOW_ERROR,
    ErrorCode.DEPENDENCY_FAILED: JobStatus.WORKFLOW_ERROR,
    ErrorCode.INTERNAL: JobStatus.WORKFLOW_ERROR,
}


class WaitReason(StrEnum):
    """Why a queued job is not being started yet (i18n ``wait.<value>``)."""

    HEAVY_BUSY = "heavy_busy"
    LIGHT_LIMIT = "light_limit"
    MEMORY_BUDGET = "memory_budget"
    LLM_RESIDENT = "llm_resident"
