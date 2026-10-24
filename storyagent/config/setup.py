"""Install-guide catalog (``setup.toml``): the dependencies of the pipeline, how to detect them and how to install them.

Pure data and validation. Nothing here probes the machine or runs anything (see ``storyagent.setup``).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

CHECK_KINDS = ("command", "run", "path", "app", "ffmpeg_filter", "ollama_models", "comfy_files", "hf_cache",
               "web_dist", "database", "voices")
STEP_KINDS = ("command", "download", "manual")
REF_KINDS = ("tool", "model", "launch", "raw")
PATH_IS = ("file", "dir", "exec", "any")


@dataclass(frozen=True)
class SetupMachine:
    arch: str = "arm64"
    system: str = "Darwin"
    min_macos: str = ""
    disk_margin_gib: float = 20.0
    memory_budget_share: float = 0.75
    default_root: str = ""
    app_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetupCapability:
    name: str
    label_key: str
    note_key: str
    needs: tuple[str, ...] = ()
    rec_ram_gib: float = 0.0
    min_ram_gib: float = 0.0


@dataclass(frozen=True)
class SetupCheck:
    kind: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SetupStep:
    id: str
    kind: str                       # command | download | manual
    argv: tuple[str, ...] = ()
    show: str = ""                  # what a manual step (or a step run elsewhere) looks like on screen
    note_key: str = ""
    cwd: str = ""
    source: str = ""
    file: str = ""
    size_gib: float | None = None   # None = the component's size when it has one download, else unknown
    foreach: str = ""               # "llm_models": one step per model the LLM roles name ({item})
    needs_service: str = ""         # a service that has to answer (ollama pull talks to the server)
    env: Mapping[str, str] = field(default_factory=dict)

    @property
    def runnable(self) -> bool:
        return self.kind != "manual" and bool(self.argv)


@dataclass(frozen=True)
class SetupComponent:
    name: str
    capability: str
    label_key: str
    note_key: str
    size_gib: float = 0.0
    measured: bool = False          # measured on the reference machine (else a rough figure)
    license: str = ""
    url: str = ""
    health: tuple[str, ...] = ()    # healthcheck entries this component makes possible (name or tool.<name>)
    after: tuple[str, ...] = ()     # components to install first
    layout: Mapping[str, str] = field(default_factory=dict)   # config ref -> folder below the install root
    detect: tuple[SetupCheck, ...] = ()
    steps: tuple[SetupStep, ...] = ()
    known_sizes: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SetupProfile:
    name: str
    label_key: str
    note_key: str
    required: tuple[str, ...]
    recommended: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetupConfig:
    machine: SetupMachine = field(default_factory=SetupMachine)
    capabilities: Mapping[str, SetupCapability] = field(default_factory=dict)
    components: Mapping[str, SetupComponent] = field(default_factory=dict)
    profiles: Mapping[str, SetupProfile] = field(default_factory=dict)

    def expand(self, names: tuple[str, ...] | list[str]) -> list[str]:
        """The capabilities in ``names`` plus everything they need, in catalog order."""
        wanted: set[str] = set()

        def add(name: str) -> None:
            if name in wanted:
                return
            wanted.add(name)
            for dep in self.capabilities[name].needs:
                add(dep)

        for n in names:
            add(n)
        return [c for c in self.capabilities if c in wanted]


def _tuple_str(where: str, value: Any, fail: Callable[[str], Exception]) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
        raise fail(f"{where} must be a list of strings")
    return tuple(value)


def _build_step(comp: str, index: int, raw: Mapping[str, Any], fail: Callable[[str], Exception]) -> SetupStep:
    where = f"setup component {comp!r} step {index}"
    kind = str(raw.get("kind", ""))
    if kind not in STEP_KINDS:
        raise fail(f"{where}: kind must be one of {STEP_KINDS}")
    argv = _tuple_str(f"{where} argv", raw.get("argv", []), fail)
    if kind != "manual" and not argv:
        raise fail(f"{where}: a {kind} step needs argv")
    if kind == "manual" and not (raw.get("note_key") or raw.get("show")):
        raise fail(f"{where}: a manual step needs note_key or show")
    if kind == "download" and not (raw.get("source") and raw.get("file")):
        raise fail(f"{where}: a download step names its source and file")
    size = raw.get("size_gib")
    return SetupStep(
        id=f"{comp}.{index}", kind=kind, argv=argv, show=str(raw.get("show", "")),
        note_key=str(raw.get("note_key", "")),
        cwd=str(raw.get("cwd", "")), source=str(raw.get("source", "")), file=str(raw.get("file", "")),
        size_gib=None if size is None else float(size), foreach=str(raw.get("foreach", "")),
        needs_service=str(raw.get("needs_service", "")),
        env={str(k): str(v) for k, v in dict(raw.get("env", {})).items()})


def _build_check(comp: str, raw: Mapping[str, Any], fail: Callable[[str], Exception]) -> SetupCheck:
    kind = str(raw.get("kind", ""))
    if kind not in CHECK_KINDS:
        raise fail(f"setup component {comp!r}: detect kind {kind!r} unknown; known: {list(CHECK_KINDS)}")
    args = {k: v for k, v in raw.items() if k != "kind"}
    if kind == "path":
        ref = str(args.get("ref", ""))
        if ref.partition(":")[0] not in REF_KINDS or not ref.partition(":")[2]:
            raise fail(f"setup component {comp!r}: path ref {ref!r} must look like tool:NAME.KEY, model:REF.KEY, "
                       "launch:NAME.KEY or raw:PATH")
        if str(args.get("is", "any")) not in PATH_IS:
            raise fail(f"setup component {comp!r}: path `is` must be one of {PATH_IS}")
    if kind == "command" and not args.get("name"):
        raise fail(f"setup component {comp!r}: a command check needs `name`")
    if kind == "run" and not args.get("argv"):
        raise fail(f"setup component {comp!r}: a run check needs `argv`")
    return SetupCheck(kind, args)


def build_setup(raw: Mapping[str, Any], fail: Callable[[str], Exception]) -> SetupConfig:
    m = dict(raw.get("machine", {}))
    machine = SetupMachine(
        arch=str(m.get("arch", "arm64")), system=str(m.get("system", "Darwin")), min_macos=str(m.get("min_macos", "")),
        disk_margin_gib=float(m.get("disk_margin_gib", 20)),
        memory_budget_share=float(m.get("memory_budget_share", 0.75)), default_root=str(m.get("default_root", "")),
        app_dirs=tuple(str(x) for x in m.get("app_dirs", ())))
    if not 0 < machine.memory_budget_share <= 1:
        raise fail("[machine] memory_budget_share must be in (0, 1]")

    caps: dict[str, SetupCapability] = {}
    for name, spec in dict(raw.get("capabilities", {})).items():
        caps[name] = SetupCapability(
            name=name, label_key=str(spec.get("label_key", f"setup.cap.{name}")),
            note_key=str(spec.get("note_key", f"setup.cap.{name}.note")),
            needs=_tuple_str(f"capability {name!r} needs", spec.get("needs", []), fail),
            rec_ram_gib=float(spec.get("rec_ram_gib", 0)), min_ram_gib=float(spec.get("min_ram_gib", 0)))
    for item in caps.values():
        for dep in item.needs:
            if dep not in caps:
                raise fail(f"setup capability {item.name!r} needs unknown capability {dep!r}")

    comps: dict[str, SetupComponent] = {}
    for name, spec in dict(raw.get("components", {})).items():
        cap_name = str(spec.get("capability", ""))
        if cap_name not in caps:
            raise fail(f"setup component {name!r}: unknown capability {cap_name!r}")
        steps = tuple(_build_step(name, i, s, fail) for i, s in enumerate(spec.get("steps", []), 1))
        detect = tuple(_build_check(name, c, fail) for c in spec.get("detect", []))
        if not detect:
            raise fail(f"setup component {name!r} has no detect check")
        layout = {str(k): str(v) for k, v in dict(spec.get("layout", {})).items()}
        for ref in layout:
            if ref.partition(":")[0] not in ("tool", "model", "launch"):
                raise fail(f"setup component {name!r}: layout ref {ref!r} must be tool:, model: or launch:")
        comps[name] = SetupComponent(
            name=name, capability=cap_name, label_key=str(spec.get("label_key", f"setup.c.{name}")),
            note_key=str(spec.get("note_key", f"setup.c.{name}.note")), size_gib=float(spec.get("size_gib", 0)),
            measured=bool(spec.get("measured", False)), license=str(spec.get("license", "")),
            url=str(spec.get("url", "")), health=_tuple_str(f"component {name!r} health", spec.get("health", []), fail),
            after=_tuple_str(f"component {name!r} after", spec.get("after", []), fail), layout=layout,
            detect=detect, steps=steps,
            known_sizes={str(k): float(v) for k, v in dict(spec.get("known_sizes", {})).items()})
    seen: set[str] = set()
    for comp in comps.values():
        for dep in comp.after:
            if dep not in comps:
                raise fail(f"setup component {comp.name!r}: after names unknown component {dep!r}")
            if dep not in seen:
                raise fail(f"setup component {comp.name!r}: {dep!r} must be listed before it (install order)")
        seen.add(comp.name)

    profiles: dict[str, SetupProfile] = {}
    for name, spec in dict(raw.get("profiles", {})).items():
        required = _tuple_str(f"profile {name!r} required", spec.get("required", []), fail)
        recommended = _tuple_str(f"profile {name!r} recommended", spec.get("recommended", []), fail)
        for cap_ref in (*required, *recommended):
            if cap_ref not in caps:
                raise fail(f"setup profile {name!r}: unknown capability {cap_ref!r}")
        if not required:
            raise fail(f"setup profile {name!r} needs at least one required capability")
        profiles[name] = SetupProfile(
            name=name, label_key=str(spec.get("label_key", f"setup.profile.{name}")),
            note_key=str(spec.get("note_key", f"setup.profile.{name}.note")), required=required,
            recommended=recommended)
    return SetupConfig(machine, caps, comps, profiles)
