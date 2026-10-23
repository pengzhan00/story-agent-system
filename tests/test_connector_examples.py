"""The shipped connector examples load as configuration and pass the adapters' own checks."""
from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.generic_cli import CliAdapter
from storyagent.media.adapters.generic_http import HttpAdapter
from storyagent.media.contracts import JobContext

from .adapter_kit import job, make_rig, make_settings

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples" / "connectors"
NAMES = sorted(p.name.removesuffix(".models.toml") for p in EXAMPLES.glob("*.models.toml"))
INPUTS = {"prompt": "a test", "text": "hello", "refs": []}


def test_every_example_has_a_models_file() -> None:
    assert len(NAMES) >= 6 and (EXAMPLES / "README.md").is_file()


@pytest.mark.parametrize("name", NAMES)
def test_the_example_loads_and_is_accepted_by_its_adapter(tmp_path: Path, name: str) -> None:
    services = EXAMPLES / f"{name}.services.toml"
    s = make_settings(tmp_path, services=services.read_text() if services.is_file() else "",
                      models=(EXAMPLES / f"{name}.models.toml").read_text())
    [ref] = [r for r, m in s.models.items() if m.provider in ("http", "cli")]
    model = s.models[ref]
    assert model.extra["connector"] and model.commercial_ok in (True, False)
    adapter = CliAdapter(s) if model.provider == "cli" else HttpAdapter(s)
    kind = MediaKind(model.kind)
    ref_file = tmp_path / "in.wav"
    ref_file.write_bytes(b"x")
    rig = make_rig(s, adapter)
    rec = rig.submit(job(kind, ref, {**INPUTS, "refs": [str(ref_file)]}))
    ctx = JobContext(s, rec, tmp_path / "work", lambda *a, **k: None)
    found = adapter.validate(rec, ctx)
    issues = [i for i in found if i.code is not ErrorCode.MISSING_TOOL]            # the tool may not be installed
    assert issues == [], [(i.code, i.message) for i in issues]
