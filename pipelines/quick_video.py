"""
QuickVideoGenerator — 指定时长短视频一键生成

流程:
  用户输入 prompt + duration_sec
    → LLM 自动拆分成 N 个分镜描述
    → 依次调用 RenderDispatcher 渲染每帧段
    → ffmpeg 拼接 + 可选 BGM
    → 返回最终 mp4

使用示例:
    gen = QuickVideoGenerator(output_dir="/tmp/qv")
    for update in gen.generate("海边日落，海鸥飞翔", duration_sec=30):
        print(update["msg"])
    print(update["output_path"])
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Generator

import requests

PROJECT_ROOT = Path(__file__).parent.parent

# ──────────────────────────────────────────────────────────────────────────────
# 默认参数
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# LTX 2.3 分辨率规则：宽高均为 32 的整数倍，帧数为 8n+1
# 4:3 各档均为精确比例；9:16 / 16:9 在 720p/1080p 有 <2.3% 偏差（官方训练值）
# ──────────────────────────────────────────────────────────────────────────────

# RESOLUTION_TABLE[(aspect_alias, quality)] = (width, height, fps, frames)
# quality: "preview" | "标准" | "720p" | "1080p"
RESOLUTION_TABLE: dict[tuple[str, str], tuple[int, int, int, int]] = {
    # 竖屏 9:16
    ("竖屏 9:16", "preview"):  (288,  512,  8,  9),
    ("竖屏 9:16", "标准"):      (576,  1024, 16, 49),
    ("竖屏 9:16", "720p"):     (736,  1280, 16, 49),
    ("竖屏 9:16", "1080p"):    (1088, 1920, 16, 25),
    # 横屏 16:9
    ("横屏 16:9", "preview"):  (512,  288,  8,  9),
    ("横屏 16:9", "标准"):      (1024, 576,  16, 49),
    ("横屏 16:9", "720p"):     (1280, 736,  16, 49),
    ("横屏 16:9", "1080p"):    (1920, 1088, 16, 25),
    # 横屏 4:3（精确比例）
    ("横屏 4:3",  "preview"):  (384,  288,  8,  9),
    ("横屏 4:3",  "标准"):      (768,  576,  16, 49),
    ("横屏 4:3",  "720p"):     (1024, 768,  16, 49),
    ("横屏 4:3",  "1080p"):    (1280, 960,  16, 25),
    # 竖屏 3:4（精确比例）
    ("竖屏 3:4",  "preview"):  (288,  384,  8,  9),
    ("竖屏 3:4",  "标准"):      (576,  768,  16, 49),
    ("竖屏 3:4",  "720p"):     (768,  1024, 16, 49),
    ("竖屏 3:4",  "1080p"):    (960,  1280, 16, 25),
    # 方形 1:1
    ("方形 1:1",  "preview"):  (288,  288,  8,  9),
    ("方形 1:1",  "标准"):      (576,  576,  16, 49),
    ("方形 1:1",  "720p"):     (768,  768,  16, 49),
    ("方形 1:1",  "1080p"):    (1088, 1088, 16, 25),
}

# 别名扩展（英文 / 老代码兼容）
_ALIAS: dict[str, str] = {
    "portrait": "竖屏 9:16", "landscape": "横屏 16:9",
    "square": "方形 1:1",    "4:3": "横屏 4:3",  "3:4": "竖屏 3:4",
}

def _resolve_aspect(aspect: str) -> str:
    return _ALIAS.get(aspect, aspect)

def get_preset(aspect_ratio: str, quality: str = "标准") -> dict:
    """返回 {width, height, fps, frames} 字典。不存在时回退到「标准」预览。"""
    aspect = _resolve_aspect(aspect_ratio)
    key = (aspect, quality)
    if key not in RESOLUTION_TABLE:
        key = (aspect, "标准")
    if key not in RESOLUTION_TABLE:
        key = ("竖屏 9:16", "标准")
    w, h, fps, frames = RESOLUTION_TABLE[key]
    return {"width": w, "height": h, "fps": fps, "frames": frames}

def get_smoke_preset(aspect_ratio: str) -> dict:
    return get_preset(aspect_ratio, "preview")

# 向后兼容的静态预设（指向「标准」档）
PORTRAIT_PRESET  = get_preset("竖屏 9:16", "标准")
LANDSCAPE_PRESET = get_preset("横屏 16:9", "标准")
SQUARE_PRESET    = get_preset("方形 1:1",  "标准")
PORTRAIT_SMOKE_PRESET  = get_smoke_preset("竖屏 9:16")
LANDSCAPE_SMOKE_PRESET = get_smoke_preset("横屏 16:9")
SQUARE_SMOKE_PRESET    = get_smoke_preset("方形 1:1")

ASPECT_PRESETS = {k: get_preset(k, "标准") for k in
    ["竖屏 9:16", "横屏 16:9", "横屏 4:3", "竖屏 3:4", "方形 1:1",
     "portrait", "landscape", "square"]}

SMOKE_ASPECT_PRESETS = {k: get_smoke_preset(k) for k in ASPECT_PRESETS}

# LTX 2.3 每次生成固定帧数 49 @ 16fps ≈ 3.06s
# 如果用户目标时长短于单 shot，直接生成 1 shot 然后截断
_DEFAULT_SHOT_SEC = 49 / 16  # 3.0625s

LOCAL_STORYBOARD_PIPELINE = "local_storyboard_reel"
QWEN_CLOUD_PIPELINE = "qwen_wan_cloud"
LTX_LOCAL_PIPELINE = "ltx_t2v"
LTX_LOCAL_PIPELINE_ONLY = "ltx_t2v_only"   # 纯 t2v，无 i2v 参考帧，速度更快
LOCAL_PRODUCTION_PIPELINES = {LOCAL_STORYBOARD_PIPELINE, QWEN_CLOUD_PIPELINE}


# ──────────────────────────────────────────────────────────────────────────────
# LLM 分镜规划
# ──────────────────────────────────────────────────────────────────────────────

_STORYBOARD_PROMPT = """\
你是专业的视频分镜师。用户想制作一段 **{duration}秒** 的短视频，主题描述如下：

