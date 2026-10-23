"""Operator plugins: ``<config>/plugins/*.py`` with ``register(registry, settings)``."""
from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace

from storyagent.connect.plugins import load_plugins
from storyagent.media.contracts import HealthState
from storyagent.media.healthcheck import run_checks
from storyagent.media.registry import AdapterRegistry, default_registry

from .adapter_kit import make_settings

GOOD = textwrap.dedent('''
    from storyagent.media.adapters.fake import FakeAdapter

    class MyAdapter(FakeAdapter):
        provider = "myplug"

    def register(registry, settings):
        registry.register(MyAdapter())
''')


def plugins(tmp_path: Path, **files: str) -> Path:
    s = make_settings(tmp_path)
    folder = s.config_dir / "plugins"
    folder.mkdir()
    for name, text in files.items():
        (folder / f"{name}.py").write_text(text)
    return folder


def test_a_plugin_adds_its_adapter_to_the_registry(tmp_path: Path) -> None:
    plugins(tmp_path, good=GOOD)
    s = make_settings(tmp_path)
    reg = default_registry(s)
    assert "myplug" in reg and "comfy" in reg                     # the packaged adapters are still there
    [report] = reg.plugin_reports
    assert (report.name, report.providers, report.error) == ("good", ("myplug",), "")
    assert reg.get("myplug") is not None and reg.get("myplug").provider == "myplug"


def test_a_broken_plugin_is_skipped_and_reported_but_the_rest_load(tmp_path: Path) -> None:
    plugins(tmp_path, a_syntax="def register(:\n", b_none="x = 1\n", c_raises=(
        "def register(registry, settings):\n    raise RuntimeError('no GPU')\n"),
        d_taken=GOOD.replace('"myplug"', '"comfy"'), e_good=GOOD, _private="raise SystemExit(1)\n")
    s = make_settings(tmp_path)
    reg = default_registry(s)
    by_name = {r.name: r for r in reg.plugin_reports}
    assert set(by_name) == {"a_syntax", "b_none", "c_raises", "d_taken", "e_good"}      # _private is ignored
    assert "SyntaxError" in by_name["a_syntax"].error and "register" in by_name["b_none"].error
    assert "no GPU" in by_name["c_raises"].error and "already taken" in by_name["d_taken"].error
    assert by_name["e_good"].error == "" and "myplug" in reg
    assert reg.get("comfy").provider == "comfy"                                          # not replaced


def test_the_health_check_lists_a_plugin_that_did_not_load(tmp_path: Path) -> None:
    plugins(tmp_path, broken="def register(registry, settings):\n    raise RuntimeError('boom')\n")
    s = make_settings(tmp_path)
    up = SimpleNamespace(status_code=200)
    report = run_checks(s, registry=default_registry(s), probes=[], http_get=lambda *a, **k: up)
    entry = {e.name: e for e in report.entries}["plugin.broken"]
    assert entry.state is HealthState.DEGRADED and "boom" in entry.detail


def test_no_plugins_folder_is_fine(tmp_path: Path) -> None:
    s = make_settings(tmp_path)
    assert load_plugins(AdapterRegistry(), s) == []
