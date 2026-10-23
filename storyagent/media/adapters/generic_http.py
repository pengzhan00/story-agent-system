"""Call any HTTP service as a model: ``provider = "http"`` and a ``[models.X.connector]`` table.

    [services.mytool]                      # in services.toml: where it listens and how to tell it is up
    url = "http://mytool.example:9000"
    health_path = "/health"

    [models."my-img"]
    kind = "image.generate"
    provider = "http"
    [models."my-img".connector]
    service = "mytool"
    mode = "async"                         # "sync": the answer to the first request is the result
    ext = "png"                            # what comes back (default by kind)
    required = ["prompt"]                  # values the job must have; otherwise it is refused before anything is sent
    headers = { Authorization = "Bearer {env.MYTOOL_KEY}" }          # optional; {env.X} keeps keys out of the file
    [models."my-img".connector.submit]
    method = "POST"                        # default POST
    path = "/generate"
    json = { prompt = "{prompt}", seed = "{seed}", width = "{width}", steps = "{steps|20}" }
    id_path = "job.id"                     # async: where the job id is in the answer
    [models."my-img".connector.poll]       # async only
    path = "/jobs/{id}"
    status_path = "status"
    done = ["done", "succeeded"]
    failed = ["error", "failed"]
    progress_path = "progress"             # optional; progress_scale = 100 when it counts percent
    error_path = "error"                   # optional: text shown when it failed
    [models."my-img".connector.result]
    url_path = "result.url"                # or b64_path = "images.0", or path = "/jobs/{id}/file", or content = "body"

Entries of ``json`` / ``data`` / ``query`` whose placeholder the job has no value for are left out (see
``connect/template.py``). The answer of a ``sync`` request and the status answers are read as JSON by dotted path
(``data.0.url``); ``content = "body"`` takes the raw bytes of the answer as the file.
"""
from __future__ import annotations

import base64
import json
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from storyagent.config import ConfigError, Settings
from storyagent.connect.spec import GENERIC_KINDS, connector_models, connector_of
from storyagent.connect.template import NOTHING, Missing, get_path, job_values, lookup, render, render_string
from storyagent.domain.errors import ErrorCode

from ..contracts import (
    AdapterBase,
    AdapterError,
    ArtifactSpec,
    Capabilities,
    HealthReport,
    HealthState,
    JobContext,
    JobRecord,
    Progress,
    ValidationIssue,
)
from ._http import ServiceClient

DEFAULT_EXT = {"audio": "wav", "video": "mp4", "image": "png"}
SYNC = "sync:"


