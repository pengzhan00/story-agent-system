"""The command-line wizard: a tool's --help -> options -> a connector whose inputs and output the system manages."""
from __future__ import annotations

import json
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings, load_settings
from storyagent.connect import clihelp
from storyagent.connect.template import job_values, render_argv
from storyagent.domain import JobStatus, MediaKind
from storyagent.media.adapters.generic_cli import CliAdapter

from .adapter_kit import job, make_rig, make_settings, wav_bytes

H3_HELP = (Path(__file__).parent / "h3_help.txt").read_text("utf-8")


def by_flag(options: list[clihelp.Option]) -> dict[str, clihelp.Option]:
    return {o.flag: o for o in options}


# -- reading --help ---------------------------------------------------------------------------------------------------
def test_h3_help_is_read_into_typed_options_with_roles() -> None:
    o = by_flag(clihelp.parse_help(H3_HELP))
    assert (o["--width"].kind, o["--width"].default, o["--width"].role) == ("int", 864, "param")
    assert o["--seconds"].kind == "float" and o["--steps"].default == 20
    assert o["--prompt"].role == "prompt" and o["--seed"].role == "seed" and o["--model-dir"].role == "fixed"
    assert o["--output"].role == "output" and o["--frames-dir"].role == "output_dir"
    assert (o["--first-frame"].role, o["--ref-image"].role, o["--ref-audio"].role) == ("image", "image", "audio")
    assert o["--ref-video"].role == "video" and o["--ref-image"].repeat and not o["--first-frame"].repeat
    assert o["--ref-video-audio"].metavars == ["VIDEO", "AUDIO"]
    assert o["--token-reduction"].kind == "bool" and o["--token-reduction"].role == "switch"
    assert o["--ssd-streaming"].role == "switch"            # prose that follows two spaces is not a value name
    assert o["--help"].role == "skip" and not o["--help"].include


def test_pasted_text_in_other_styles_is_read() -> None:
    options = clihelp.parse_help("  -t, --text TEXT   what to say\n"
                                 "  -o FILE            output file (default: out.wav)\n"
                                 "      --speed X      speaking speed, default 1.0\n")
    o = by_flag(options)
    assert o["--text"].flags == ["-t", "--text"] and o["--text"].role == "prompt"
    assert o["-o"].role == "output"
    assert o["--speed"].kind == "float" and o["--speed"].default == 1.0


# -- building ---------------------------------------------------------------------------------------------------------
def chosen() -> list[clihelp.Option]:
    options = by_flag(clihelp.parse_help(H3_HELP))
    options["--model-dir"].default = "/models/H3"
    keep = ("--model-dir", "--prompt", "--output", "--width", "--steps", "--seed", "--token-reduction", "--first-frame",
            "--last-frame", "--ref-image", "--ref-audio")
    return [options[k] for k in keep]


def test_inputs_and_output_are_placeholders_never_paths() -> None:
    text, argv = clihelp.connector_text("h3-cli", "video.av", "/tools/h3", chosen(), ext="mp4", cwd="/tools")
    table = tomllib.loads(text)["models"]["h3-cli"]                  # valid TOML, loadable by the config loader
    assert table["provider"] == "cli" and table["connector"]["argv"] == argv and table["params"] == {
        "width": 864, "steps": 20}
    flat = json.dumps(argv)
    assert '"{out}"' in flat and "{images.0}" in flat and "{images.1}" in flat and "{images.*}" in flat
    assert "{audios.*}" in flat and "outputs/h3.mp4" not in flat     # the tool's own default path is gone
    assert {"if": "token_reduction", "args": ["--token-reduction"]} in argv


def test_the_preview_is_the_command_a_job_would_run() -> None:
    _, argv = clihelp.connector_text("h3-cli", "video.av", "/tools/h3", chosen(), ext="mp4")
    _, params = clihelp.build_argv("/tools/h3", chosen())
    shown = clihelp.preview(argv, params, {"token_reduction": True}, "mp4")
    assert shown.startswith("/tools/h3 --model-dir /models/H3 --prompt '<prompt>' --output '<work>/output.mp4'")
    assert "--token-reduction" in shown and "--first-frame '<image 1>' --last-frame '<image 2>'" in shown
    assert shown.count("--ref-image") == 2                           # once for every image the job brings


