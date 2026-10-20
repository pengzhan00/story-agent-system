"""CosyVoice2 batch runner. Executed by the CosyVoice virtualenv's Python, NOT by the storyagent environment.

    python cosyvoice_zero_shot.py <config.json>

config.json:
    root       CosyVoice checkout (cwd; its third_party/Matcha-TTS goes on sys.path)
    model_dir  pretrained model directory (relative to root or absolute)
    engine     cv2 (CosyVoice2) | cv3 (CosyVoice 3.0: AutoModel, 3 s references, prompt_text needs <|endofprompt|>)
    mode       zero_shot (needs ref_text) | cross_lingual | instruct (needs instruct)
    ref_wav    reference voice, a FILE path (this CosyVoice version rejects tensors)
    ref_text   transcript of ref_wav            instruct   style instruction for mode=instruct
    speed      1.0 = normal                     out_dir    where <id>.wav (mono, model sample rate) is written
    seed       optional; torch seed for item i is seed + i
    items      [{"id": ..., "text": ...}]       one model load serves all items

Progress is printed as JSON lines (event = loaded | item | failed | done). Long lines are split by the model into
several chunks; ALL chunks are joined (a loop that stops at the first chunk truncates long dialogue).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


def emit(**kw: Any) -> None:
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def main(cfg: dict[str, Any]) -> int:
    root = cfg["root"]
    os.chdir(root)
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "third_party", "Matcha-TTS"))

    import numpy as np
    import soundfile as sf
    engine = cfg.get("engine", "cv2")
    if engine == "cv3":
        from cosyvoice.cli.cosyvoice import AutoModel

        model = AutoModel(model_dir=cfg["model_dir"], load_trt=False, load_vllm=False, fp16=False)
        if "<|endofprompt|>" not in cfg.get("ref_text", ""):  # CosyVoice3's LLM asserts on it
            cfg["ref_text"] = f"You are a helpful assistant.<|endofprompt|>{cfg.get('ref_text', '')}"
    else:
        from cosyvoice.cli.cosyvoice import CosyVoice2

        model = CosyVoice2(cfg["model_dir"], load_jit=False, load_trt=False, fp16=False)
    rate = model.sample_rate
    emit(event="loaded", sample_rate=rate)
    mode, speed = cfg.get("mode", "zero_shot"), float(cfg.get("speed", 1.0))
    items = cfg["items"]
    os.makedirs(cfg["out_dir"], exist_ok=True)
    failed = 0
    for index, item in enumerate(items):
        text = item["text"].strip()
        try:
            if cfg.get("seed") is not None:  # reproducible sampling; items differ by their index
                import torch

                torch.manual_seed(int(cfg["seed"]) + index)
            if mode == "zero_shot":
                gen = model.inference_zero_shot(text, cfg["ref_text"], cfg["ref_wav"], stream=False, speed=speed)
            elif mode == "cross_lingual":
                gen = model.inference_cross_lingual(text, cfg["ref_wav"], stream=False, speed=speed)
            elif mode == "instruct":
                gen = model.inference_instruct2(text, cfg["instruct"], cfg["ref_wav"], stream=False, speed=speed)
            else:
                raise ValueError(f"unknown mode {mode}")
            chunks = [np.squeeze(o["tts_speech"].detach().cpu().numpy()) for o in gen]
            if not chunks:
                raise RuntimeError("the model returned no audio")
            audio = np.concatenate(chunks)
            sf.write(os.path.join(cfg["out_dir"], f"{item['id']}.wav"), audio, rate)
            emit(event="item", id=item["id"], index=index, total=len(items), seconds=round(len(audio) / rate, 3))
        except Exception as exc:  # report and continue: one bad line must not lose the others
            failed += 1
            emit(event="failed", id=item["id"], index=index, total=len(items), error=str(exc)[:300])
    emit(event="done", count=len(items) - failed, failed=failed)
    return 1 if failed else 0


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as fh:
        sys.exit(main(json.load(fh)))
