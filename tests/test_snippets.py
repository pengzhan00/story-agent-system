"""Connector snippets written as TOML text: the starting points are valid, mistakes are named, saving keeps the rest."""
from __future__ import annotations

import pytest

from storyagent.config import Settings, load_settings
from storyagent.connect.snippets import TEMPLATES, check_snippet, save_snippet, template
from storyagent.connect.store import ConnectError

CLI = '''
[models."my-cli"]
kind = "audio.tts"
provider = "cli"
license_tag = "mit"
[models."my-cli".connector]
argv = ["/bin/echo", "{text}"]
'''


def codes(settings: Settings, models: str, services: str = "", **kw: bool) -> set[str]:
    return {i.code for i in check_snippet(settings, models, services, **kw).issues if i.level == "error"}


@pytest.mark.parametrize("name", TEMPLATES)
def test_every_starting_point_passes_the_check_as_it_is(fsettings: Settings, name: str) -> None:
    models, services = template(name)
    checked = check_snippet(fsettings, models, services)
    assert checked.ok, [(i.code, dict(i.params)) for i in checked.issues]
    assert {i.code for i in checked.issues} <= {"license_empty"}              # only the reminder to fill in the licence


def test_an_unknown_starting_point_is_refused() -> None:
    with pytest.raises(ConnectError) as e:
        template("nope")
    assert e.value.code == "unknown_template"


def test_mistakes_are_named(fsettings: Settings) -> None:
    assert codes(fsettings, "[models.x") == {"toml_syntax"}
    assert codes(fsettings, "") == {"one_model"}
    assert codes(fsettings, CLI + CLI.replace("my-cli", "other")) == {"one_model"}
    assert codes(fsettings, '[other.x]\na = 1\n') == {"only_root", "one_model"}
    assert codes(fsettings, CLI.replace("my-cli", "Bad Name")) == {"bad_ref"}
    assert codes(fsettings, CLI.replace('"cli"', '"nope"')) == {"provider"}
    assert codes(fsettings, CLI.replace("audio.tts", "text.story")) == {"kind"}
    assert codes(fsettings, CLI.replace('argv = ["/bin/echo", "{text}"]', 'argv = []')) == {"cli_argv"}
    assert codes(fsettings, CLI.split("[models.\"my-cli\".connector]")[0]) == {"no_connector"}
    assert codes(fsettings, '[models.c]\nkind = "image.generate"\nprovider = "comfy"\nworkflow = "nope"\n') == {
        "comfy_workflow"}
    assert codes(fsettings, CLI, '[other.y]\na = 1\n') == {"only_root"}


def test_http_connector_structure_is_checked(fsettings: Settings) -> None:
    models, services = template("http_async")
    assert codes(fsettings, models, "") == {"http_service"}                  # no such service
    assert codes(fsettings, models.replace('id_path = "id"\n', ""), services) == {"http_async"}
    assert codes(fsettings, models.replace('path = "/jobs"\n', ""), services) == {"http_submit"}
    assert codes(fsettings, models.replace('url_path = "result.url"', ""), services) == {"http_result"}
    assert codes(fsettings, models.replace('service = "my_job_tool"', 'service = "my_job_tool"\nmode = "x"'),
                 services) == {"http_mode"}


def test_the_whole_configuration_has_its_say(fsettings: Settings) -> None:
    bad = CLI.replace('kind = "audio.tts"', 'kind = "audio.tts"\ntimeout_sec = "soon"')
    assert "config_invalid" in codes(fsettings, bad)


def test_a_packaged_model_name_is_taken_and_a_name_of_your_own_needs_force(fsettings: Settings) -> None:
    packaged = next(iter(fsettings.models))
    assert codes(fsettings, CLI.replace("my-cli", packaged)) == {"ref_packaged"}
    save_snippet(fsettings, CLI)
    again = load_settings({"STORY_AGENT_HOME": str(fsettings.home)})
    assert codes(again, CLI) == {"exists"}
    forced = check_snippet(again, CLI, force=True)
    assert forced.ok and [i.code for i in forced.issues] == ["will_replace"]


def test_saving_adds_the_tables_and_leaves_the_rest_of_the_files_alone(fsettings: Settings) -> None:
    models_file, services_file = fsettings.config_dir / "models.toml", fsettings.config_dir / "services.toml"
    before_models, before_services = models_file.read_text(), services_file.read_text()
    models, services = template("http_sync")
    save_snippet(fsettings, models, services)
    assert models_file.read_text().startswith(before_models.rstrip("\n"))
    assert services_file.read_text().startswith(before_services.rstrip("\n"))
    loaded = load_settings({"STORY_AGENT_HOME": str(fsettings.home)})
    assert loaded.models["my-http-tool"].provider == "http" and "my_tool" in loaded.services

    save_snippet(loaded, models.replace("timeout_sec = 600", "timeout_sec = 900"), services, force=True)
    text = models_file.read_text()
    assert text.count('[models."my-http-tool"]') == 1 and "timeout_sec = 900" in text
    assert services_file.read_text().count("[services.my_tool]") == 1


def test_a_snippet_with_errors_is_not_written(fsettings: Settings) -> None:
    before = (fsettings.config_dir / "models.toml").read_text()
    with pytest.raises(ConnectError) as e:
        save_snippet(fsettings, CLI.replace('"cli"', '"nope"'))
    assert e.value.code == "invalid" and e.value.params["issues"] == ["provider"]
    assert (fsettings.config_dir / "models.toml").read_text() == before