# -- the template language ------------------------------------------------------------------------------------------
def test_files_are_listed_by_type_and_repeat_groups_and_switches_work(tmp_path: Path) -> None:
    refs = ["a.png", "b.jpg", "voice.wav", "clip.mp4"]
    v = job_values({"refs": refs, "prompt": "p"}, {"fast": True, "slow": False}, 1, tmp_path)
    assert v["images"] == [str(tmp_path / "a.png"), str(tmp_path / "b.jpg")] and v["image"] == v["images"][0]
    assert v["audios"] == [str(tmp_path / "voice.wav")] and v["videos"] == [str(tmp_path / "clip.mp4")]
    argv = ["t", ["--img", "{images.*}"], ["--aud", "{audios.*}"], ["--none", "{videos2.*}"],
            {"if": "fast", "args": ["--fast"]}, {"if": "slow", "args": ["--slow"]}, {"if": "missing", "args": ["--m"]}]
    assert render_argv(argv, v) == ["t", "--img", v["images"][0], "--img", v["images"][1], "--aud", v["audios"][0],
                                    "--fast"]


# -- a job through the wizard's connector --------------------------------------------------------------------------
TOOL = '''
import json, sys
args = sys.argv[1:]
open(args[args.index("--record") + 1], "w").write(json.dumps(args))
open(args[args.index("--output") + 1], "wb").write(open(args[args.index("--wav") + 1], "rb").read())
'''


def test_a_job_runs_with_system_managed_files(tmp_path: Path) -> None:
    tool = tmp_path / "tool.py"
    tool.write_text(TOOL)
    wav = tmp_path / "sample.wav"
    wav.write_bytes(wav_bytes())
    record = tmp_path / "args.json"
    help_text = ("  --prompt TEXT   the line to speak\n  --output PATH   output file\n"
                 "  --voice PATH    voice audio to copy\n"
                 "  --loud          louder\n  --wav PATH      sample\n  --record PATH   record\n")
    options = clihelp.parse_help(help_text)
    for o in options:
        if o.flag == "--wav":
            o.role, o.default = "fixed", str(wav)
        if o.flag == "--record":
            o.role, o.default = "fixed", str(record)
    text, _ = clihelp.connector_text("wiz", "audio.tts", sys.executable, options, ext="wav")
    text = text.replace('argv = ["' + sys.executable + '"', 'argv = ["' + sys.executable + '", "' + str(tool) + '"')
    s = make_settings(tmp_path, models=text)
    r = make_rig(s, CliAdapter(s))
    voice = tmp_path / "voice.wav"
    voice.write_bytes(wav_bytes())
    rec, assets = r.run(job(MediaKind.AUDIO_TTS, "wiz", {"prompt": "hello", "refs": [str(voice)]},
                            params={"loud": True}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    args = json.loads(record.read_text())
    out = args[args.index("--output") + 1]
    assert args[args.index("--voice") + 1] == str(voice) and "--loud" in args
    assert Path(out).name == "output.wav" and Path(out).parent != tmp_path      # the system picked the place
    assert len(assets) == 1 and assets[0].kind == "audio.tts"       # the file the tool wrote became the job's asset


# -- API ---------------------------------------------------------------------------------------------------------------
@pytest.fixture
def client(fsettings: Settings) -> Iterator[TestClient]:
    again = lambda: load_settings({"STORY_AGENT_HOME": str(fsettings.home)})  # noqa: E731
    with TestClient(create_app(fsettings, loader=again), raise_server_exceptions=False,
                    headers={"Accept-Language": "en"}) as c:
        yield c


def test_api_parses_pasted_help_and_builds_the_connector(client: TestClient) -> None:
    r = client.post("/api/tools/cli/parse", json={"program": "/tools/h3", "help_text": H3_HELP})
    assert r.status_code == 200
    parsed = r.json()
    assert parsed["ran"] is False and "output" in parsed["roles"]
    rows = {o["flag"]: o for o in parsed["options"]}
    assert rows["--width"]["default"] == 864 and rows["--first-frame"]["role"] == "image"
    keep = [o for o in parsed["options"] if o["flag"] in ("--prompt", "--output", "--width", "--first-frame")]
    built = client.post("/api/tools/cli/build", json={"ref": "h3-cli", "kind": "video.av", "program": "/tools/h3",
                                                      "options": keep, "ext": "mp4"})
    assert built.status_code == 200, built.text
    body = built.json()
    assert body["command"].startswith("/tools/h3 --prompt '<prompt>' --output '<work>/output.mp4'")
    # the text goes straight into the connector editor's check
    checked = client.post("/api/tools/connector/check", json={"models": body["models"]})
    assert checked.json()["ok"] is True, checked.text
    assert client.post("/api/tools/cli/parse", json={"program": "x", "help_text": "nothing here"}).status_code == 400
