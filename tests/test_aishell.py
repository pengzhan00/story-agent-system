"""AISHELL-3 speakers become library voices: one good clip each, minors left out, consent not assumed."""
from __future__ import annotations

import subprocess
from pathlib import Path

from storyagent import aishell
from storyagent.media.ledger import Ledger
from storyagent.media.library import Library

from .story_kit import make_engine

CHARS = "今天的天气非常好我们一起出去走走吧"


def _corpus(root: Path) -> None:
    (root / "train" / "wav").mkdir(parents=True)
    info = ["# header", "SSB0001\tB\tfemale\tnorth", "SSB0002\tA\tmale\tnorth", "SSB0003\tC\tmale\tsouth",
            "SSB0004\tD\tfemale\tnorth"]
    (root / "spk-info.txt").write_text("\n".join(info) + "\n")
    rows = []
    for spk, secs in (("SSB0001", (2, 8, 30)), ("SSB0002", (8,)), ("SSB0003", (7,))):
        (root / "train" / "wav" / spk).mkdir()
        for i, sec in enumerate(secs, 1):
            name = f"{spk}{i:04d}.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                            "-t", str(sec), "-acodec", "pcm_s16le", str(root / "train" / "wav" / spk / name)],
                           check=True)
            rows.append(f"{name}\t" + " ".join(f"{c} zi4" for c in CHARS))
    (root / "train" / "content.txt").write_text("\n".join(rows) + "\n")


def test_a_clip_per_speaker_nearest_eight_seconds_and_no_one_under_fourteen(tmp_path: Path) -> None:
    engine, _, settings = make_engine(tmp_path)
    corpus = tmp_path / "corpus"
    _corpus(corpus)
    lib = Library(engine.db, settings, Ledger(engine.db, settings))
    rows = aishell.import_speakers(settings, lib, corpus, tmp_path / "work")
    made = {r.speaker: r for r in rows if r.name}
    assert sorted(made) == ["SSB0001", "SSB0003"]                       # A (SSB0002) is never imported
    assert {r.speaker: r.skipped for r in rows if r.skipped} == {"SSB0004": "no_clip"}
    assert made["SSB0001"].seconds == 8.0 and made["SSB0001"].text == CHARS      # the pinyin is gone
    item = lib.find("voice", "AISHELL3-SSB0001")
    assert item is not None and "female" in item.tags and "age:14-25" in item.tags and "real-speaker" in item.tags
    assert item.meta["consent"] is False and item.meta["text"] == CHARS            # consent is not assumed
    assert item.asset.sha256 and abs((item.asset.duration_sec or 0) - 8.0) < 0.2
    again = aishell.import_speakers(settings, lib, corpus, tmp_path / "work")
    assert {r.skipped for r in again if r.skipped} == {"exists", "no_clip"}
    said = aishell.import_speakers(settings, lib, corpus, tmp_path / "work", only=("SSB0003",), consent=True,
                                   replace=True)
    assert [r.speaker for r in said if r.name] == ["SSB0003"]
    assert lib.find("voice", "AISHELL3-SSB0003").meta["consent"] is True           # type: ignore[union-attr]


def test_a_corpus_line_keeps_the_characters_only() -> None:
    assert aishell.transcript("\u597d hao3 \u4e16 shi4 abc") == "\u597d\u4e16"
