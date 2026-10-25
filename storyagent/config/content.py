"""Content rating configuration (``content.toml``): per-rating storage, masking, logging and export policy."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

ENCRYPTIONS = ("off", "volume")
_LATIN = re.compile(r"^[A-Za-z0-9 \-']+$")


@dataclass(frozen=True)
class RatingPolicy:
    name: str
    rank: int = 0
    label_key: str = ""
    mask_ui: bool = False
    vault: bool = False
    log_prompts: bool = True
    allow_export: bool = True
    allow_remote: bool = True
    gates: bool = False
    min_age: int = 0          # the age a viewer must have (approved by an admin) to see this rating at all

    @property
    def restricted(self) -> bool:
        return self.vault or self.mask_ui or self.gates or not self.allow_export


@dataclass(frozen=True)
class ContentConfig:
    default_rating: str = "general"
    vault_dir: str = "vault"
    volume_mount: str = ""
    encryption: str = "volume"
    unlock_ttl_sec: int = 900
    min_adult_age: int = 18
    blocked_terms: tuple[str, ...] = ()
    allowed_phrases: tuple[re.Pattern[str], ...] = ()    # wording that contains a blocked term and means something else
    ratings: Mapping[str, RatingPolicy] = field(default_factory=lambda: {"general": RatingPolicy("general")})

    def policy(self, rating: str | None) -> RatingPolicy:
        """Policy of ``rating``; an unknown or empty name is the default rating (rows written before a rating was
        removed from the config stay readable)."""
        return self.ratings.get(rating or "", self.ratings[self.default_rating])

    def known(self, rating: str) -> bool:
        return rating in self.ratings

    def higher(self, a: str | None, b: str | None) -> str:
        pa, pb = self.policy(a), self.policy(b)
        return pa.name if pa.rank >= pb.rank else pb.name

    def blocked_in(self, text: str) -> str | None:
        """The first blocked term found in ``text`` (whole words for Latin terms, substring for the rest), after the
        ``allowed_phrases`` (a musical key such as "D minor") are taken out."""
        for allowed in self.allowed_phrases:
            text = allowed.sub(" ", text)
        low = text.lower()
        for term in self.blocked_terms:
            t = term.lower()
            if _LATIN.match(t):
                if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", low):
                    return term
            elif t in low:
                return term
        return None


def build_content(raw: Mapping[str, Any], error: type[Exception]) -> ContentConfig:
    cfg = raw.get("content", {})
    ratings: dict[str, RatingPolicy] = {}
    for name, spec in cfg.get("ratings", {}).items():
        policy = RatingPolicy(
            name=name, rank=int(spec.get("rank", 0)), label_key=str(spec.get("label_key", f"content.rating.{name}")),
            mask_ui=bool(spec.get("mask_ui", False)), vault=bool(spec.get("vault", False)),
            log_prompts=bool(spec.get("log_prompts", True)), allow_export=bool(spec.get("allow_export", True)),
            allow_remote=bool(spec.get("allow_remote", True)), gates=bool(spec.get("gates", False)),
            min_age=int(spec.get("min_age", 0)))
        if policy.vault and not policy.gates:
            raise error(f"content.ratings.{name}: a rating stored in the vault must keep gates = true")
        ratings[name] = policy
    default = str(cfg.get("default_rating", "general"))
    if default not in ratings:
        raise error(f"content.default_rating {default!r} is not defined in content.ratings")
    if ratings[default].restricted:
        raise error("content.default_rating must be an unrestricted rating")
    if len({p.rank for p in ratings.values()}) != len(ratings):
        raise error("content.ratings: every rating needs its own rank")
    by_rank = sorted(ratings.values(), key=lambda p: p.rank)
    if any(a.min_age > b.min_age for a, b in zip(by_rank, by_rank[1:], strict=False)):
        raise error("content.ratings: min_age may not fall as the rank rises")
    encryption = str(cfg.get("encryption", "volume"))
    if encryption not in ENCRYPTIONS:
        raise error(f"content.encryption must be one of {list(ENCRYPTIONS)}")
    vault_dir = str(cfg.get("vault_dir", "vault")).strip()
    if not vault_dir or vault_dir.startswith(("/", "~")) or ".." in vault_dir.split("/"):
        raise error("content.vault_dir must be a relative folder name under the data home")
    gates = cfg.get("gates", {})
    min_age = int(gates.get("min_adult_age", 18))
    if min_age < 18:
        raise error("content.gates.min_adult_age cannot be below 18")
    for r in ratings.values():
        if (r.vault or r.gates) and r.min_age < min_age:
            raise error(f"content.ratings.{r.name}: a rating with the vault or the gates needs min_age >= {min_age}")
    phrases: list[re.Pattern[str]] = []
    for raw_pattern in gates.get("allowed_phrases", ()):
        try:
            phrases.append(re.compile(str(raw_pattern)))
        except re.error as exc:
            raise error(f"content.gates.allowed_phrases: {raw_pattern!r} is not a valid pattern ({exc})") from exc
    return ContentConfig(
        default_rating=default, vault_dir=vault_dir, volume_mount=str(cfg.get("volume_mount", "")).strip(),
        encryption=encryption, unlock_ttl_sec=int(cfg.get("unlock_ttl_sec", 900)), min_adult_age=min_age,
        blocked_terms=tuple(str(t) for t in gates.get("blocked_terms", ())), allowed_phrases=tuple(phrases),
        ratings=ratings)
