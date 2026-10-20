"""Minimal JSON-backed i18n.

All user-visible text goes through ``t(key, **params)``; locale files live in
``storyagent/locales/<locale>.json`` as flat ``{"dotted.key": "text {param}"}`` maps.
"""
from .catalog import (
    Catalog,
    LocaleError,
    available_locales,
    check_catalogs,
    get_catalog,
    set_locale,
    t,
)

__all__ = [
    "Catalog",
    "LocaleError",
    "available_locales",
    "check_catalogs",
    "get_catalog",
    "set_locale",
    "t",
]
