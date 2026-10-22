"""Structural guards for the v2 package."""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "storyagent"

# Anything environment-specific must come from Settings / TOML, never from code.
FORBIDDEN = {
    r"/Users/": "absolute user path",
    r"~/(myworkspace|Documents|\.local)": "home-relative machine path",
    r"https?://(127\.0\.0\.1|localhost)": "hard-coded service URL",
    r"\bpkill\b|\bkillall\b|os\.kill\(": "killing other processes is not allowed",
    r"shell\s*=\s*True": "subprocess shell=True",
}


def py_files() -> list[Path]:
    return [p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_machine_specific_literals() -> None:
    offenders = []
    for path in py_files():
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for pattern, why in FORBIDDEN.items():
                if path.name == "launcher.py" and "killall" in pattern and "os.kill(" in line and "pkill" not in line:
                    continue      # the operator-requested stop of a service listed with `stop = "port"` (see below)
                if re.search(pattern, line):
                    offenders.append(f"{path.relative_to(PKG.parent)}:{n}: {why}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_no_cjk_literals_in_code() -> None:
    """User-visible text lives in locales/; code only carries keys."""
    cjk = re.compile(r"[一-鿿]")
    offenders = []
    for path in py_files():
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if cjk.search(line) and not line.lstrip().startswith(("#", '"""', "'''")):
                offenders.append(f"{path.relative_to(PKG.parent)}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_the_only_process_signal_is_the_operator_requested_service_stop() -> None:
    """``os.kill`` appears once, in ``Launcher._signal``, which ``Launcher.stop`` calls for a service whose
    ``[launch.<name>]`` has ``stop = "port"``; nothing else may signal a process."""
    users = [p for p in py_files() if "os.kill(" in p.read_text("utf-8")]
    assert [p.name for p in users] == ["launcher.py"]
    assert (PKG / "launcher.py").read_text("utf-8").count("os.kill(") == 1
