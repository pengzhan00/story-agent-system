"""The command-line wizard: a tool's ``--help`` becomes a list of options, and the chosen options become a
``provider = "cli"`` connector (``connect/template.py`` language) whose files are managed by the system.

* **Inputs** are never typed as paths: a picture/audio/video option reads the job's reference files by type
  (``{images.0}``, ``{audios.*}`` ...), which the worker has already resolved from ``asset:`` / ``library:`` references.
* **Outputs** are never typed as paths: the output option gets ``{out}`` (a file in the job's work folder, registered
  as the job's asset once it exists and reads), and an output folder gets ``{work}/<name>``.
* Everything else is a parameter (``[params]`` + an optional ``["--flag", "{name}"]`` group, or an ``if`` switch).

Pure functions over text; running the tool's ``--help`` is the caller's business (``run_help``).
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .template import render_argv

ROLES = ("param", "switch", "prompt", "negative_prompt", "seed", "image", "audio", "video", "file",
         "output", "output_dir", "fixed", "skip")
_OPT = re.compile(r"^(\s+)(-[A-Za-z0-9]|--[A-Za-z0-9][\w-]*)((?:,\s*(?:-[A-Za-z0-9]|--[A-Za-z0-9][\w-]*))*)(.*)$")
_DEFAULT = re.compile(r"\(?\bdefault(?:s)?\s*(?:is|:|=)?\s*:?\s*([^\s,;)]+)", re.IGNORECASE)
_META = re.compile(r"^(?:[A-Z][A-Z0-9_]*|<[^>]+>|\[[^\]]+\])$")
_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_WORDS = {
    "image": ("image", "frame", "picture", "png", "jpg", "photo"),
    "audio": ("audio", "soundtrack", "wav", "voice", "speech"),
    "video": ("video", "mp4", "clip"),
}
_PATH_META = {"PATH", "FILE", "DIR", "FOLDER", "PATHS", "FILENAME", "IMAGE", "AUDIO", "VIDEO", "MP4", "WAV", "PNG"}
_COUNT_META = {"N", "INT", "NUM", "NUMBER", "COUNT", "SECONDS", "SEC", "FLOAT"}


@dataclass
class Option:
    flag: str                        # the long form when there is one
    flags: list[str]
    metavars: list[str]
    description: str
    kind: str                        # bool | int | float | string | path
    default: Any = None
    role: str = "param"
    param: str = ""                  # the name a job refers to it by ({param}, [params])
    repeat: bool = False             # the flag may be given again for every file ("Append ...")
    include: bool = True
    notes: list[str] = field(default_factory=list)

    def asdict(self) -> dict[str, Any]:
        return {"flag": self.flag, "flags": self.flags, "metavars": self.metavars, "description": self.description,
                "kind": self.kind, "default": self.default, "role": self.role, "param": self.param,
                "repeat": self.repeat, "include": self.include}


def snake(flag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", flag.lstrip("-").lower()).strip("_")


def _value(raw: str, kind: str) -> Any:
    if kind == "int" and _NUM.match(raw):
        return int(float(raw))
    if kind == "float" and _NUM.match(raw):
        return float(raw)
    return raw


def _kind(metavars: list[str], description: str, default: str | None, flag_name: str = "") -> str:
    if not metavars:
        return "bool"
    meta = metavars[0].strip("<>[]").upper()
    if meta in _PATH_META or "path" in meta.lower():
        return "path"
    if meta in _COUNT_META or (default is not None and _NUM.match(default)):
        wide = (default and "." in default) or meta in ("SECONDS", "SEC", "FLOAT") or "second" in flag_name
        return "float" if wide else "int"
    return "string"


def _role(opt: Option) -> str:
    text = f"{opt.flag} {' '.join(opt.metavars)} {opt.description}".lower()
    name = opt.flag.lstrip("-").lower()
    if name in ("help", "version", "h"):
        return "skip"
    if name in ("prompt", "p", "text", "caption"):
        return "prompt"
    if name in ("negative-prompt", "negative", "neg"):
        return "negative_prompt"
    if name == "seed":
        return "seed"
    if opt.kind == "bool":
        return "switch"
    if opt.kind != "path":
        return "param"
    if name in ("o", "output", "out", "output-file", "out-file") or (
            "output" in text and "input" not in text and "dir" not in name):
        return "output"
    if "dir" in name and any(w in text for w in ("write", "output", "save", "frames")):
        return "output_dir"
    if "model" in name or "weights" in name or "checkpoint" in name:
        return "fixed"
    head = f"{name} {' '.join(opt.metavars)}".lower()
    for scope in (head, text):          # the flag's own name decides before the prose does
        hits = [(scope.find(w), role) for role, words in _WORDS.items() for w in words if w in scope]
        if hits:
            return min(hits)[1]
    return "file"


def parse_help(text: str) -> list[Option]:
    """The options a ``--help`` text lists. Anything unusual is kept as a plain option for the author to adjust."""
    options: list[Option] = []
    current: Option | None = None
    for line in text.splitlines():
        m = _OPT.match(line)
        if m and len(m.group(1)) <= 8:
            rest = m.group(4)
            flags = [m.group(2), *[f.strip() for f in m.group(3).split(",") if f.strip()]]
            metavars: list[str] = []
            desc = rest.strip()
            if rest and not rest.startswith("  "):      # metavars follow the flag after one space; the text after two
                parts = re.split(r"\s{2,}", rest.lstrip(), maxsplit=1)
                left, desc = parts[0], parts[1] if len(parts) > 1 else ""
                for tok in left.split():
                    if _META.match(tok):
                        metavars.append(tok.strip("<>[]"))
                    else:
                        desc = f"{tok} {desc}".strip()
            long = next((f for f in flags if f.startswith("--")), flags[0])
            dm = _DEFAULT.search(desc)
            default = dm.group(1).strip(".") if dm else None
            kind = _kind(metavars, desc, default, long)
            current = Option(flag=long, flags=flags, metavars=metavars, description=desc.strip(), kind=kind,
                             default=_value(default, kind) if default is not None else None,
                             param=snake(long), repeat=bool(re.search(r"\bappend|\brepeat|\bmultiple", desc, re.I)))
            current.role = _role(current)
            current.include = current.role != "skip"
            options.append(current)
        elif current is not None and line.startswith("        ") and line.strip() and not line.strip().startswith("-"):
            current.description = f"{current.description} {line.strip()}".strip()
        elif not line.strip():
            current = None
    return options


def run_help(program: str, arg: str = "--help", timeout: float = 15.0) -> str:
    """``program --help`` (no shell, no input, time-limited); the text it printed on either stream."""
    exe = str(Path(program).expanduser())
    done = subprocess.run([exe, arg], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          cwd=str(Path(exe).parent) if "/" in exe and Path(exe).parent.is_dir() else None, check=False)
    return (done.stdout or "") + ("\n" + done.stderr if done.stderr else "")


# -- building the connector ------------------------------------------------------------------------------------------
def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, Mapping):
        return "{ " + ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, Sequence):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(str(v))


def _media_words(opt: Option, index: int) -> list[str]:
    """One placeholder per file the option takes: ``VIDEO AUDIO`` is a video and an audio."""
    out: list[str] = []
    for meta in (opt.metavars or [""]):
        word = meta.lower()
        role = next((r for r in ("image", "audio", "video") if r in word), opt.role if opt.role in _WORDS else "image")
        out.append(role)
    return out


def build_argv(program: str, options: Sequence[Option]) -> tuple[list[Any], dict[str, Any]]:
    """``(argv template, params)`` for the chosen options. Files are counted per type in the order they appear."""
    argv: list[Any] = [program]
    params: dict[str, Any] = {}
    used: dict[str, int] = {"image": 0, "audio": 0, "video": 0, "file": 0}
    for opt in options:
        if not opt.include or opt.role == "skip":
            continue
        flag = opt.flag
        name = opt.param or snake(flag)
        if opt.role == "prompt":
            argv += [flag, "{prompt}"]
        elif opt.role == "negative_prompt":
            argv.append([flag, "{negative_prompt}"])
        elif opt.role == "seed":
            argv.append([flag, "{seed}"])
        elif opt.role == "output":
            argv += [flag, "{out}"]
        elif opt.role == "output_dir":
            argv += [flag, "{work}/" + (name or "files")]
        elif opt.role == "fixed":
            argv += [flag, str(opt.default if opt.default is not None else "")]
        elif opt.role == "switch":
            argv.append({"if": name, "args": [flag]})
            if opt.default:
                params[name] = True
        elif opt.role in ("image", "audio", "video", "file"):
            if opt.role == "file":
                argv.append([flag, "{ref}"])
                continue
            words = _media_words(opt, used[opt.role])
            if opt.repeat and len(words) == 1:
                argv.append([flag, "{" + words[0] + "s.*}"])
                continue
            group: list[str] = [flag]
            for w in words:
                group.append("{" + f"{w}s.{used[w]}" + "}")
                used[w] += 1
            argv.append(group)
        else:
            argv.append([flag, "{" + name + "}"])
            if opt.default is not None:
                params[name] = opt.default
    return argv, params


def connector_text(ref: str, kind: str, program: str, options: Sequence[Option], *, ext: str = "",
                   cwd: str = "", timeout_sec: int = 3600, license_tag: str = "") -> tuple[str, list[Any]]:
    """The ``models.toml`` section for the tool, and its argv template."""
    argv, params = build_argv(program, options)
    lines = [f'[models."{ref}"]', f"kind = {_toml_value(kind)}", 'provider = "cli"', f"family = {_toml_value(ref)}",
             f"license_tag = {_toml_value(license_tag)}", "commercial_ok = false", f"timeout_sec = {timeout_sec}"]
    if params:
        lines += [f'[models."{ref}".params]', *[f"{k} = {_toml_value(v)}" for k, v in params.items()]]
    lines += [f'[models."{ref}".connector]', "argv = [" + ", ".join(_toml_value(x) for x in argv) + "]"]
    if cwd:
        lines.append(f"cwd = {_toml_value(cwd)}")
    if ext:
        lines.append(f"ext = {_toml_value(ext.lstrip('.'))}")
    return "\n".join(lines) + "\n", argv


def preview(argv: Sequence[Any], params: Mapping[str, Any], sample: Mapping[str, Any] | None = None,
            ext: str = "") -> str:
    """The command a job would run, with sample values: what the author checks before saving."""
    values: dict[str, Any] = {
        "prompt": "<prompt>", "negative_prompt": "<negative prompt>", "seed": 42,
        "out": "<work>/output" + ("." + ext.lstrip(".") if ext else ""),
        "work": "<work>", "ref": "<file>", "images": ["<image 1>", "<image 2>"], "audios": ["<audio 1>"],
        "videos": ["<video 1>"], **dict(params), **dict(sample or {}),
    }
    return shlex.join(render_argv(argv, values))
