from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

FALLBACK_LOCALE = "en"
_PARAM = re.compile(r"\{(\w+)\}")


class LocaleError(ValueError):
    pass


def locales_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "locales"


def available_locales(directory: Path | None = None) -> list[str]:
    d = directory or locales_dir()
    return sorted(p.stem for p in d.glob("*.json"))


def _load(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise LocaleError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise LocaleError(f"{path} must be a flat {{str: str}} map")
    return data


@dataclass
class Catalog:
    locale: str
    messages: dict[str, str]
    fallback: dict[str, str]

    def t(self, key: str, **params: object) -> str:
        text = self.messages.get(key)
        if text is None:
            text = self.fallback.get(key)
            if text is None:
                log.warning("missing i18n key %r (locale=%s)", key, self.locale)
                return key
        try:
            return text.format(**params) if params or "{" in text else text
        except (KeyError, IndexError) as exc:
            log.warning("i18n key %r missing param %s", key, exc)
            return text


_lock = threading.Lock()
_current: Catalog | None = None


def get_catalog(locale: str, directory: Path | None = None) -> Catalog:
    d = directory or locales_dir()
    path = d / f"{locale}.json"
    if not path.is_file():
        raise LocaleError(f"locale {locale!r} not found in {d}")
    fb_path = d / f"{FALLBACK_LOCALE}.json"
    fallback = _load(fb_path) if fb_path.is_file() and locale != FALLBACK_LOCALE else {}
    return Catalog(locale=locale, messages=_load(path), fallback=fallback)


def set_locale(locale: str, directory: Path | None = None) -> Catalog:
    global _current
    with _lock:
        _current = get_catalog(locale, directory)
        return _current


def t(key: str, **params: object) -> str:
    global _current
    if _current is None:
        from storyagent.config import get_settings

        with _lock:
            if _current is None:
                try:
                    _current = get_catalog(get_settings().locale)
                except LocaleError:
                    _current = get_catalog(FALLBACK_LOCALE)
    return _current.t(key, **params)


def check_catalogs(directory: Path | None = None) -> list[str]:
    """Return human-readable problems: key-set mismatch or param mismatch across locales."""
    d = directory or locales_dir()
    names = available_locales(d)
    if not names:
        return [f"no locale files in {d}"]
    cats = {n: _load(d / f"{n}.json") for n in names}
    all_keys = set().union(*(set(c) for c in cats.values()))
    problems: list[str] = []
    for name, cat in cats.items():
        for key in sorted(all_keys - set(cat)):
            problems.append(f"{name}: missing key {key}")
    for key in sorted(all_keys):
        sigs = {
            name: frozenset(_PARAM.findall(cat[key])) for name, cat in cats.items() if key in cat
        }
        if len(set(sigs.values())) > 1:
            problems.append(f"{key}: placeholder mismatch {({n: sorted(s) for n, s in sigs.items()})}")
    return problems
