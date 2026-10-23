"""``provider = "cli"``: any command-line tool as a model, from a ``[models.X.connector]`` table."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.generic_cli import CliAdapter
from storyagent.media.contracts import HealthState

from .adapter_kit import job, make_rig, make_settings, wav_bytes

TOOL = textwrap.dedent('''
    import json, sys, time
    args = sys.argv[1:]
    mode = args[args.index("--mode") + 1] if "--mode" in args else "ok"
    open(args[args.index("--record") + 1], "w").write(json.dumps(args))
    if "--stdin-to" in args:
        open(args[args.index("--stdin-to") + 1], "w").write(sys.stdin.read())
    if mode == "fail":
        print("model exploded")
        sys.exit(3)
    for pct in (10, 60):
        print(f"{pct}% done", flush=True)
    if mode == "nothing":
        sys.exit(0)
    out = args[args.index("--out") + 1]
    open(out, "wb").write(open(args[args.index("--wav") + 1], "rb").read())
''')


def rig(tmp_path: Path, argv: list[object], **connector: object):  # noqa: ANN201
    tool = tmp_path / "tool.py"
    tool.write_text(TOOL)
    wav = tmp_path / "sample.wav"
    wav.write_bytes(wav_bytes())
    record = tmp_path / "args.json"
    full = [sys.executable, str(tool), "--record", str(record), "--wav", str(wav), *argv]
    spec = {"argv": full, "ext": "wav", "progress_regex": r"(\d+)% done", **connector}
    models = ('[models."my-tts"]\nkind = "audio.tts"\nprovider = "cli"\ntimeout_sec = 30\n'
              '[models."my-tts".connector]\n' + "".join(f"{k} = {json.dumps(v)}\n" for k, v in spec.items()))
    s = make_settings(tmp_path, models=models)
    return make_rig(s, CliAdapter(s)), record


def test_runs_the_tool_with_the_values_of_the_job_and_registers_the_file(tmp_path: Path) -> None:
    r, record = rig(tmp_path, ["--text", "{text}", ["--voice", "{ref}"], ["--seed", "{seed}"], "--out", "{out}"])
    rec, assets = r.run(job(MediaKind.AUDIO_TTS, "my-tts", {"text": "hello there"}, seed=5))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    args = json.loads(record.read_text())
    assert args[args.index("--text") + 1] == "hello there" and args[args.index("--seed") + 1] == "5"
    assert "--voice" not in args                            # no reference file: the whole group is left out
    [a] = assets
    assert a.kind == "audio.tts" and a.has_audio and a.metadata["connector"] == "cli"


def test_text_can_be_fed_to_the_standard_input(tmp_path: Path) -> None:
    heard = tmp_path / "heard.txt"
    r, _ = rig(tmp_path, ["--stdin-to", str(heard), "--out", "{out}"], stdin="{text}")
    rec, _ = r.run(job(MediaKind.AUDIO_TTS, "my-tts", {"text": "read me aloud"}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert heard.read_text() == "read me aloud"


def test_a_reference_file_fills_the_optional_group(tmp_path: Path) -> None:
    r, record = rig(tmp_path, ["--text", "{text}", ["--voice", "{ref}"], "--out", "{out}"])
    ref = tmp_path / "voice.wav"
    ref.write_bytes(wav_bytes())
    rec, _ = r.run(job(MediaKind.AUDIO_TTS, "my-tts", {"text": "hi", "refs": [str(ref)]}))
    assert rec.status is JobStatus.COMPLETED
    args = json.loads(record.read_text())
    assert args[args.index("--voice") + 1] == str(ref)


def test_a_required_value_the_job_lacks_is_invalid_input_and_nothing_runs(tmp_path: Path) -> None:
    r, record = rig(tmp_path, ["--text", "{text}", "--out", "{out}"])
    rec, _ = r.run(job(MediaKind.AUDIO_TTS, "my-tts", {"prompt": "x"}))
    assert rec.status is JobStatus.FAILED_VALIDATION and rec.error_code == ErrorCode.INVALID_INPUT.value
    assert not record.exists()


def test_a_failing_tool_reports_the_tail_of_its_log(tmp_path: Path) -> None:
    r, _ = rig(tmp_path, ["--mode", "fail", "--out", "{out}"])
    rec, assets = r.run(job(MediaKind.AUDIO_TTS, "my-tts", {"text": "x"}))
    assert rec.error_code == ErrorCode.TOOL_FAILED.value and "model exploded" in (rec.error_message or "")
    assert not assets


def test_a_tool_that_writes_nothing_is_not_completed(tmp_path: Path) -> None:
    r, _ = rig(tmp_path, ["--mode", "nothing", "--out", "{out}"])
    rec, assets = r.run(job(MediaKind.AUDIO_TTS, "my-tts", {"text": "x"}))
    assert rec.error_code == ErrorCode.OUTPUT_MISSING.value and not assets


def test_a_glob_finds_the_file_the_tool_chose_the_name_of(tmp_path: Path) -> None:
    # the tool writes to {out}; the glob must find it as well (newest match of the pattern in the work folder)
    r, _ = rig(tmp_path, ["--out", "{work}/result_1.wav"], output="result_*.wav")
    rec, assets = r.run(job(MediaKind.AUDIO_TTS, "my-tts", {"text": "x"}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert assets[0].path.endswith(".wav")


def test_a_missing_program_is_a_missing_tool_and_health_names_it(tmp_path: Path) -> None:
    models = ('[models."gone"]\nkind = "audio.tts"\nprovider = "cli"\n[models."gone".connector]\n'
              'argv = ["/no/such/tool", "{text}"]\n')
    s = make_settings(tmp_path, models=models)
    adapter = CliAdapter(s)
    rec, _ = make_rig(s, adapter).run(job(MediaKind.AUDIO_TTS, "gone", {"text": "x"}))
    assert rec.error_code == ErrorCode.MISSING_TOOL.value
    report = adapter.health()
    assert report.state is HealthState.DEGRADED and report.missing == ("gone: /no/such/tool",)


def test_a_model_without_a_connector_table_is_refused_with_the_reason(tmp_path: Path) -> None:
    models = '[models."bare"]\nkind = "audio.tts"\nprovider = "cli"\n'
    s = make_settings(tmp_path, models=models)
    rec, _ = make_rig(s, CliAdapter(s)).run(job(MediaKind.AUDIO_TTS, "bare", {"text": "x"}))
    assert rec.status is JobStatus.FAILED_VALIDATION and "connector" in (rec.error_message or "")


def test_progress_comes_from_the_log(tmp_path: Path) -> None:
    s = make_settings(tmp_path, models=('[models."p"]\nkind = "audio.tts"\nprovider = "cli"\n[models."p".connector]\n'
                                       'argv = ["x"]\nprogress_regex = "(\\\\d+)% done"\n'))
    adapter = CliAdapter(s)
    rec, _ = make_rig(s, adapter).submit(job(MediaKind.AUDIO_TTS, "p", {"text": "x"})), None
    assert adapter.progress(rec, "10% done\n60% done\n") == (0.6, "60")
    assert adapter.progress(rec, "nothing yet") == (0.0, "")
