"""Speakers of the AISHELL-3 corpus (OpenSLR SLR93, Apache-2.0) as voices of the asset library.

One reference clip per speaker, picked to suit zero-shot cloning (5-15 s, one whole sentence, a transcript that is all
characters), converted to a 24 kHz mono wav, filed as ``voice/AISHELL3-<speaker>`` with the speaker's gender, age group
and accent as tags. Speakers under 14 are never imported. The corpus licence covers the recordings, not the speakers'
consent to being cloned: voices are filed **without** the consent flag unless the operator says they have it, and a job
that clones a voice without it is refused (``require_voice_consent``).
"""
from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from storyagent.config import Settings
from storyagent.media.library import Library, LibraryError
from storyagent.media.probe import find_tool

AGES = {"A": "<14", "B": "14-25", "C": "26-40", "D": ">40"}
IMPORTABLE = ("B", "C", "D")                  # group A (under 14) is never imported
SRC_BYTES_PER_SEC = 44_100 * 2                 # the corpus: 44.1 kHz, 16 bit, mono
MIN_SEC, TARGET_SEC, MAX_SEC = 5.0, 8.0, 14.0
MIN_CHARS, MAX_CHARS = 10, 40
_CJK = re.compile(f"[{chr(0x3400)}-{chr(0x9FFF)}]")      # the characters of a transcript, not its pinyin


@dataclass(frozen=True)
class Speaker:
    id: str
    age: str
    gender: str
    accent: str


@dataclass
class Imported:
    speaker: str
    name: str = ""
    seconds: float = 0.0
    text: str = ""
    skipped: str = ""           # why nothing was imported for this speaker


def speakers(root: Path) -> dict[str, Speaker]:
    out: dict[str, Speaker] = {}
    for line in (root / "spk-info.txt").read_text("utf-8").splitlines():
        parts = line.split()
        if len(parts) == 4 and not line.startswith("#"):
            out[parts[0]] = Speaker(*parts)
    return out


def transcript(line: str) -> str:
    """A corpus line is characters each followed by their pinyin: keep the characters only."""
    return "".join(tok for tok in line.split() if _CJK.search(tok))


def _lines(root: Path) -> dict[str, list[tuple[Path, str]]]:
    """speaker -> [(wav, transcript)] over the train and test parts that are on disk."""
    found: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for part in ("train", "test"):
        content = root / part / "content.txt"
        if not content.is_file():
            continue
        for row in content.read_text("utf-8").splitlines():
            name, _, rest = row.partition("\t")
            if len(name) < 11 or not name.endswith(".wav"):
                continue
            spk = name[:7]
            wav = root / part / "wav" / spk / name
            text = transcript(rest)
            if MIN_CHARS <= len(text) <= MAX_CHARS and wav.is_file():
                found[spk].append((wav, text))
    return found


def pick(candidates: list[tuple[Path, str]]) -> tuple[Path, str, float] | None:
    """The clip whose length is nearest 8 s, within 5-14 s (file size stands in for length: the corpus is raw PCM)."""
    best: tuple[float, Path, str, float] | None = None
    for wav, text in candidates:
        sec = wav.stat().st_size / SRC_BYTES_PER_SEC
        if MIN_SEC <= sec <= MAX_SEC and (best is None or abs(sec - TARGET_SEC) < best[0]):
            best = (abs(sec - TARGET_SEC), wav, text, sec)
    return None if best is None else (best[1], best[2], best[3])


def _convert(settings: Settings, src: Path, dest: Path) -> None:
    ffmpeg = find_tool(settings, "ffmpeg")
    if ffmpeg is None:
        raise LibraryError("ffmpeg is needed to prepare the reference clips")
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(ffmpeg), "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "24000", "-sample_fmt", "s16",
         str(dest)], capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dest.is_file():
        raise LibraryError(f"could not convert {src.name}: {proc.stderr[-160:]}")


def import_speakers(
    settings: Settings, library: Library, root: Path, work_dir: Path, *, ages: tuple[str, ...] = IMPORTABLE,
    genders: tuple[str, ...] = ("female", "male"), only: tuple[str, ...] = (), limit: int = 0,
    consent: bool = False, replace: bool = False, dry_run: bool = False,
) -> list[Imported]:
    allowed = tuple(a for a in ages if a in IMPORTABLE)
    info, clips = speakers(root), _lines(root)
    work_dir.mkdir(parents=True, exist_ok=True)
    out: list[Imported] = []
    for spk_id in sorted(info):
        spk = info[spk_id]
        if spk.age not in allowed or spk.gender not in genders or (only and spk_id not in only):
            continue
        if limit and sum(1 for r in out if r.name) >= limit:
            break
        name = f"AISHELL3-{spk_id}"
        row = Imported(spk_id)
        if library.find("voice", name) and not replace:
            row.skipped = "exists"
        else:
            chosen = pick(clips.get(spk_id, []))
            if chosen is None:
                row.skipped = "no_clip"
            else:
                wav, text, sec = chosen
                row.name, row.text, row.seconds = name, text, round(sec, 1)
                if not dry_run:
                    prepared = work_dir / f"{name}.wav"
                    _convert(settings, wav, prepared)
                    library.add_file(
                        prepared, "voice", name, replace=replace,
                        description=f"AISHELL-3 (Apache-2.0) speaker {spk_id}, {spk.gender}, age {AGES[spk.age]}, "
                                    f"{spk.accent} accent",
                        tags=[spk.gender, f"age:{AGES[spk.age]}", f"accent:{spk.accent}", "aishell3", "real-speaker"],
                        meta={"consent": consent, "text": text, "source": "aishell3", "speaker": spk_id,
                              "clip": wav.name})
        out.append(row)
    return out
