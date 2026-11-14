"""Putting a trained LoRA where ComfyUI can read it.

mflux-train writes ``transformer.blocks.N.attn.wq.lora_A.weight``; the Krea 2 LoRAs ComfyUI reads call the same
modules ``diffusion_model.blocks.N...``. Only the key prefix differs, so the file is copied with a rewritten safetensors
header (the tensor bytes are untouched). A file that already uses ComfyUI's naming is copied as it is. Nothing here
needs a tensor library: a safetensors file is ``<8-byte header size><JSON header><raw data>``.
"""

from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path

TRAINER_PREFIX = "transformer."
COMFY_PREFIX = "diffusion_model."
_CHUNK = 16 * 1024 * 1024


class LoraFileError(Exception):
    """The file is not a safetensors file this module can read."""


def _read_header(fh) -> tuple[dict[str, object], int]:  # noqa: ANN001 - a binary file object
    raw = fh.read(8)
    if len(raw) != 8:
        raise LoraFileError("not a safetensors file")
    (size,) = struct.unpack("<Q", raw)
    if size <= 0 or size > 100_000_000:
        raise LoraFileError("not a safetensors file")
    try:
        header = json.loads(fh.read(size))
    except ValueError as exc:
        raise LoraFileError("unreadable safetensors header") from exc
    if not isinstance(header, dict):
        raise LoraFileError("unreadable safetensors header")
    return header, size


def comfy_file_name(src: Path) -> str:
    """The name a LoRA gets inside ComfyUI's loras folder: unique per source file (a trainer's output is named after
    its job), so two runs never overwrite each other."""
    return f"storyagent_{src.stem}.safetensors"


def export_for_comfy(src: Path, dest_dir: Path) -> str:
    """Copy ``src`` into ``dest_dir`` with ComfyUI's key naming and return the file name there. Idempotent: an earlier
    copy of the same source is reused."""
    name = comfy_file_name(src)
    dest = dest_dir / name
    if dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
        return name
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".partial")
    with src.open("rb") as fh:
        header, size = _read_header(fh)
        renamed = {
            (COMFY_PREFIX + k[len(TRAINER_PREFIX):] if k.startswith(TRAINER_PREFIX) else k): v
            for k, v in header.items()
        }
        body = json.dumps(renamed, separators=(",", ":")).encode()
        body += b" " * (-len(body) % 8)                     # the data that follows stays 8-byte aligned
        with tmp.open("wb") as out:
            out.write(struct.pack("<Q", len(body)))
            out.write(body)
            fh.seek(8 + size)
            shutil.copyfileobj(fh, out, _CHUNK)
    tmp.replace(dest)
    return name