「{user_prompt}」

请将其拆分为 **{n_shots} 个连续镜头**，每个镜头约 {shot_sec:.1f} 秒。
要求：
- 镜头间有自然的画面过渡
- 每个镜头的画面描述要具体（包含：场景、光线、镜头角度/运动、主体动作）
- en_prompt 用于 AI 视频生成，必须是英文，详尽专业

以 JSON 数组格式输出，不要加任何额外说明：
[
  {{
    "shot": 1,
    "en_prompt": "...(英文，50~120词，用于视频生成)",
    "cn_desc": "...(中文，15字以内，简短说明)"
  }},
  ...
]
"""

_NEGATIVE_PROMPT = (
    "blurry, low quality, distorted, deformed, ugly, bad anatomy, "
    "watermark, text, extra limbs, fused fingers, poorly drawn face"
)


def _plan_storyboard(user_prompt: str, n_shots: int, duration: float,
                     shot_sec: float, model: str) -> list[dict]:
    """调用 LLM 生成分镜计划，返回 [{shot, en_prompt, cn_desc}, ...]。"""
    from core.ollama_client import generate, _clean_json_candidate

    system_msg = (
        "You are a video storyboard expert. "
        "Output ONLY a valid JSON array. "
        "Do NOT think, reason, or add any explanation. "
        "Start your response with [ and end with ]. "
        "No markdown, no code fences, no commentary."
    )
    user_msg = _STORYBOARD_PROMPT.format(
        duration=int(duration),
        user_prompt=user_prompt,
        n_shots=n_shots,
        shot_sec=shot_sec,
    )

    raw = generate(
        prompt=user_msg,
        system=system_msg,
        model=model,
        temperature=0.7,
        max_tokens=2048,
        agent_type="quick_video_storyboard",
    )

    # 清洗：去 <think> / markdown fence / 非法控制字符
    text = _clean_json_candidate(raw)

    # 提取 JSON 数组（兼容前后有多余文字的情况）
    start = text.find("[")
    end   = text.rfind("]") + 1
    if start == -1 or end == 0:
        raise ValueError(f"LLM 未返回 JSON 数组:\n{text[:300]}")

    json_str = text[start:end]

    # 多策略解析：直接 → 控制字符清洗 → 结构修复 → 放弃
    import re as _re
    from core.ollama_client import _sanitize_control_chars

    parse_err = None
    for attempt, candidate in enumerate([json_str, _sanitize_control_chars(json_str)]):
        try:
            shots = json.loads(candidate)
            if isinstance(shots, list) and shots:
                return shots
            raise ValueError("LLM 返回的 JSON 为空列表")
        except (json.JSONDecodeError, ValueError) as e:
            parse_err = e
            if attempt == 0:
                continue  # 尝试下一个候选

    # 最后尝试：用 regex 抽取每个 { ... } 对象
    objects = _re.findall(r'\{[^{}]+\}', json_str, _re.DOTALL)
    if objects:
        repaired = []
        for obj in objects:
            try:
                repaired.append(json.loads(_sanitize_control_chars(obj)))
            except json.JSONDecodeError:
                pass
        if repaired:
            return repaired

    raise ValueError(f"JSON 解析彻底失败: {parse_err}") from parse_err


_CONSISTENCY_PROMPT = """\
You are a film character designer. Based on the user's story description and storyboard shots below, \
extract a concise character/style consistency bible.

Story: {user_prompt}

Storyboard (first 3 shots for reference):
{shots_preview}