class HttpAdapter(AdapterBase):
    provider = "http"

    def __init__(self, settings: Settings, poll_interval_sec: float = 2.0) -> None:
        self._settings = settings
        self.poll_interval_sec = poll_interval_sec
        self._clients: dict[str, ServiceClient] = {}
        self._sync: dict[str, tuple[bytes, str]] = {}          # external id -> (answer bytes, content type)
        self._lock = threading.Lock()

    def capabilities(self) -> Capabilities:
        return Capabilities(kinds=GENERIC_KINDS, heavy=False, supports_cancel=False, supports_reattach=True,
                            poll_interval_sec=self.poll_interval_sec)

    # -- reading the connector ----------------------------------------------------------------
    def _spec(self, job: JobRecord, ctx: JobContext) -> dict[str, Any]:
        try:
            return dict(connector_of(ctx.settings, job.model_ref, self.provider))
        except ConfigError as exc:
            raise AdapterError(ErrorCode.INVALID_PARAMS, str(exc), detail=str(exc)) from exc

    def _client(self, spec: dict[str, Any]) -> ServiceClient:
        name = str(spec.get("service") or "")
        try:
            service = self._settings.service(name)
        except ConfigError as exc:
            raise AdapterError(ErrorCode.INVALID_PARAMS, f"connector.service {name!r} is not in services.toml",
                               detail=str(exc)) from exc
        with self._lock:
            if name not in self._clients:
                self._clients[name] = ServiceClient(service)
            return self._clients[name]

    @staticmethod
    def _values(job: JobRecord, ctx: JobContext, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return job_values(job.inputs, job.params, job.seed, ctx.settings.home, extra)

    @staticmethod
    def _ext(job: JobRecord, spec: dict[str, Any]) -> str:
        return str(spec.get("ext") or DEFAULT_EXT.get(job.kind.value.split(".")[0], "bin")).lstrip(".")

    def _headers(self, spec: dict[str, Any], values: dict[str, Any]) -> dict[str, str]:
        try:
            return {str(k): str(v) for k, v in render(spec.get("headers") or {}, values, required=True).items()}
        except Missing as exc:
            raise AdapterError(ErrorCode.INVALID_PARAMS, f"connector.headers needs {{{exc.name}}}",
                               detail=exc.name) from exc

    def _request(self, spec: dict[str, Any], part: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        """The keyword arguments of the request described by ``part`` (method, path, json, data, query)."""
        try:
            path = str(render_string(str(part.get("path") or ""), values))
            kw: dict[str, Any] = {"headers": self._headers(spec, values)}
            if "json" in part:
                kw["json"] = render(part["json"], values)
            if "data" in part:
                kw["data"] = render(part["data"], values)
            if "query" in part:
                kw["params"] = render(part["query"], values)
        except Missing as exc:
            raise AdapterError(ErrorCode.INVALID_INPUT, f"the request needs a value for {{{exc.name}}}",
                               detail=exc.name) from exc
        kw["method"] = str(part.get("method") or "POST").upper()
        kw["path"] = path
        return kw

    # -- contract -----------------------------------------------------------------------------
    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        try:
            spec = self._spec(job, ctx)
            self._client(spec)
            mode = str(spec.get("mode") or "async")
            if mode not in ("sync", "async"):
                raise AdapterError(ErrorCode.INVALID_PARAMS, "connector.mode must be sync or async", detail=mode)
            submit = spec.get("submit")
            if not isinstance(submit, dict) or not submit.get("path"):
                raise AdapterError(ErrorCode.INVALID_PARAMS, "connector.submit.path is missing", detail="submit.path")
            if mode == "async" and not (isinstance(submit.get("id_path"), str) and isinstance(spec.get("poll"), dict)):
                raise AdapterError(ErrorCode.INVALID_PARAMS, "async needs submit.id_path and a [poll] table",
                                   detail="async")
            values = self._values(job, ctx, {"id": "0"})
            for name in spec.get("required") or []:
                if lookup(values, str(name)) is NOTHING:
                    raise AdapterError(ErrorCode.INVALID_INPUT, f"this tool needs a value for {name}",
                                       detail=str(name))
            self._request(spec, submit, values)
        except AdapterError as exc:
            return [ValidationIssue(exc.code, exc.message, exc.params)]
        return []

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        spec = self._spec(job, ctx)
        client = self._client(spec)
        values = self._values(job, ctx)
        kw = self._request(spec, spec["submit"], values)
        resp = client.request(kw.pop("method"), kw.pop("path"), timeout=float(spec.get("timeout_sec") or 120), **kw)
        if str(spec.get("mode") or "async") == "sync":
            ext_id = SYNC + uuid.uuid4().hex
            with self._lock:
                self._sync[ext_id] = (resp.content, resp.headers.get("content-type", ""))
            return ext_id
        try:
            body = resp.json()
        except ValueError as exc:
            raise AdapterError(ErrorCode.SERVICE_REJECTED, f"{client.service.name}: the answer is not JSON",
                               service=client.service.name, detail="not JSON") from exc
        found = get_path(body, str(spec["submit"].get("id_path") or ""))
        if found is None or found == "":
            raise AdapterError(ErrorCode.SERVICE_REJECTED,
                               f"no job id at {spec['submit'].get('id_path')!r} in {str(body)[:200]}",
                               service=client.service.name, detail="no job id")
        return str(found)

    def _poll_body(self, spec: dict[str, Any], client: ServiceClient, external_id: str, job: JobRecord,
                   ctx: JobContext) -> Any:
        part = dict(spec.get("poll") or {})
        part.setdefault("method", "GET")
        kw = self._request(spec, part, self._values(job, ctx, {"id": external_id}))
        return client.request(kw.pop("method"), kw.pop("path"), timeout=30, **kw).json()

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        if external_id.startswith(SYNC):
            with self._lock:
                known = external_id in self._sync
            if not known:
                raise AdapterError(ErrorCode.WORKER_LOST, "the answer belongs to a previous worker")
            return Progress("succeeded", 1.0, "done")
        spec = self._spec(job, ctx)
        poll = spec.get("poll") or {}
        body = self._poll_body(spec, self._client(spec), external_id, job, ctx)
        status = str(get_path(body, str(poll.get("status_path") or "status")))
        done = {str(x) for x in poll.get("done") or ["done", "succeeded", "completed"]}
        failed = {str(x) for x in poll.get("failed") or ["error", "failed"]}
        if status in failed:
            text = get_path(body, str(poll.get("error_path") or "error"))
            msg = str(text) if text else f"the service reports {status}"
            return Progress("failed", error=AdapterError(ErrorCode.TOOL_FAILED, msg, detail=msg[:300]))
        if status in done:
            return Progress("succeeded", 1.0, "done")
        raw = get_path(body, str(poll["progress_path"])) if poll.get("progress_path") else None
        try:
            fraction = float(raw) / float(poll.get("progress_scale") or 1) if raw is not None else 0.0
        except (TypeError, ValueError):
            fraction = 0.0
        return Progress("running", max(0.0, min(fraction, 0.99)), status if status != "None" else "")

    def reattach(self, external_id: str, job: JobRecord, ctx: JobContext) -> bool:
        if external_id.startswith(SYNC):
            return False
        try:
            self.poll(external_id, job, ctx)
        except AdapterError:
            return False
        return True

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        spec = self._spec(job, ctx)
        client = self._client(spec)
        result = dict(spec.get("result") or {})
        ext = self._ext(job, spec)
        dest = ctx.work_dir / f"output.{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        body: Any = None
        raw = b""
        if external_id.startswith(SYNC):
            with self._lock:
                raw, _ctype = self._sync.pop(external_id, (b"", ""))
            if result.get("content") != "body":
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    body = None
        elif result.get("content") != "body":
            body = self._poll_body(spec, client, external_id, job, ctx)
        values = self._values(job, ctx, {"id": external_id})
        if result.get("content") == "body":
            if external_id.startswith(SYNC):
                dest.write_bytes(raw)
            else:
                part = {"method": "GET", "path": result.get("path", "")}
                kw = self._request(spec, part, values)
                client.download(kw["path"], dest)
        elif result.get("b64_path"):
            data = get_path(body, str(result["b64_path"]))
            if not isinstance(data, str) or not data:
                raise AdapterError(ErrorCode.OUTPUT_MISSING, f"no base64 data at {result['b64_path']!r}")
            dest.write_bytes(base64.b64decode(data.split(",", 1)[-1]))
        elif result.get("url_path"):
            url = get_path(body, str(result["url_path"]))
            if not isinstance(url, str) or not url:
                raise AdapterError(ErrorCode.OUTPUT_MISSING, f"no url at {result['url_path']!r}")
            suffix = Path(urlparse(url).path).suffix.lstrip(".")
            if suffix and not spec.get("ext"):
                dest = ctx.work_dir / f"output.{suffix}"
            client.download(url, dest)
        elif result.get("path"):
            kw = self._request(spec, {"method": "GET", "path": result["path"]}, values)
            client.download(kw["path"], dest)
        else:
            raise AdapterError(ErrorCode.INVALID_PARAMS,
                               "connector.result needs url_path, b64_path, path or content = \"body\"",
                               detail="result")
        if not dest.is_file() or dest.stat().st_size == 0:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "the service answered but no file came back")
        return [ArtifactSpec(dest, metadata={"connector": "http", "service": str(spec.get("service")),
                                             "prompt": str(job.inputs.get("prompt", ""))[:500]})]

    def health(self) -> HealthReport:
        down: list[str] = []
        services: set[str] = set()
        for ref in connector_models(self._settings, self.provider):
            spec = self._settings.model(ref).extra.get("connector")
            name = str(spec.get("service") or "") if isinstance(spec, dict) else ""
            if name:
                services.add(name)
        for name in sorted(services):
            try:
                self._settings.service(name)
            except ConfigError:
                down.append(f"{name}: not in services.toml")
                continue
            try:
                ServiceClient(self._settings.service(name)).request("GET", self._settings.service(name).health_path,
                                                                    timeout=5)
            except AdapterError:
                down.append(name)
        if down:
            return HealthReport(HealthState.NOT_RUNNING, "", tuple(down))
        return HealthReport(HealthState.OK)
