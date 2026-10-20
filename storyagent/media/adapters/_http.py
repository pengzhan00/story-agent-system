"""Shared HTTP plumbing for service-backed adapters: one place that maps transport faults to ``AdapterError``."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests

from storyagent.config import ServiceConfig
from storyagent.domain.errors import ErrorCode

from ..contracts import AdapterError


class ServiceClient:
    """A tiny wrapper over ``requests`` bound to one configured service.

    * connection problems / timeouts  -> ``service_unreachable`` (retryable)
    * HTTP 5xx                        -> ``service_rejected`` (retryable)
    * HTTP 4xx                        -> ``service_rejected`` (not retryable)
    """

    def __init__(self, service: ServiceConfig, session: requests.Session | None = None, timeout: float = 30.0) -> None:
        self.service = service
        self.base = service.url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def url(self, path: str) -> str:
        return path if path.startswith("http") else self.base + path

    def request(self, method: str, path: str, *, timeout: float | None = None, **kw: Any) -> requests.Response:
        try:
            resp = self.session.request(method, self.url(path), timeout=timeout or self.timeout, **kw)
        except requests.RequestException as exc:
            raise AdapterError(
                ErrorCode.SERVICE_UNREACHABLE, f"{self.service.name}: {type(exc).__name__}",
                retryable=True, service=self.service.name,
            ) from exc
        if resp.status_code >= 400:
            raise AdapterError(
                ErrorCode.SERVICE_REJECTED, f"{self.service.name} HTTP {resp.status_code}: {resp.text[:300]}",
                retryable=resp.status_code >= 500, service=self.service.name,
                detail=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return resp

    def json(self, method: str, path: str, **kw: Any) -> Any:
        resp = self.request(method, path, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            raise AdapterError(
                ErrorCode.SERVICE_REJECTED, f"{self.service.name}: response is not JSON",
                retryable=True, service=self.service.name, detail="not JSON",
            ) from exc

    def download(self, path: str, dest: Path, *, timeout: float = 120.0) -> Path:
        resp = self.request("GET", path, timeout=timeout, stream=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                fh.write(chunk)
        return dest


def clean(params: Mapping[str, Any], rename: Mapping[str, str], allowed: set[str]) -> dict[str, Any]:
    """Map our parameter names to the service's and keep only what the service understands."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        name = rename.get(key, key)
        if name in allowed:
            out[name] = value
    return out