Output ONLY a valid JSON object with these exact keys:
{{
  "character": "detailed appearance of the MAIN character (hair, clothing, age, ethnicity, expression) — English, max 60 words",
  "style": "visual style, lighting, color tone, camera style — English, max 30 words",
  "setting": "environment/location keywords — English, max 20 words"
}}
No markdown, no code fences, just raw JSON.
"""


def _extract_character_bible(user_prompt: str, shots: list[dict], model: str) -> dict:
    """
    调用 LLM 从故事+分镜提取角色一致性圣经。
    失败时返回空 dict（不阻断主流程）。
    """
    from core.ollama_client import generate_json
    shots_preview = "\n".join(
        f"Shot {s.get('shot', i+1)}: {s.get('en_prompt', '')[:120]}"
        for i, s in enumerate(shots[:3])
    )
    try:
        bible = generate_json(
            prompt=_CONSISTENCY_PROMPT.format(
                user_prompt=user_prompt,
                shots_preview=shots_preview,
            ),
            system="You output only raw JSON. No markdown, no explanation.",
            model=model,
            temperature=0.2,
            max_tokens=512,
            agent_type="character_bible",
        )
        # 只保留已知字段，防止 LLM 乱加
        return {
            "character": str(bible.get("character", "")).strip(),
            "style":     str(bible.get("style", "")).strip(),
            "setting":   str(bible.get("setting", "")).strip(),
        }
    except Exception:
        return {}


def _enrich_shot_prompt(shot_prompt: str, bible: dict) -> str:
    """
    将角色圣经注入镜头 prompt 开头，让模型在每个镜头都"看到"一致的角色描述。
    格式：[Character: ...] [Style: ...] {original prompt}
    """
    if not bible:
        return shot_prompt
    parts = []
    if bible.get("character"):
        parts.append(f"[Character: {bible['character']}]")
    if bible.get("style"):
        parts.append(f"[Style: {bible['style']}]")
    if bible.get("setting"):
        parts.append(f"[Setting: {bible['setting']}]")
    prefix = " ".join(parts)
    return f"{prefix} {shot_prompt}" if prefix else shot_prompt


def _derive_project_seed(user_prompt: str) -> int:
    """从故事 prompt 推导项目级固定种子（跨镜头保持一致性基础噪声）。"""
    import hashlib
    return int(hashlib.sha256(user_prompt.encode()).hexdigest()[:8], 16) % (2 ** 31)


def _fallback_storyboard(user_prompt: str, n_shots: int) -> list[dict]:
    """LLM 不可用时的保底分镜（原 prompt 重复使用，加序号修饰）。"""
    variations = [
        "wide establishing shot, cinematic lighting",
        "medium shot, natural movement, golden hour",
        "close-up detail shot, shallow depth of field",
        "tracking shot following subject, dynamic movement",
        "aerial overview, sweeping camera motion",
        "slow motion detail, soft bokeh background",
        "low angle shot looking up, dramatic perspective",
        "over-the-shoulder shot, storytelling angle",
    ]
    result = []
    for i in range(n_shots):
        var = variations[i % len(variations)]
        result.append({
            "shot": i + 1,
            "en_prompt": f"{user_prompt}, {var}, high quality, 4K",
            "cn_desc": f"镜头 {i+1}",
        })
    return result


# ──────────────────────────────────────────────────────────────────────────────
# ffmpeg 工具
# ──────────────────────────────────────────────────────────────────────────────

def _ffmpeg(*args, timeout: int = 300) -> tuple[bool, str]:
    import subprocess
    cmd = ["ffmpeg", "-y"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stderr[-400:] if r.returncode != 0 else ""
    except subprocess.TimeoutExpired:
        return False, "ffmpeg 超时"
    except FileNotFoundError:
        return False, "ffmpeg 未安装"


def _get_image_output(comfyui_outputs: dict) -> list[dict]:
    files: list[dict] = []
    if not comfyui_outputs:
        return files
    for node in comfyui_outputs.values():
        for img in node.get("images", []) or []:
            if isinstance(img, dict) and img.get("filename"):
                files.append(img)
    return files


def _build_storyboard_image_workflow(
    *,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    ckpt_name: str,
    filename_prefix: str,
    steps: int,
    cfg: float,
    seed: int,
) -> dict:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": prompt},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": negative_prompt},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": int(seed),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": filename_prefix},
        },
    }


def _make_motion_video_from_image(
    image_path: str,
    output_path: str,
    *,
    width: int,
    height: int,
    fps: int,
    frames: int,
    motion_variant: int = 0,
) -> bool:
    duration = max(frames / max(fps, 1), 0.8)
    zoom_speed = "0.0012" if motion_variant % 2 == 0 else "0.0008"
    scale_filter = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    zoompan_filter = (
        f"{scale_filter},"
        f"zoompan=z='min(zoom+{zoom_speed},1.10)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={width}x{height}:fps={fps},format=yuv420p"
    )
    ok, _ = _ffmpeg(
        "-loop", "1", "-i", image_path,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(duration),
        "-vf", zoompan_filter,
        "-map", "0:v", "-map", "1:a",
        "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
        timeout=300,
    )
    return ok and Path(output_path).exists()


def _download_file(url: str, output_path: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "story-agent-system/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(output_path, "wb") as f:
        shutil.copyfileobj(resp, f)


def run_qwen_cloud_video_task(
    *,
    prompt: str,
    duration_sec: float,
    aspect_ratio: str,
    output_path: str,
    api_key: str,
    api_base: str = "",
    model: str = "",
    poll_interval: int = 15,
) -> tuple[bool, str]:
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "missing qwen cloud api key"

    base_url = (api_base or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
    model_name = (model or "qwen-vl-max").strip()
    endpoint = f"{base_url}/services/aigc/video-generation/video-synthesis"
    size = {
        "portrait": "576*1024",
        "landscape": "1024*576",
        "square": "960*960",
        "竖屏 9:16": "576*1024",
        "横屏 16:9": "1024*576",
        "方形 1:1": "960*960",
    }.get(aspect_ratio, "576*1024")
    duration = int(max(5, min(round(duration_sec), 15)))
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": model_name,
        "input": {"prompt": prompt},
        "parameters": {
            "size": size,
            "duration": duration,
            "prompt_extend": True,
        },
    }
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    task_id = ((data.get("output") or {}).get("task_id") or "").strip()
    if not task_id:
        return False, f"no task_id returned: {data}"

    poll_url = f"{base_url}/tasks/{task_id}"
    deadline = time.time() + 3600
    while time.time() < deadline:
        poll = requests.get(poll_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
        poll.raise_for_status()
        pdata = poll.json()
        output = pdata.get("output") or {}
        status = (output.get("task_status") or "").upper()
        if status == "SUCCEEDED":
            video_url = output.get("video_url")
            if not video_url:
                return False, f"task succeeded but no video_url: {pdata}"
            _download_file(video_url, output_path)
            return True, output_path
        if status in {"FAILED", "CANCELED", "UNKNOWN"}:
            return False, output.get("message") or f"task failed: {status}"
        time.sleep(max(5, poll_interval))
    return False, "qwen cloud task timeout"


def _get_duration(path: str) -> float:
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _extract_last_frame(video_path: str, out_png: str) -> bool:
    """用 ffmpeg 提取视频最后一帧，保存为 PNG。成功返回 True。"""
    import subprocess
    try:
        # 方法1：从末尾 0.5s 取 1 帧（适合短视频，不会超出范围）
        r = subprocess.run(
            ["ffmpeg", "-y", "-sseof", "-0.5", "-i", video_path,
             "-vframes", "1", "-q:v", "2", out_png],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and Path(out_png).exists() and Path(out_png).stat().st_size > 0:
            return True
        # 方法2：解码整个视频，-update 1 让每帧覆盖同一文件，最终得到最后一帧
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-q:v", "2", "-update", "1", out_png],
            capture_output=True, timeout=60,
        )
        return r2.returncode == 0 and Path(out_png).exists() and Path(out_png).stat().st_size > 0
    except Exception:
        return False


def _concat_videos(video_paths: list[str], output_path: str,
                   crossfade: float = 0.3) -> bool:
    """用 ffmpeg 拼接多段视频，支持 crossfade 转场。"""
    valid = [p for p in video_paths if Path(p).exists()]
    if not valid:
        return False
    if len(valid) == 1:
        shutil.copy2(valid[0], output_path)
        return True

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        if crossfade <= 0:
            lst = tmp / "list.txt"
            lst.write_text("\n".join(f"file '{v}'" for v in valid))
            ok, _ = _ffmpeg("-f", "concat", "-safe", "0",
                            "-i", str(lst), "-c", "copy", output_path)
            if ok:
                return True
            # re-encode fallback
        # xfade 逐段叠加
        current = valid[0]
        for i, nxt in enumerate(valid[1:], 1):
            dur_a = _get_duration(current)
            offset = max(0.0, dur_a - crossfade)
            out_tmp = str(tmp / f"merged_{i}.mp4")
            ok, err = _ffmpeg(
                "-i", current, "-i", nxt,
                "-filter_complex",
                f"[0:v][1:v]xfade=transition=fade:duration={crossfade}:offset={offset:.3f}[v];"
                f"[0:a][1:a]acrossfade=d={crossfade}[a]",
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "aac", "-b:a", "128k",
                out_tmp,
            )
            if not ok:
                # simple concat fallback
                lst2 = tmp / f"list_{i}.txt"
                lst2.write_text(f"file '{current}'\nfile '{nxt}'")
                _ffmpeg("-f", "concat", "-safe", "0",
                        "-i", str(lst2), "-c:v", "libx264", "-preset", "fast",
                        "-c:a", "aac", out_tmp)
            current = out_tmp
        shutil.copy2(current, output_path)
    return Path(output_path).exists()


def _trim_video(input_path: str, output_path: str, target_duration: float) -> bool:
    """精确截断到目标时长。"""
    ok, _ = _ffmpeg(
        "-i", input_path,
        "-t", str(target_duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    )
    return ok


def _add_bgm(video_path: str, bgm_path: str, output_path: str) -> bool:
    """叠加 BGM（循环+降音量）到视频。"""
    dur = _get_duration(video_path)
    ok, _ = _ffmpeg(
        "-i", video_path,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        f"[1:a]volume=0.20,atrim=duration={dur}[bgm];"
        "[0:a][bgm]amix=inputs=2:duration=first[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        output_path,
    )
    return ok


# ──────────────────────────────────────────────────────────────────────────────
# 主类
# ──────────────────────────────────────────────────────────────────────────────

class QuickVideoGenerator:
    """
    一键生成指定时长短视频。

    generate() 是一个 generator，每步 yield 一个 dict:
      { "step": str, "msg": str, "progress": float (0~1),
        "output_path": Optional[str], "error": Optional[str] }
    最后一次 yield 的 output_path 为最终视频路径（成功时）。
    """

    def __init__(
        self,
        output_dir: str | Path = PROJECT_ROOT / "output" / "quick_videos",
        llm_model: str = "",
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._llm_model = llm_model

    def _resolve_model(self) -> str:
        if self._llm_model:
            return self._llm_model
        try:
            from core.ollama_client import DEFAULT_MODEL
            return DEFAULT_MODEL
        except Exception:
            return "qwen3:8b"

    def _render_storyboard_image(
        self,
        *,
        prompt: str,
        out_dir: Path,
        shot_num: int,
        width: int,
        height: int,
        preview_mode: bool,
    ) -> Path:
        from core.service_ports import comfyui_api_base
        from pipelines.render_pipeline import submit_workflow, wait_for_completion_result, _COMFYUI_OUTPUT_DIR

        ckpt_name = "animagine-xl-3.1.safetensors"
        ckpt_path = Path(os.path.expanduser("~/myworkspace/ComfyUI_models/checkpoints")) / ckpt_name
        if not ckpt_path.exists():
            ckpt_name = "sd_xl_base_1.0.safetensors"

        filename_prefix = f"quickvideo/storyboard_{int(time.time())}_{shot_num:03d}"
        workflow = _build_storyboard_image_workflow(
            prompt=prompt,
            negative_prompt=_NEGATIVE_PROMPT,
            width=width,
            height=height,
            ckpt_name=ckpt_name,
            filename_prefix=filename_prefix,
            steps=10 if preview_mode else 18,
            cfg=5.5 if preview_mode else 6.5,
            seed=int(time.time() * 1000) % (2 ** 31),
        )
        prompt_id = submit_workflow(workflow, comfyui_api_base())
        result = wait_for_completion_result(prompt_id, comfyui_api_base(), timeout=1800)
        if result["status"] != "completed":
            raise RuntimeError(result["error_message"] or "storyboard image generation failed")
        images = _get_image_output(result.get("outputs", {}))
        if not images:
            raise RuntimeError("storyboard image generation returned no image")
        img = images[0]
        subfolder = img.get("subfolder", "")
        src = (_COMFYUI_OUTPUT_DIR / subfolder / img["filename"]) if subfolder else (_COMFYUI_OUTPUT_DIR / img["filename"])
        if not src.exists():
            raise FileNotFoundError(f"storyboard image missing: {src}")
        dst = out_dir / f"shot_{shot_num:03d}.png"
        shutil.copy2(src, dst)
        return dst

    def _generate_qwen_cloud_video(
        self,
        *,
        prompt: str,
        duration_sec: float,
        aspect_ratio: str,
        out_dir: Path,
        ts: int,
        progress_fn,
    ) -> Generator[dict, None, None]:
        api_key = (
            os.getenv("QWEN_VIDEO_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("QWEN_API_KEY")
            or ""
        ).strip()
        if not api_key:
            yield progress_fn("error", "❌ 未配置 `QWEN_VIDEO_API_KEY` / `DASHSCOPE_API_KEY`", 1.0, error="missing qwen cloud api key")
            return

        base_url = (os.getenv("QWEN_VIDEO_API_BASE") or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
        endpoint = f"{base_url}/services/aigc/video-generation/video-synthesis"
        model = (os.getenv("QWEN_VIDEO_MODEL") or "wan2.2-t2v-plus").strip()
        size = {
            "portrait": "576*1024",
            "landscape": "1024*576",
            "square": "960*960",
            "竖屏 9:16": "576*1024",
            "横屏 16:9": "1024*576",
            "方形 1:1": "960*960",
        }.get(aspect_ratio, "576*1024")
        duration = int(max(5, min(round(duration_sec), 15)))
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        payload = {
            "model": model,
            "input": {"prompt": prompt},
            "parameters": {
                "size": size,
                "duration": duration,
                "prompt_extend": True,
            },
        }
        yield progress_fn("cloud_submit", f"☁️ 已提交 Qwen 云端视频任务：{model} · {size} · {duration}s", 0.15)
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        task_id = ((data.get("output") or {}).get("task_id") or "").strip()
        if not task_id:
            raise RuntimeError(f"Qwen 云端未返回 task_id: {data}")

        poll_url = f"{base_url}/tasks/{task_id}"
        deadline = time.time() + 3600
        while time.time() < deadline:
            poll = requests.get(poll_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
            poll.raise_for_status()
            pdata = poll.json()
            output = pdata.get("output") or {}
            status = (output.get("task_status") or "").upper()
            if status == "SUCCEEDED":
                video_url = output.get("video_url")
                if not video_url:
                    raise RuntimeError(f"Qwen 云端成功但未返回 video_url: {pdata}")
                named_path = self.output_dir / f"quickvideo_{ts}.mp4"
                _download_file(video_url, str(named_path))
                yield progress_fn("done", f"☁️ Qwen 云端视频完成\n📁 {named_path}", 1.0, output_path=str(named_path))
                return
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                raise RuntimeError(output.get("message") or f"Qwen 云端任务失败: {status}")
            yield progress_fn("cloud_wait", f"☁️ 云端任务进行中：{status or 'PENDING'}（task_id={task_id[:8]}）", 0.35)
            time.sleep(15)
        raise TimeoutError("Qwen 云端视频任务超时")

    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        duration_sec: float = 30.0,
        pipeline_name: str = "",
        aspect_ratio: str = "portrait",
        reference_image_path: str = "",
        bgm_prompt: str = "",
        crossfade: float = 0.3,
        allow_fallback: bool = True,
        audio_mode: str = "auto",
        preview_mode: bool = False,
        quality: str = "标准",
    ) -> Generator[dict, None, None]:
        """
        主入口。yield 进度更新，最终 yield 包含 output_path。

        Args:
            prompt:        视频主题描述（中英文均可）
            duration_sec:  目标时长（秒），15~300
            pipeline_name: 指定渲染管线，空字符串=自动选择
            aspect_ratio:  "portrait" | "landscape" | "square"
            reference_image_path: 真实参考图路径；生产主链推荐必填
            bgm_prompt:    背景音乐描述；空=不加 BGM
            crossfade:     镜头转场时长（秒），0=硬切
        """
        reference_image_path = (reference_image_path or "").strip()
        has_reference = bool(reference_image_path and Path(reference_image_path).exists())
        t0 = time.time()
        ts = int(t0)
        out_dir = self.output_dir / str(ts)
        out_dir.mkdir(parents=True, exist_ok=True)

        def _prog(step: str, msg: str, progress: float, **kw) -> dict:
            return {"step": step, "msg": msg, "progress": progress,
                    "output_path": None, "error": None, **kw}

        # ── 0. 参数校验 ──────────────────────────────────────────────
        duration_sec = max(5.0, min(float(duration_sec), 300.0))
        effective_quality = "preview" if preview_mode else (quality or "标准")
        preset = get_preset(aspect_ratio, effective_quality)
        shot_sec = preset["frames"] / preset["fps"]  # 3.0625s for 49@16
        n_shots = max(1, math.ceil(duration_sec / shot_sec))

        yield _prog(
            "init",
            (
                f"🎬 计划生成 {n_shots} 个镜头 × {shot_sec:.1f}s ≈ {n_shots*shot_sec:.0f}s 视频"
                f" ｜ 输出 {preset['width']}×{preset['height']} @ {preset['fps']}fps"
                f"{' ｜ 试片模式' if preview_mode else ''}"
            ),
            0.02,
        )

        # ── 1. LLM 分镜规划 ──────────────────────────────────────────
        yield _prog("plan", "🤖 LLM 正在规划分镜…", 0.05)
        try:
            shots = _plan_storyboard(
                user_prompt=prompt,
                n_shots=n_shots,
                duration=duration_sec,
                shot_sec=shot_sec,
                model=self._resolve_model(),
            )
            yield _prog("plan", f"✅ 分镜规划完成：{len(shots)} 个镜头", 0.10)
        except Exception as e:
            yield _prog("plan", f"⚠️ LLM 规划失败（{e}），使用保底分镜", 0.10)
            shots = _fallback_storyboard(prompt, n_shots)

        # ── 1.5. 提取角色一致性圣经 ─────────────────────────────────────────
        yield _prog("plan", "🎭 提取角色一致性描述…", 0.11)
        character_bible: dict = {}
        project_seed: int = _derive_project_seed(prompt)
        try:
            character_bible = _extract_character_bible(prompt, shots, self._resolve_model())
            if character_bible and any(character_bible.values()):
                bible_summary = " | ".join(f"{k}: {v[:40]}" for k, v in character_bible.items() if v)
                yield _prog("plan", f"✅ 角色圣经：{bible_summary}", 0.115)
            else:
                yield _prog("plan", "⚠️ 角色圣经提取为空，将直接使用分镜 prompt", 0.115)
        except Exception as _be:
            yield _prog("plan", f"⚠️ 角色圣经提取失败（{_be}），跳过", 0.115)

        if not pipeline_name:
            pipeline_name = LTX_LOCAL_PIPELINE_ONLY if preview_mode else (LTX_LOCAL_PIPELINE if has_reference else LOCAL_STORYBOARD_PIPELINE)

        if pipeline_name == QWEN_CLOUD_PIPELINE:
            try:
                yield from self._generate_qwen_cloud_video(
                    prompt=prompt,
                    duration_sec=duration_sec,
                    aspect_ratio=aspect_ratio,
                    out_dir=out_dir,
                    ts=ts,
                    progress_fn=_prog,
                )
            except Exception as e:
                yield _prog("error", f"❌ Qwen 云端视频失败: {e}", 1.0, error=str(e))
            return

        if pipeline_name == LOCAL_STORYBOARD_PIPELINE:
            shot_videos: list[str] = []
            render_progress_per_shot = 0.72 / max(n_shots, 1)
            for idx, shot in enumerate(shots):
                shot_num = idx + 1
                cn_desc = shot.get("cn_desc", f"镜头{shot_num}")
                en_prompt = shot.get("en_prompt", prompt)
                base_prog = 0.12 + idx * render_progress_per_shot
                yield _prog("storyboard", f"🖼️ 生成分镜图 {shot_num}/{n_shots}：{cn_desc}", base_prog)
                try:
                    if has_reference and shot_num == 1:
                        image_path = Path(reference_image_path)
                    else:
                        image_path = self._render_storyboard_image(
                            prompt=en_prompt,
                            out_dir=out_dir,
                            shot_num=shot_num,
                            width=preset["width"],
                            height=preset["height"],
                            preview_mode=preview_mode,
                        )
                    shot_video = out_dir / f"shot_{shot_num:03d}.mp4"
                    if not _make_motion_video_from_image(
                        str(image_path),
                        str(shot_video),
                        width=preset["width"],
                        height=preset["height"],
                        fps=preset["fps"],
                        frames=preset["frames"],
                        motion_variant=idx,
                    ):
                        raise RuntimeError("ffmpeg motion render failed")
                    shot_videos.append(str(shot_video))
                    yield _prog("storyboard", f"✅ 分镜成片 {shot_num}/{n_shots} 完成", base_prog + render_progress_per_shot)
                except Exception as e:
                    yield _prog("storyboard", f"⚠️ 分镜 {shot_num} 失败: {e}", base_prog + render_progress_per_shot)
            if not shot_videos:
                yield _prog("error", "❌ 本机友好成片未生成任何镜头", 1.0, error="no storyboard shots rendered")
                return

            yield _prog("concat", f"🔗 拼接 {len(shot_videos)} 个镜头…", 0.88)
            concat_path = str(out_dir / "concat.mp4")
            concat_ok = _concat_videos(shot_videos, concat_path, crossfade=crossfade)
            if not concat_ok:
                concat_path = shot_videos[0]
                yield _prog("concat", "⚠️ 拼接失败，使用第一个镜头作为输出", 0.90)

            trimmed_path = str(out_dir / "trimmed.mp4")
            actual_dur = _get_duration(concat_path)
            if actual_dur > duration_sec + 0.5:
                yield _prog("trim", f"✂️ 截断到 {duration_sec:.0f}s（实际 {actual_dur:.1f}s）", 0.92)
                if not _trim_video(concat_path, trimmed_path, duration_sec):
                    trimmed_path = concat_path
            else:
                trimmed_path = concat_path

            final_path = str(out_dir / "final.mp4")
            if bgm_prompt and bgm_prompt.strip():
                yield _prog("bgm", f"🎵 生成 BGM：{bgm_prompt[:40]}…", 0.94)
                bgm_out = str(out_dir / "bgm.mp3")
                try:
                    from pipelines.audio_pipeline import generate_music, generate_music_acestep, generate_music_acestep_direct, generate_music_ffmpeg
                    bgm_ok = False
                    audio_mode = (audio_mode or "auto").strip().lower()
                    audio_duration = int(duration_sec) + 5
                    if audio_mode == "acestep_only":
                        bgm_ok = generate_music_acestep_direct(prompt=bgm_prompt, output_path=bgm_out, duration=audio_duration) or generate_music_acestep(prompt=bgm_prompt, output_path=bgm_out, duration=audio_duration)
                    elif audio_mode == "ffmpeg_only":
                        bgm_ok = generate_music_ffmpeg(prompt=bgm_prompt, output_path=bgm_out, duration=audio_duration)
                    else:
                        bgm_ok = generate_music(prompt=bgm_prompt, output_path=bgm_out, duration=audio_duration)
                    if bgm_ok and Path(bgm_out).exists():
                        _add_bgm(trimmed_path, bgm_out, final_path)
                        yield _prog("bgm", "✅ BGM 已添加", 0.97)
                    else:
                        final_path = trimmed_path
                        yield _prog("bgm", "⚠️ BGM 生成失败，使用无 BGM 版本", 0.97)
                except Exception as e:
                    final_path = trimmed_path
                    yield _prog("bgm", f"⚠️ BGM 异常: {e}", 0.97)
            else:
                final_path = trimmed_path

            if not Path(final_path).exists():
                final_path = trimmed_path if Path(trimmed_path).exists() else concat_path
            named_path = str(self.output_dir / f"quickvideo_{ts}.mp4")
            try:
                shutil.copy2(final_path, named_path)
            except Exception:
                named_path = final_path
            elapsed = time.time() - t0
            final_dur = _get_duration(named_path)
            yield _prog("done", f"🎉 本机友好成片完成！视频时长 {final_dur:.1f}s，耗时 {elapsed:.0f}s\n📁 {named_path}", 1.0, output_path=named_path)
            return

        # ── 2. 获取渲染管线 ──────────────────────────────────────────
        yield _prog("pipeline", "🔧 初始化渲染管线…", 0.12)
        original_active = ""
        try:
            from pipelines.render_pipeline import get_dispatcher
            dispatcher = get_dispatcher()
            original_active = getattr(dispatcher, "active_pipeline", "") or ""
            if pipeline_name:
                dispatcher.set_active_pipeline(pipeline_name)
        except Exception as e:
            yield _prog("pipeline", f"❌ 渲染管线初始化失败: {e}", 1.0, error=str(e))
            return

        # ── 3. 逐 shot 渲染 ─────────────────────────────────────────
        # 一致性参数
        _ANCHOR_INTERVAL = 4   # 每 N 镜头重注规范锚定帧，防止外观漂移

        try:
            shot_videos: list[str] = []
            render_progress_per_shot = 0.75 / max(n_shots, 1)  # 渲染占 75% 进度
            prev_last_frame: str | None = None   # 上一镜头最后一帧（时序 i2v 衔接）
            canonical_frame: str | None = None   # 第一个成功镜头最后一帧（规范锚定，防止漂移）

            for idx, shot in enumerate(shots):
                shot_num  = idx + 1
                en_prompt = shot.get("en_prompt", prompt)
                cn_desc   = shot.get("cn_desc", f"镜头{shot_num}")
                base_prog = 0.12 + idx * render_progress_per_shot

                # ── 角色圣经注入（语义一致性）
                enriched_prompt = _enrich_shot_prompt(en_prompt, character_bible)

                yield _prog(
                    "render",
                    f"🎥 渲染镜头 {shot_num}/{n_shots}：{cn_desc}",
                    base_prog,
                )

                shot_payload = {
                    "story": {"scene_description": enriched_prompt},
                    "camera": {"duration_sec": shot_sec},
                    "output_spec": {
                        "width":  preset["width"],
                        "height": preset["height"],
                        "frames": preset["frames"],
                        "fps":    preset["fps"],
                        "quality_tier": "preview" if preview_mode else "production",
                    },
                    "project_format": "movie" if aspect_ratio in ("landscape", "横屏 16:9") else "short_drama",
                    "allow_fallback": allow_fallback,
                    "negative_prompt": _NEGATIVE_PROMPT,
                    # 噪声一致性：项目级固定种子 + 镜头偏移
                    "_project_seed": project_seed,
                    "_shot_num": shot_num,
                }

                # ── 参考图优先级（视觉一致性）──────────────────────────────
                # 1. 每 _ANCHOR_INTERVAL 镜头强制重注规范锚定帧（防止累积漂移）
                # 2. 其他镜头使用上一镜头最后一帧（时序连续性）
                # 3. 后备：用户指定的初始参考图
                use_canonical = (
                    canonical_frame
                    and Path(canonical_frame).exists()
                    and idx > 0
                    and idx % _ANCHOR_INTERVAL == 0
                )
                if use_canonical:
                    shot_payload["reference_image_path"] = canonical_frame
                elif prev_last_frame and Path(prev_last_frame).exists():
                    shot_payload["reference_image_path"] = prev_last_frame
                elif canonical_frame and Path(canonical_frame).exists():
                    shot_payload["reference_image_path"] = canonical_frame
                elif has_reference:
                    shot_payload["reference_image_path"] = reference_image_path

                out_mp4 = str(out_dir / f"shot_{shot_num:03d}.mp4")
                try:
                    render_result = dispatcher.render(shot_payload, Path(out_mp4))
                    result_path = getattr(render_result, "path", render_result)
                    result_path = Path(str(result_path)) if result_path is not None else None
                    if result_path and result_path.exists():
                        shot_videos.append(str(result_path))
                        yield _prog(
                            "render",
                            f"✅ 镜头 {shot_num} 完成",
                            base_prog + render_progress_per_shot,
                        )
                        # 提取最后一帧供下一镜头 i2v 使用
                        if idx + 1 < n_shots:
                            last_frame_png = str(out_dir / f"shot_{shot_num:03d}_lastframe.png")
                            if _extract_last_frame(str(result_path), last_frame_png):
                                prev_last_frame = last_frame_png
                                if canonical_frame is None:
                                    canonical_frame = last_frame_png  # 首帧即规范锚定
                            else:
                                prev_last_frame = None
                    else:
                        prev_last_frame = None  # 失败则不传递参考帧
                        yield _prog("render", f"⚠️ 镜头 {shot_num} 渲染返回空路径，跳过", base_prog + render_progress_per_shot)
                except Exception as e:
                    prev_last_frame = None  # 失败则不传递参考帧
                    yield _prog("render", f"⚠️ 镜头 {shot_num} 渲染失败: {e}，跳过", base_prog + render_progress_per_shot)

            if not shot_videos:
                yield _prog("error", "❌ 所有镜头均渲染失败，无法生成视频", 1.0, error="no shots rendered")
                return

            # ── 4. 拼接镜头 ──────────────────────────────────────────────
            yield _prog("concat", f"🔗 拼接 {len(shot_videos)} 个镜头…", 0.88)
            concat_path = str(out_dir / "concat.mp4")
            concat_ok   = _concat_videos(shot_videos, concat_path, crossfade=crossfade)
            if not concat_ok:
                # 保底：只用第一个
                concat_path = shot_videos[0]
                yield _prog("concat", "⚠️ 拼接失败，使用第一个镜头作为输出", 0.90)

            # ── 5. 精确截断到目标时长 ─────────────────────────────────────
            trimmed_path = str(out_dir / "trimmed.mp4")
            actual_dur   = _get_duration(concat_path)
            if actual_dur > duration_sec + 0.5:
                yield _prog("trim", f"✂️ 截断到 {duration_sec:.0f}s（实际 {actual_dur:.1f}s）", 0.92)
                if not _trim_video(concat_path, trimmed_path, duration_sec):
                    trimmed_path = concat_path
            else:
                trimmed_path = concat_path

            # ── 6. 加 BGM（可选）──────────────────────────────────────────
            final_path = str(out_dir / "final.mp4")
            if bgm_prompt and bgm_prompt.strip():
                yield _prog("bgm", f"🎵 生成 BGM：{bgm_prompt[:40]}…", 0.94)
                bgm_out = str(out_dir / "bgm.mp3")
                try:
                    from pipelines.audio_pipeline import (
                        generate_music,
                        generate_music_acestep,
                        generate_music_acestep_direct,
                        generate_music_ffmpeg,
                    )

                    bgm_ok = False
                    audio_mode = (audio_mode or "auto").strip().lower()
                    audio_duration = int(duration_sec) + 5

                    if audio_mode == "acestep_only":
                        bgm_ok = generate_music_acestep_direct(
                            prompt=bgm_prompt,
                            output_path=bgm_out,
                            duration=audio_duration,
                        ) or generate_music_acestep(
                            prompt=bgm_prompt,
                            output_path=bgm_out,
                            duration=audio_duration,
                        )
                    elif audio_mode == "ffmpeg_only":
                        bgm_ok = generate_music_ffmpeg(
                            prompt=bgm_prompt,
                            output_path=bgm_out,
                            duration=audio_duration,
                        )
                    else:
                        bgm_ok = generate_music(
                            prompt=bgm_prompt,
                            output_path=bgm_out,
                            duration=audio_duration,
                        )

                    if bgm_ok and Path(bgm_out).exists():
                        _add_bgm(trimmed_path, bgm_out, final_path)
                        yield _prog("bgm", "✅ BGM 已添加", 0.97)
                    else:
                        final_path = trimmed_path
                        yield _prog("bgm", "⚠️ BGM 生成失败，使用无 BGM 版本", 0.97)
                except Exception as e:
                    final_path = trimmed_path
                    yield _prog("bgm", f"⚠️ BGM 异常: {e}", 0.97)
            else:
                final_path = trimmed_path

            if not Path(final_path).exists():
                final_path = trimmed_path if Path(trimmed_path).exists() else concat_path

            # ── 7. 复制到带时间戳的最终路径 ──────────────────────────────
            named_path = str(self.output_dir / f"quickvideo_{ts}.mp4")
            try:
                shutil.copy2(final_path, named_path)
            except Exception:
                named_path = final_path

            elapsed = time.time() - t0
            final_dur = _get_duration(named_path)
            yield _prog(
                "done",
                f"🎉 完成！视频时长 {final_dur:.1f}s，耗时 {elapsed:.0f}s\n📁 {named_path}",
                1.0,
                output_path=named_path,
            )
        finally:
            if original_active:
                dispatcher.set_active_pipeline(original_active)
