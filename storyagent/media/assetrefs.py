"""Asset references inside job inputs and params.

A value may name an asset instead of a file path:

    "asset:<asset id>"                       "library:<role>/<name>"
    {"asset": "<asset id>"}                  {"library": "<role>/<name>"}

The worker resolves every reference to an absolute path just before the adapter runs, so adapters only ever see
paths. A library item of role ``voice`` used where a ``voice_ref`` object is expected (that is, as the whole
value of the ``voice_ref`` input) expands to ``{"audio", "text", "consent"}`` from the item's metadata.
The stored job keeps the reference form; ``resolve`` returns what was resolved so it can be logged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from storyagent.config import Settings

from .contracts import AssetRecord
from .ledger import Ledger, LedgerError
from .library import ROLES, Library, LibraryError

# Only a WHOLE value of exactly this shape is a reference, so free text ("asset: the old chest") is never touched.
_REF = re.compile(r"^(?:asset:[0-9a-f]{32}|library:(?:" + "|".join(ROLES) + r")/[^/\x00\n]{1,80})$")


class RefError(ValueError):
    """A reference could not be resolved (unknown, trashed, or its file is gone)."""


@dataclass
class Resolution:
    assets: dict[str, str] = field(default_factory=dict)   # reference text -> asset id
    sha256: dict[str, str] = field(default_factory=dict)   # asset id -> sha256 (for reproducibility)

    def __bool__(self) -> bool:
        return bool(self.assets)


def _ref_of(value: Any) -> str | None:
    if isinstance(value, str):
        return value if _REF.match(value) else None
    if isinstance(value, dict) and len(value) == 1:
        (k, v), = value.items()
        if k in ("asset", "library") and isinstance(v, str):
            return _ref_of(f"{k}:{v}")
    return None


class Resolver:
    def __init__(self, settings: Settings, ledger: Ledger, library: Library | None = None) -> None:
        self.settings, self.ledger = settings, ledger
        self.library = library or Library(ledger.db, settings, ledger)

    def _asset_path(self, asset: AssetRecord, ref: str) -> str:
        if asset.trashed_at:
            raise RefError(f"{ref}: the asset is in the trash")
        path = self.settings.home / asset.path
        if not path.is_file():
            raise RefError(f"{ref}: file is missing ({asset.path})")
        return str(path)

    def _one(self, ref: str, out: Resolution, *, voice_object: bool) -> Any:
        kind, _, rest = ref.partition(":")
        try:
            if kind == "asset":
                asset = self.ledger.get_asset(rest)
                out.assets[ref], out.sha256[asset.id] = asset.id, asset.sha256
                path = self._asset_path(asset, ref)
                if voice_object:  # a bare asset used as a voice reference: its own metadata says what it says / consent
                    return {"audio": path, "text": str(asset.metadata.get("ref_text", asset.metadata.get("text", ""))),
                            "consent": bool(asset.metadata.get("consent", False))}
                return path
            role, _, name = rest.partition("/")
            item = self.library.find(role, name)
            if item is None or item.asset is None:
                raise RefError(f"{ref}: no such library item")
            out.assets[ref], out.sha256[item.asset.id] = item.asset.id, item.asset.sha256
            path = self._asset_path(item.asset, ref)
            if voice_object and item.role == "voice":
                return {
                    "audio": path, "text": str(item.meta.get("text", "")),
                    "consent": bool(item.meta.get("consent", False)),
                    "consent_note": str(item.meta.get("consent_note", "")),
                }
            return path
        except (LedgerError, LibraryError) as exc:
            raise RefError(f"{ref}: {exc}") from exc

    def _walk(self, value: Any, out: Resolution, *, voice_object: bool = False) -> Any:
        ref = _ref_of(value)
        if ref is not None:
            return self._one(ref, out, voice_object=voice_object)
        if isinstance(value, dict):
            return {k: self._walk(v, out, voice_object=(k == "voice_ref")) for k, v in value.items()}
        if isinstance(value, list):
            return [self._walk(v, out) for v in value]
        return value

    def resolve(
        self, inputs: dict[str, Any], params: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], Resolution]:
        out = Resolution()
        return self._walk(inputs, out), self._walk(params, out), out
