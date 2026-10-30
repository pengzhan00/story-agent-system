from __future__ import annotations

import sys

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.demucs import DemucsAdapter
from storyagent.media.contracts import JobContext

from .adapter_kit import job, make_rig, make_settings, wav_bytes


def test_explicit_local_repository_rejects_wrong_format_before_subprocess(tmp_path):
    repo = tmp_path / "weights"
    repo.mkdir()
    (repo / "955717e8.safetensors").write_bytes(b"wrong engine format")
    settings = make_settings(
        tmp_path,
        services=f'[tools.seedvc]\npython="{sys.executable}"\n',
        models=f'[models."demucs-htdemucs".extra]\nmodel_dir="{repo}"\n',
    )
    source = settings.home / "source.wav"
    source.write_bytes(wav_bytes())
    adapter = DemucsAdapter()
    rig = make_rig(settings, adapter)
    try:
        record, assets = rig.run(job(MediaKind.AUDIO_SEPARATE, "demucs-htdemucs", {"audio": str(source)}))
        assert record.status is JobStatus.FAILED_VALIDATION
        assert record.error_code == ErrorCode.MISSING_MODEL.value
        assert not assets
        # Once compatible weights are present, the command consumes the configured local repo.
        (repo / "955717e8.th").write_bytes(b"fixture only; never executed")
        ctx = JobContext(settings, record, tmp_path / "work", lambda *args, **kw: None)
        assert adapter.validate(record, ctx) == []
        command = adapter.command(record, ctx)
        assert command.argv[command.argv.index("--repo") + 1] == str(repo)
    finally:
        rig.worker.shutdown()
