from __future__ import annotations

import json
import sys

from storyagent.domain import JobStatus, MediaKind
from storyagent.media.adapters import say

from .adapter_kit import job, make_rig, make_settings, wav_bytes


def test_leading_dash_text_is_a_literal_argument(tmp_path, monkeypatch):
    """Speech is one positional argument after --, even when it resembles say options."""
    argv_path = tmp_path / "argv.json"
    wav_path = tmp_path / "fixture.wav"
    wav_path.write_bytes(wav_bytes())
    binary = tmp_path / "say"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        f"Path({str(argv_path)!r}).write_text(json.dumps(args))\n"
        f"Path(args[args.index('-o') + 1]).write_bytes(Path({str(wav_path)!r}).read_bytes())\n"
    )
    binary.chmod(0o755)
    monkeypatch.setattr(say, "SAY", str(binary))
    rig = make_rig(make_settings(tmp_path), say.SayAdapter(poll_interval_sec=0.01))
    text = "-o /tmp/x"
    try:
        record, assets = rig.run(job(MediaKind.AUDIO_TTS, "say-macos", {"text": text}))
        assert record.status is JobStatus.COMPLETED
        assert len(assets) == 1
        args = json.loads(argv_path.read_text())
        assert args[-2:] == ["--", text]
        assert args.count("--") == 1
    finally:
        rig.worker.shutdown()
