"""
能力声明式渲染管线 — Wan2.2 核心栈

  RenderPipeline (ABC)
    ├── Wan2TI2VPipeline  — Wan2.2 TI2V 5B GGUF，文本+参考图→视频（主力，production_ready）
    ├── Wan2T2VPipeline   — Wan2.2 T2V-A14B GGUF，纯文本→视频（待下载模型）
    ├── Wan2VACEPipeline  — Wan2.2 VACE-Fun-A14B，视频→视频（待下载模型）
    └── StubPipeline      — ffmpeg 纯黑帧占位（兜底，无 ComfyUI 依赖）

  RenderDispatcher
    - 启动时调用 /object_info 探测，标记每条管线是否可用
    - render() 按 priority 顺序尝试，失败自动回退
    - capability_matrix() 可暴露给 UI 展示兼容矩阵
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_PIPELINES_DIR = Path(__file__).parent
_PROJECT_ROOT = _PIPELINES_DIR.parent
_COMFYUI_OUTPUT_DIR = Path(os.path.expanduser("~/Documents/ComfyUI/output"))

NEGATIVE_PROMPT = (
    "blurry, low quality, distorted, deformed, ugly, bad anatomy, "
    "watermark, text, extra limbs, fused fingers, deformed hands, "
    "poorly drawn face, mutation, mutated, extra limbs, gross proportions"
)


def normalize_shot_payload(raw_scene: dict) -> dict:
    """Normalize legacy / shot.v1 / shot.v2 payloads into one render contract."""
    story = raw_scene.get("story", {}) or {}
    camera = raw_scene.get("camera", {}) or {}
    audio = raw_scene.get("audio", {}) or {}
    refs = raw_scene.get("references", {}) or {}
    style = raw_scene.get("style", {}) or {}
    subject = raw_scene.get("subject", {}) or {}
    scene_block = raw_scene.get("scene", {}) or {}
    continuity = raw_scene.get("continuity", {}) or {}
    output_spec = raw_scene.get("output_spec", {}) or {}
    scene_asset = refs.get("scene_asset") or raw_scene.get("scene_asset", {}) or {}
    characters = refs.get("characters") or raw_scene.get("characters", []) or []
    dialogue = (
        story.get("dialogue_snippets")
        or raw_scene.get("dialogue_snippets", [])
        or raw_scene.get("dialogue", [])
        or []
    )
    location = scene_block.get("location") or story.get("location") or raw_scene.get("location", "")
    mood = story.get("mood") or raw_scene.get("mood", "")
    narration = story.get("narration") or story.get("beat") or raw_scene.get("narration", "")
    shot_type = camera.get("shot_type") or raw_scene.get("shot_type", raw_scene.get("camera_angle", ""))
    camera_angle = camera.get("camera_angle") or shot_type or raw_scene.get("camera_angle", "")
    width = raw_scene.get("width") or output_spec.get("width") or scene_asset.get("width")
    height = raw_scene.get("height") or output_spec.get("height") or scene_asset.get("height")
    frames = raw_scene.get("frames") or output_spec.get("frames")
    fps = raw_scene.get("fps") or output_spec.get("fps")
    negative_prompt = style.get("negative_prompt") or raw_scene.get("negative_prompt", NEGATIVE_PROMPT)
    return {
        "schema_version": raw_scene.get("schema_version", "legacy"),
        "subject": {
            "characters": subject.get("characters") or characters,
            "primary_character": subject.get("primary_character", ""),
            "emotion": subject.get("emotion") or mood,
            "action": subject.get("action", ""),
            "expression": subject.get("expression", ""),
        },
        "scene": {
            "location": location,
            "time_of_day": scene_block.get("time_of_day") or story.get("time_of_day") or raw_scene.get("time_of_day", ""),
            "weather": scene_block.get("weather") or story.get("weather") or raw_scene.get("weather", ""),
            "lighting": scene_block.get("lighting") or scene_asset.get("lighting", ""),
            "atmosphere": scene_block.get("atmosphere") or scene_asset.get("atmosphere", ""),
            "props": scene_block.get("props", []),
        },
        "story": {
            "beat": story.get("beat") or narration,
            "mood": mood,
            "narration": narration,
            "dialogue_snippets": dialogue,
            "intent": story.get("intent", ""),
        },
        "camera": {
            "shot_type": shot_type,
            "camera_angle": camera_angle,
            "framing": camera.get("framing") or shot_type,
            "movement": camera.get("movement") or raw_scene.get("camera_movement", ""),
            "lens_language": camera.get("lens_language", ""),
            "duration_sec": camera.get("duration_sec") or raw_scene.get("duration_sec", 3.0),
        },
        "audio": {
            "bgm_mood": audio.get("bgm_mood") or raw_scene.get("bgm_mood", ""),
            "tts_required": bool(audio.get("tts_required", dialogue)),
            "sfx_cues": audio.get("sfx_cues", []),
        },
        "references": {
            "scene_asset": scene_asset,
            "characters": characters,
        },
        "style": {
            "style_guide": style.get("style_guide") or raw_scene.get("style_guide", ""),
            "visual_style": style.get("visual_style", ""),
            "color_script": style.get("color_script") or scene_asset.get("color_palette", ""),
            "negative_prompt": negative_prompt,
            "quality_target": style.get("quality_target", "production"),
        },
        "continuity": {
            "character_anchor": continuity.get("character_anchor", ""),
            "scene_anchor": continuity.get("scene_anchor", ""),
            "previous_shot_summary": continuity.get("previous_shot_summary", ""),
            "costume_lock": continuity.get("costume_lock", ""),
        },
        "output_spec": {
            "width": width,
            "height": height,
            "frames": frames,
            "fps": fps,
            "quality_tier": output_spec.get("quality_tier", "production"),
        },
        "location": location,
        "time_of_day": scene_block.get("time_of_day") or story.get("time_of_day") or raw_scene.get("time_of_day", ""),
        "weather": scene_block.get("weather") or story.get("weather") or raw_scene.get("weather", ""),
        "mood": mood,
        "narration": narration,
        "dialogue_snippets": dialogue,
        "scene_asset": scene_asset,
        "characters": characters,
        "style_guide": style.get("style_guide") or raw_scene.get("style_guide", ""),
        "bgm_mood": audio.get("bgm_mood") or raw_scene.get("bgm_mood", ""),
        "camera_angle": camera_angle,
        "shot_type": shot_type,
        "width": width,
        "height": height,
        "frames": frames,
        "fps": fps,
        "negative_prompt": negative_prompt,
        "allow_fallback": bool(raw_scene.get("allow_fallback", False)),
    }


def _join_non_empty(parts: list[str]) -> str:
    return ", ".join([p.strip() for p in parts if isinstance(p, str) and p.strip()])


def _character_descriptors(normalized: dict) -> tuple[list[str], list[str]]:
    appearance_parts: list[str] = []
    anchor_parts: list[str] = []
    for char in normalized.get("characters", []):
        if isinstance(char, dict):
            appearance = _join_non_empty([
                char.get("name", ""),
                char.get("appearance", ""),
                char.get("costume", ""),
                char.get("expression", ""),
                char.get("action", ""),
            ])
            if appearance:
                appearance_parts.append(appearance)
            anchor = _join_non_empty([
                char.get("name", ""),
                char.get("prompt_template", ""),
                char.get("voice_profile", ""),
            ])
            if anchor:
                anchor_parts.append(anchor)
        elif char:
            appearance_parts.append(str(char))
    return appearance_parts, anchor_parts


def build_pipeline_prompt_bundle(scene: dict, pipeline_name: str) -> dict:
    """Map normalized shot payload into deterministic prompts/config for a pipeline."""
    normalized = normalize_shot_payload(scene)
    scene_block = normalized.get("scene", {}) or {}
    story = normalized.get("story", {}) or {}
    camera = normalized.get("camera", {}) or {}
    style = normalized.get("style", {}) or {}
    continuity = normalized.get("continuity", {}) or {}
    subject = normalized.get("subject", {}) or {}
    scene_asset = normalized.get("scene_asset", {}) or {}
    appearance_parts, anchor_parts = _character_descriptors(normalized)
    base_visual = [
        scene_block.get("location", ""),
        scene_asset.get("description", ""),
        scene_block.get("lighting", ""),
        scene_block.get("atmosphere", ""),
        f"palette {style.get('color_script', '')}" if style.get("color_script") else "",
        scene_block.get("time_of_day", ""),
        scene_block.get("weather", ""),
        story.get("mood", ""),
        camera.get("shot_type", ""),
        camera.get("camera_angle", ""),
        camera.get("framing", ""),
        camera.get("movement", ""),
        camera.get("lens_language", ""),
        story.get("narration", ""),
        style.get("style_guide", ""),
        style.get("visual_style", ""),
    ]
    continuity_parts = [
        continuity.get("character_anchor", ""),
        continuity.get("scene_anchor", ""),
        continuity.get("previous_shot_summary", ""),
        continuity.get("costume_lock", ""),
    ]
    dialogue_lines: list[str] = []
    for line in (story.get("dialogue_snippets") or [])[:3]:
        if isinstance(line, dict):
            dialogue_lines.append(_join_non_empty([
                line.get("character", ""),
                line.get("line", ""),
                line.get("emotion", ""),
            ]))
        elif line:
            dialogue_lines.append(str(line))

    if pipeline_name.startswith("wan2") or pipeline_name == "flux_wan2_twostage":
        positive_parts = [
            _join_non_empty(base_visual),
            _join_non_empty(appearance_parts),
            _join_non_empty(continuity_parts),
            _join_non_empty(dialogue_lines),
            _join_non_empty([subject.get("action", ""), subject.get("expression", ""), subject.get("emotion", "")]),
            "cinematic anime video shot, coherent motion, consistent face, production-ready short drama frame",
        ]
        stage1_parts = [
            _join_non_empty(base_visual),
            _join_non_empty(appearance_parts + anchor_parts),
            _join_non_empty([subject.get("action", ""), subject.get("expression", "")]),
            "hero storyboard keyframe, crisp linework, strong composition, stable identity",
        ]
        return {
            "positive_prompt": _join_non_empty(positive_parts),
            "negative_prompt": style.get("negative_prompt", NEGATIVE_PROMPT),
            "stage1_prompt": _join_non_empty(stage1_parts),
            "reference_required": True,
            "width": normalized.get("width") or 832,
            "height": normalized.get("height") or 480,
            "frames": normalized.get("frames") or 49,
            "fps": normalized.get("fps") or 16,
        }

    if pipeline_name.startswith("animatediff"):
        positive_parts = [
            _join_non_empty(base_visual),
            _join_non_empty(appearance_parts),
            _join_non_empty([subject.get("action", ""), subject.get("expression", ""), subject.get("emotion", "")]),
            _join_non_empty(dialogue_lines),
            _join_non_empty(continuity_parts),
            "anime storyboard frame sequence, dynamic motion, consistent character design, detailed background, high quality",
        ]
        return {
            "positive_prompt": _join_non_empty(positive_parts),
            "negative_prompt": style.get("negative_prompt", NEGATIVE_PROMPT),
            "width": normalized.get("width") or 1024,
            "height": normalized.get("height") or 1024,
            "frames": normalized.get("frames") or 16,
            "fps": normalized.get("fps") or 8,
        }

    positive_parts = [
        _join_non_empty(base_visual),
        _join_non_empty(appearance_parts),
        _join_non_empty([subject.get("action", ""), subject.get("expression", "")]),
        "anime storyboard frame, cinematic composition, detailed background, high quality",
    ]
    return {
        "positive_prompt": _join_non_empty(positive_parts),
        "negative_prompt": style.get("negative_prompt", NEGATIVE_PROMPT),
        "width": normalized.get("width") or 512,
        "height": normalized.get("height") or 768,
    }


# ══════════════════════════════════════════════════════
# ComfyUI 工具函数（所有管线共用）
# ══════════════════════════════════════════════════════

_object_info_cache: Optional[dict] = None


def get_object_info(comfyui_url: str = "http://127.0.0.1:8188",
                    force_refresh: bool = False) -> dict:
    """从 ComfyUI 获取所有可用节点定义（带内存缓存）。"""
    global _object_info_cache
    import requests as req
    if _object_info_cache is None or force_refresh:
        try:
            r = req.get(f"{comfyui_url}/object_info", timeout=10)
            if r.status_code == 200:
                _object_info_cache = r.json()
        except Exception:
            _object_info_cache = {}
    return _object_info_cache or {}


def find_nodes_by_type(workflow: dict, class_type: str) -> list[str]:
    return [nid for nid, node in workflow.items()
            if node.get("class_type") == class_type]


def find_ksampler_positive_node(workflow: dict) -> Optional[str]:
    for kid in find_nodes_by_type(workflow, "KSampler"):
        ref = workflow[kid].get("inputs", {}).get("positive")
        if isinstance(ref, list) and ref:
            return str(ref[0])
    return None


def find_ksampler_negative_node(workflow: dict) -> Optional[str]:
    for kid in find_nodes_by_type(workflow, "KSampler"):
        ref = workflow[kid].get("inputs", {}).get("negative")
        if isinstance(ref, list) and ref:
            return str(ref[0])
    return None


def inject_prompt(workflow: dict, positive: str, negative: str = "") -> dict:
    """动态定位 positive/negative 节点并注入 prompt（不依赖硬编码节点 ID）。"""
    pos_id = find_ksampler_positive_node(workflow)
    neg_id = find_ksampler_negative_node(workflow)
    if pos_id and pos_id in workflow:
        workflow[pos_id]["inputs"]["text"] = positive
    elif "3" in workflow:
        workflow["3"]["inputs"]["text"] = positive
    if negative:
        if neg_id and neg_id in workflow:
            workflow[neg_id]["inputs"]["text"] = negative
        elif "4" in workflow:
            workflow["4"]["inputs"]["text"] = negative
    return workflow


def inject_seed(workflow: dict, seed: int) -> dict:
    for kid in find_nodes_by_type(workflow, "KSampler"):
        workflow[kid]["inputs"]["seed"] = seed
    return workflow


def inject_loras(workflow: dict, lora_refs: list[dict]) -> dict:
    if not lora_refs:
        return workflow
    ckpt_ids = find_nodes_by_type(workflow, "CheckpointLoaderSimple")
    if not ckpt_ids:
        return workflow
    ckpt_id = ckpt_ids[0]
    cur_model = [ckpt_id, 0]
    cur_clip = [ckpt_id, 1]
    next_id = max((int(k) for k in workflow if k.isdigit()), default=0) + 1
    last_model = last_clip = None
    for i, lora in enumerate(lora_refs):
        lora_name = lora.get("name", "")
        if not lora_name:
            continue
        strength = float(lora.get("strength", 0.8))
        nid = str(next_id + i)
        workflow[nid] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": lora_name,
                "strength_model": strength,
                "strength_clip": strength,
                "model": cur_model,
                "clip": cur_clip,
            },
        }
        cur_model = [nid, 0]
        cur_clip = [nid, 1]
        last_model = cur_model
        last_clip = cur_clip
    if last_model:
        for kid in find_nodes_by_type(workflow, "KSampler"):
            if isinstance(workflow[kid]["inputs"].get("model"), list):
                workflow[kid]["inputs"]["model"] = last_model
        for kid in find_nodes_by_type(workflow, "ADE_UseEvolvedSampling"):
            if isinstance(workflow[kid]["inputs"].get("model"), list):
                workflow[kid]["inputs"]["model"] = last_model
        for kid in find_nodes_by_type(workflow, "CLIPTextEncode"):
            if isinstance(workflow[kid]["inputs"].get("clip"), list):
                workflow[kid]["inputs"]["clip"] = last_clip
    return workflow


_CONTROLNET_MAP = {
    "canny": "controlnet_canny.safetensors",
    "depth": "controlnet_depth.safetensors",
    "openpose": "controlnet_openpose.safetensors",
    "lineart": "controlnet_lineart.safetensors",
    "tile": "control_v11f1e_sd15_tile.pth",
}


def inject_controlnet(
    workflow: dict,
    control_type: str,
    image_ref: Optional[list] = None,
    strength: float = 0.6,
    comfyui_url: str = "http://127.0.0.1:8188",
) -> dict:
    if not image_ref:
        return workflow
    cn_name = _CONTROLNET_MAP.get(control_type, f"controlnet_{control_type}.safetensors")
    if "ControlNetLoader" not in get_object_info(comfyui_url):
        return workflow
    next_id = max((int(k) for k in workflow if k.isdigit()), default=0) + 1
    loader_id = str(next_id)
    apply_id = str(next_id + 1)
    workflow[loader_id] = {
        "class_type": "ControlNetLoader",
        "inputs": {"control_net_name": cn_name},
    }
    pos_id = find_ksampler_positive_node(workflow)
    pos_ref = [pos_id, 0] if pos_id else ["3", 0]
    workflow[apply_id] = {
        "class_type": "ControlNetApply",
        "inputs": {
            "conditioning": pos_ref,
            "control_net": [loader_id, 0],
            "image": image_ref,
            "strength": strength,
        },
    }
    for kid in find_nodes_by_type(workflow, "KSampler"):
        workflow[kid]["inputs"]["positive"] = [apply_id, 0]
    return workflow


def submit_workflow(workflow: dict,
                    comfyui_url: str = "http://127.0.0.1:8188") -> Optional[str]:
    import requests as req
    r = req.post(f"{comfyui_url}/prompt", json={"prompt": workflow}, timeout=30)
    r.raise_for_status()
    return r.json().get("prompt_id")


def wait_for_completion(
    prompt_id: str,
    comfyui_url: str = "http://127.0.0.1:8188",
    timeout: int = 7200,
) -> Optional[dict]:
    result = wait_for_completion_result(prompt_id, comfyui_url, timeout)
    if result["status"] == "completed":
        return result["outputs"]
    return None


def wait_for_completion_result(
    prompt_id: str,
    comfyui_url: str = "http://127.0.0.1:8188",
    timeout: int = 7200,
) -> dict:
    import requests as req
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = req.get(f"{comfyui_url}/history/{prompt_id}", timeout=10)
            if r.status_code == 200 and r.text not in ("", "{}"):
                hist = r.json()
                if prompt_id in hist:
                    payload = hist[prompt_id]
                    status = payload.get("status", {})
                    if status.get("completed"):
                        return {
                            "status": "completed",
                            "outputs": payload.get("outputs", {}),
                            "error_type": "",
                            "error_message": "",
                        }
                    if status.get("status_str") == "error" or status.get("error"):
                        messages = status.get("messages") or []
                        error_message = "ComfyUI execution failed"
                        error_type = "workflow_error"
                        for item in reversed(messages):
                            if item and item[0] == "execution_error":
                                details = item[1]
                                error_type = str(details.get("exception_type") or "workflow_error")
                                node_type = details.get("node_type") or details.get("node_id") or "unknown"
                                error_message = (
                                    f"{node_type}: {details.get('exception_message') or 'execution_error'}"
                                )
                                break
                        return {
                            "status": "error",
                            "outputs": {},
                            "error_type": error_type,
                            "error_message": error_message,
                        }
        except Exception as exc:
            return {
                "status": "error",
                "outputs": {},
                "error_type": "transport_error",
                "error_message": str(exc),
            }
        time.sleep(3)
    return {
        "status": "timeout",
        "outputs": {},
        "error_type": "timeout",
        "error_message": f"ComfyUI execution timed out after {timeout}s",
    }


def get_video_output(comfyui_outputs: dict) -> list[dict]:
    files = []
    for node_id, node_out in comfyui_outputs.items():
        for key in ("gifs", "videos", "images"):
            for item in node_out.get(key, []):
                if isinstance(item, dict) and item.get("filename"):
                    files.append({
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                        "node": node_id,
                    })
        for key in ("files",):
            for item in node_out.get(key, []):
                if isinstance(item, dict) and item.get("filename"):
                    files.append({
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                        "node": node_id,
                    })
    return files


def _resolve_reference_image_target(shot_payload: dict) -> Path:
    project_name = shot_payload.get("project_name", "project")
    shot_id = int(shot_payload.get("shot_id", 0) or 0)
    scene_id = shot_payload.get("scene_id", "") or shot_payload.get("location", "scene")
    safe_scene = str(scene_id).replace(" ", "_")[:40]
    ref_dir = _PROJECT_ROOT / "output" / "projects" / project_name / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)
    if shot_id:
        return ref_dir / f"shot_{shot_id:04d}_keyframe.png"
    return ref_dir / f"{safe_scene}_keyframe.png"


def _first_image_from_outputs(comfyui_outputs: dict) -> Optional[Path]:
    for node_out in comfyui_outputs.values():
        for item in node_out.get("images", []):
            if isinstance(item, dict) and item.get("filename"):
                subfolder = item.get("subfolder", "")
                p = (_COMFYUI_OUTPUT_DIR / subfolder / item["filename"]
                     if subfolder else _COMFYUI_OUTPUT_DIR / item["filename"])
                if p.exists():
                    return p
    return None


def generate_reference_keyframe_image(
    shot_payload: dict,
    comfyui_url: str = "http://127.0.0.1:8188",
    config: Optional[dict] = None,
) -> Path:
    """Generate a deterministic storyboard keyframe placeholder image for TI2V pipelines.

    Uses PIL to create a 480×832 dark blue-gray placeholder with the scene description
    text rendered in white. No ComfyUI dependency required.
    """
    from PIL import Image, ImageDraw

    config = config or {}
    output_path = _resolve_reference_image_target(shot_payload)

    # Cache hit: return immediately if a valid image already exists
    if output_path.exists() and output_path.stat().st_size > 1024:
        return output_path

    # Build label text from scene description or prompt bundle
    scene_text = shot_payload.get("scene_description", "")
    if not scene_text:
        try:
            bundle = build_pipeline_prompt_bundle(shot_payload, "wan2_ti2v")
            scene_text = (bundle.get("stage1_prompt") or bundle["positive_prompt"])[:80]
        except Exception:
            scene_text = ""

    # Create 480×832 (W×H) cinematic dark blue-gray placeholder
    img = Image.new("RGB", (480, 832), color=(80, 80, 90))
    draw = ImageDraw.Draw(img)

    # Wrap and center text
    if scene_text:
        max_chars_per_line = 36
        words = scene_text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            if len(current) + len(word) + 1 <= max_chars_per_line:
                current = (current + " " + word).strip()
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)

        line_height = 24
        total_height = len(lines) * line_height
        y_start = (832 - total_height) // 2
        for i, line in enumerate(lines):
            # Default PIL font: each character is ~6px wide at size 10
            text_width = len(line) * 6
            x = (480 - text_width) // 2
            draw.text((x, y_start + i * line_height), line, fill=(255, 255, 255))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), format="PNG")
    print(f"[Keyframe] PIL placeholder generated: {output_path}")
    return output_path


def build_scene_prompt(scene: dict) -> str:
    """Backward-compatible generic prompt builder."""
    return build_pipeline_prompt_bundle(scene, "generic")["positive_prompt"]


def inject_prompts(workflow_json: dict, shot, pipeline_name: str = "generic") -> dict:
    """
    动态注入 shot 的 dialogue + visual_prompt + 分辨率到 workflow JSON。
    shot 可以是 core.models.Shot 实例或 dict（包含 dialogue/visual_prompt）。
    返回修改后的 workflow。
    """
    import copy

    wf = copy.deepcopy(workflow_json)

    normalized = None
    if hasattr(shot, "render_payload"):
        rp = shot.render_payload
        if isinstance(rp, str):
            try:
                rp = json.loads(rp)
            except Exception:
                rp = {}
        if isinstance(rp, dict):
            normalized = normalize_shot_payload(rp)
    elif isinstance(shot, dict):
        normalized = normalize_shot_payload(shot)
    if normalized is None:
        normalized = normalize_shot_payload({})
    bundle = build_pipeline_prompt_bundle(normalized, pipeline_name)
    prompt_text = bundle["positive_prompt"]
    negative_prompt = bundle.get("negative_prompt", NEGATIVE_PROMPT)

    # ── 3. 替换占位符文本字段 ─────────────────────
    for node in wf.values():
        inputs = node.get("inputs", {})
        if "clip_l" in inputs and isinstance(inputs["clip_l"], str):
            inputs["clip_l"] = prompt_text
        if "t5xxl" in inputs and isinstance(inputs["t5xxl"], str):
            inputs["t5xxl"] = bundle.get("stage1_prompt", prompt_text)
        if "text" in inputs and isinstance(inputs["text"], str):
            current = inputs["text"].lower()
            if any(token in current for token in ("negative", "low quality", "blurry", "watermark")):
                inputs["text"] = negative_prompt
            else:
                inputs["text"] = prompt_text

    # ── 4. 替换 width/height ──────────────────────
    width = int(bundle.get("width") or 832)
    height = int(bundle.get("height") or 480)

    for node in wf.values():
        inputs = node.get("inputs", {})
        if "width" in inputs and isinstance(inputs["width"], (int, float)):
            inputs["width"] = width
        if "height" in inputs and isinstance(inputs["height"], (int, float)):
            inputs["height"] = height
        if "length" in inputs and isinstance(inputs["length"], (int, float)) and bundle.get("frames"):
            inputs["length"] = int(bundle["frames"])
        if "fps" in inputs and isinstance(inputs["fps"], (int, float)) and bundle.get("fps"):
            inputs["fps"] = int(bundle["fps"])

    return wf


# ══════════════════════════════════════════════════════
# InstantID 工作流注入（角色一致性）
# ══════════════════════════════════════════════════════

def inject_instantid(
    workflow: dict,
    face_image_name: str,
    instantid_model: str = "ip-adapter.bin",
    controlnet_name: str = "InstantID-ControlNet.safetensors",
    ip_weight: float = 0.8,
    cn_strength: float = 0.8,
    comfyui_url: str = "http://127.0.0.1:8188",
) -> dict:
    """
    向已有 workflow 注入 InstantID 节点，实现参考人脸驱动的角色一致性。

    依赖 comfyui_instantid 自定义节点（InstantIDModelLoader / InstantIDFaceAnalysis /
    ApplyInstantID）以及 controlnet/InstantID-ControlNet.safetensors、
    instantid/ip-adapter.bin、insightface/antelopev2。

    自动适配以下管线：
      - SDXL/CheckpointLoaderSimple → Checkpoint[0] 为 model ref
      - Flux/UNETLoader             → UNETLoader[0] 为 model ref
      - Wan/UnetLoaderGGUF          → UnetLoaderGGUF[0] 为 model ref

    若节点不可用则静默跳过，workflow 保持不变（降级为无 InstantID 渲染）。
    """
    object_info = get_object_info(comfyui_url)
    if "InstantIDModelLoader" not in object_info:
        print("[InstantID] comfyui_instantid 节点未安装，跳过")
        return workflow

    next_id = max((int(k) for k in workflow if k.isdigit()), default=0) + 1
    face_analysis_id = str(next_id)
    instantid_loader_id = str(next_id + 1)
    cn_loader_id = str(next_id + 2)
    load_image_id = str(next_id + 3)
    apply_id = str(next_id + 4)

    # InsightFace 人脸分析（使用 CPU，兼容 Apple Silicon）
    workflow[face_analysis_id] = {
        "class_type": "InstantIDFaceAnalysis",
        "inputs": {"provider": "CPU"},
    }

    # InstantID ip-adapter 权重加载
    workflow[instantid_loader_id] = {
        "class_type": "InstantIDModelLoader",
        "inputs": {"instantid_file": instantid_model},
    }

    # InstantID ControlNet 加载
    workflow[cn_loader_id] = {
        "class_type": "ControlNetLoader",
        "inputs": {"control_net_name": controlnet_name},
    }

    # 参考人脸图（已上传到 ComfyUI input）
    workflow[load_image_id] = {
        "class_type": "LoadImage",
        "inputs": {"image": face_image_name, "upload": "image"},
    }

    # 找 model ref：支持多种管线类型
    model_ref = None
    for node_type in ("CheckpointLoaderSimple", "UNETLoader", "UnetLoaderGGUF"):
        ids = find_nodes_by_type(workflow, node_type)
        if ids:
            model_ref = [ids[0], 0]
            break
    if model_ref is None:
        model_ref = ["1", 0]  # fallback

    # 找 positive / negative
    pos_id = find_ksampler_positive_node(workflow)
    neg_id = find_ksampler_negative_node(workflow)
    pos_ref = [pos_id, 0] if pos_id else ["3", 0]
    neg_ref = [neg_id, 0] if neg_id else ["4", 0]

    # ApplyInstantID → 输出 [0]=model, [1]=positive, [2]=negative
    # 新版 ApplyInstantID API：weight 作为独立FLOAT输入（非 ip_weight/cn_strength）
    instantid_weight = min(ip_weight, cn_strength) * 0.8 + 0.2  # 综合权重
    workflow[apply_id] = {
        "class_type": "ApplyInstantID",
        "inputs": {
            "instantid": [instantid_loader_id, 0],
            "insightface": [face_analysis_id, 0],
            "control_net": [cn_loader_id, 0],
            "image": [load_image_id, 0],
            "model": model_ref,
            "positive": pos_ref,
            "negative": neg_ref,
            "weight": round(instantid_weight, 2),
            "start_at": 0.0,
            "end_at": 1.0,
        },
    }

    # 重定向 KSampler 输入
    for kid in find_nodes_by_type(workflow, "KSampler"):
        workflow[kid]["inputs"]["model"] = [apply_id, 0]
        workflow[kid]["inputs"]["positive"] = [apply_id, 1]
        workflow[kid]["inputs"]["negative"] = [apply_id, 2]

    # 重定向 ADE_UseEvolvedSampling（AnimateDiff）
    for kid in find_nodes_by_type(workflow, "ADE_UseEvolvedSampling"):
        workflow[kid]["inputs"]["model"] = [apply_id, 0]

    return workflow


# ══════════════════════════════════════════════════════
# 异常
# ══════════════════════════════════════════════════════

class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    path: Path
    pipeline_name: str
    requested_pipeline: str = ""
    fallback_used: bool = False
    fallback_from: str = ""
    render_tier: str = "production"


# ══════════════════════════════════════════════════════
# RenderPipeline ABC
# ══════════════════════════════════════════════════════

class RenderPipeline(ABC):
    """
    所有渲染管线的统一契约。

    子类声明 required_nodes（ComfyUI class_type 列表）和 required_models
    （模型文件名，仅供 UI 展示；运行时通过节点输入验证）。
    Dispatcher 在首次渲染前调用 validate() 探测可用性。
    """

    name: str = "base"
    required_nodes: list[str] = []
    required_models: list[str] = []

    def __init__(self, config: dict, comfyui_url: str = "http://127.0.0.1:8188"):
        self.config = config
        self.comfyui_url = comfyui_url

    def validate(self, object_info: dict) -> list[str]:
        """返回缺失项列表（"node:XXX" 格式），空列表 = 可用。"""
        return [f"node:{n}" for n in self.required_nodes if n not in object_info]

    @abstractmethod
    def render(self, shot_payload: dict, output_path: Path) -> RenderResult:
        """渲染 shot 到 output_path。成功返回路径，失败抛 RenderError。"""
        ...

    def _upload_image(self, image_path: str) -> str:
        """上传本地图片到 ComfyUI input，返回可供 LoadImage 节点使用的文件名。"""
        import requests as req
        with open(image_path, "rb") as f:
            r = req.post(
                f"{self.comfyui_url}/upload/image",
                files={"image": (Path(image_path).name, f, "image/png")},
                data={"overwrite": "true"},
                timeout=30,
            )
        r.raise_for_status()
        return r.json().get("name", Path(image_path).name)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"



# ══════════════════════════════════════════════════════
# StubPipeline
# ══════════════════════════════════════════════════════

class StubPipeline(RenderPipeline):
    """纯 ffmpeg 黑帧占位，无 ComfyUI 依赖，最终兜底。"""

    name = "stub"
    required_nodes = []

    def validate(self, object_info: dict) -> list[str]:
        if not shutil.which("ffmpeg"):
            return ["ffmpeg (not installed)"]
        return []

    def render(self, shot_payload: dict, output_path: Path) -> Path:
        cfg = self.config
        width = cfg.get("width", 512)
        height = cfg.get("height", 768)
        duration = cfg.get("duration_sec", 3.0)
        label = (shot_payload.get("location") or shot_payload.get("name") or "shot")[:40]
        label = label.replace("'", "").replace("\\", "")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["ffmpeg", "-y",
             "-f", "lavfi",
             "-i", f"color=c=0x1a1a2e:s={width}x{height}:r=8:d={duration}",
             "-vf", (
                 f"drawtext=text='{label}':fontcolor=white:fontsize=24:"
                 f"x=(w-text_w)/2:y=(h-text_h)/2"
             ),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output_path)],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RenderError(f"StubPipeline ffmpeg 失败: {r.stderr.decode()[:300]}")
        return output_path


# ══════════════════════════════════════════════════════
# Wan2TI2VPipeline
# ══════════════════════════════════════════════════════

class Wan2TI2VPipeline(RenderPipeline):
    """
    Wan2.2 TI2V 5B GGUF — 文本 + 参考图 → 视频。

    依赖：
      - ComfyUI-GGUF 自定义节点（UnetLoaderGGUF）
      - ComfyUI-VideoHelperSuite（VHS_VideoCombine）
      - Wan2.2-TI2V-5B-Q4_K_M.gguf（建议完整文件 >= 1.2 GB）
      - UMT5-XXL text encoder
      - wan2_ti2v_workflow.json 工作流文件（手动配置）

    validate() 会检测模型文件大小并给出明确提示。
    """

    name = "wan2_ti2v"
    required_nodes = [
        "UnetLoaderGGUF",
        "CLIPLoader",
        "ModelSamplingSD3",
        "Wan22ImageToVideoLatent",
        "CreateVideo",
        "SaveVideo",
    ]

    def validate(self, object_info: dict) -> list[str]:
        missing = super().validate(object_info)

        # 检查工作流文件
        wf_file = _PIPELINES_DIR / self.config.get("workflow_file", "wan2_ti2v_workflow.json")
        if not wf_file.exists():
            missing.append(f"workflow_file:{wf_file.name} (未找到，请参考 wan2_ti2v_workflow.json.example 创建)")

        # 检查 GGUF 模型文件大小（避免接受不完整下载）
        gguf_raw = self.config.get("gguf_path", "")
        if gguf_raw:
            gguf_path = Path(os.path.expanduser(gguf_raw))
            min_mb = self.config.get("min_gguf_size_mb", 3000)
            if not gguf_path.exists():
                missing.append(f"gguf:{gguf_path.name} (文件不存在)")
            else:
                actual_mb = gguf_path.stat().st_size / (1024 * 1024)
                if actual_mb < min_mb:
                    missing.append(
                        f"gguf:{gguf_path.name} 下载不完整 "
                        f"({actual_mb:.0f}MB < {min_mb}MB，请重新下载完整模型)"
                    )

        # 检查 text encoder
        enc_raw = self.config.get("text_encoder", "")
        if enc_raw:
            enc_path = Path(os.path.expanduser(enc_raw))
            min_enc_mb = self.config.get("min_encoder_size_mb", 1000)
            if not enc_path.exists():
                missing.append(f"encoder:{enc_path.name} (文件不存在)")
            else:
                actual_mb = enc_path.stat().st_size / (1024 * 1024)
                if actual_mb < min_enc_mb:
                    missing.append(
                        f"encoder:{enc_path.name} 下载不完整 "
                        f"({actual_mb:.0f}MB < {min_enc_mb}MB)"
                    )

        return missing

    def _upload_image(self, image_path: str) -> str:
        """上传参考图到 ComfyUI input 目录，返回文件名（供 LoadImage 节点使用）。"""
        import requests as req
        with open(image_path, "rb") as f:
            r = req.post(
                f"{self.comfyui_url}/upload/image",
                files={"image": (Path(image_path).name, f, "image/png")},
                data={"overwrite": "true"},
                timeout=30,
            )
        r.raise_for_status()
        return r.json().get("name", Path(image_path).name)

    def render(self, shot_payload: dict, output_path: Path) -> Path:
        wf_file = _PIPELINES_DIR / self.config.get("workflow_file", "wan2_ti2v_workflow.json")
        wf = json.loads(wf_file.read_text())

        bundle = build_pipeline_prompt_bundle(shot_payload, self.name)
        prompt = bundle["positive_prompt"]
        negative_prompt = bundle.get("negative_prompt", NEGATIVE_PROMPT)
        seed = int(time.time() * 1000) % (2 ** 31)

        # 官方 Wan2.2 TI2V 拓扑：CLIPTextEncode → KSampler，latent 由 Wan22ImageToVideoLatent 提供
        clip_ids = find_nodes_by_type(wf, "CLIPTextEncode")
        if clip_ids:
            wf[clip_ids[0]]["inputs"]["text"] = prompt   # 第一个 = positive
            if len(clip_ids) > 1:
                wf[clip_ids[1]]["inputs"]["text"] = negative_prompt
        else:
            wf = inject_prompt(wf, prompt, negative_prompt)           # fallback
        wf = inject_seed(wf, seed)

        # 注入参考图（TI2V 必须）
        ref_image = shot_payload.get("reference_image_path")
        reference_strategy = (
            (shot_payload.get("references", {}) or {}).get("reference_strategy")
            or shot_payload.get("reference_strategy")
            or "auto_keyframe"
        )
        if not ref_image or not Path(str(ref_image)).exists():
            if reference_strategy == "auto_keyframe":
                ref_image = str(generate_reference_keyframe_image(
                    shot_payload,
                    comfyui_url=self.comfyui_url,
                    config=self.config,
                ))
            else:
                raise RenderError(
                    "Wan2TI2VPipeline 需要 shot_payload['reference_image_path']，"
                    "当前 shot 无参考图且 reference_strategy 不是 auto_keyframe"
                )
        uploaded_name = self._upload_image(ref_image)
        # 找 LoadImage 节点并注入文件名
        for node in wf.values():
            if node.get("class_type") == "LoadImage":
                node["inputs"]["image"] = uploaded_name
                break

        # 注入 UNET / CLIP / VAE / latent / sampler 参数
        gguf_name = Path(os.path.expanduser(self.config.get("gguf_path", ""))).name
        encoder_name = Path(os.path.expanduser(self.config.get("text_encoder", ""))).name
        vae_name = self.config.get("vae", "Wan2.2_VAE.safetensors")
        for node in wf.values():
            ct = node.get("class_type")
            if ct in {"UNETLoader", "UnetLoaderGGUF"}:
                node["inputs"]["unet_name"] = gguf_name
            elif ct == "CLIPLoader" and encoder_name:
                node["inputs"]["clip_name"] = encoder_name
                node["inputs"]["type"] = "wan"
            elif ct == "VAELoader":
                node["inputs"]["vae_name"] = vae_name
            elif ct == "Wan22ImageToVideoLatent":
                node["inputs"]["width"] = int(bundle.get("width") or self.config.get("width", 832))
                node["inputs"]["height"] = int(bundle.get("height") or self.config.get("height", 480))
                node["inputs"]["length"] = int(bundle.get("frames") or self.config.get("frames", 49))
                node["inputs"]["batch_size"] = 1
            elif ct == "ModelSamplingSD3":
                node["inputs"]["shift"] = float(self.config.get("shift", 8.0))
            elif ct == "KSampler":
                node["inputs"]["steps"] = int(self.config.get("steps", 20))
                node["inputs"]["cfg"] = float(self.config.get("cfg", 5.0))
                node["inputs"]["sampler_name"] = self.config.get("sampler_name", "uni_pc")
                node["inputs"]["scheduler"] = self.config.get("scheduler", "simple")
                node["inputs"]["denoise"] = float(self.config.get("denoise", 1.0))
            elif ct == "CreateVideo":
                node["inputs"]["fps"] = float(bundle.get("fps") or self.config.get("fps", 16))
            elif ct == "SaveVideo":
                prefix = shot_payload.get("output_prefix") or "video/wan2_scene"
                node["inputs"]["filename_prefix"] = prefix
                node["inputs"]["format"] = "mp4"
                node["inputs"]["codec"] = "h264"

        # ── InstantID：有参考人脸图时自动注入 ──
        ref_face = shot_payload.get("face_image")
        if ref_face and Path(str(ref_face)).exists():
            try:
                uploaded = self._upload_image(str(ref_face))
                wf = inject_instantid(
                    wf, uploaded,
                    ip_weight=self.config.get("instantid_ip_weight", 0.8),
                    cn_strength=self.config.get("instantid_cn_strength", 0.8),
                    comfyui_url=self.comfyui_url,
                )
            except Exception as e:
                print(f"[InstantID] Wan2 注入失败（{e}），继续无 InstantID 渲染")

        prompt_id = submit_workflow(wf, self.comfyui_url)
        if not prompt_id:
            raise RenderError("ComfyUI 拒绝提交 Wan2.2 工作流")

        timeout = self.config.get("timeout", 7200)
        result = wait_for_completion_result(prompt_id, self.comfyui_url, timeout=timeout)
        if result["status"] != "completed":
            raise RenderError(
                f"Wan2.2 渲染失败 ({result['error_type']}, prompt_id={prompt_id[:8]}): "
                f"{result['error_message']}"
            )
        outputs = result["outputs"]

        files = get_video_output(outputs)
        if not files:
            raise RenderError("Wan2.2 ComfyUI 输出中无视频文件")

        vf = files[0]
        subfolder = vf.get("subfolder", "")
        src = (_COMFYUI_OUTPUT_DIR / subfolder / vf["filename"]
               if subfolder else _COMFYUI_OUTPUT_DIR / vf["filename"])
        if not src.exists():
            raise RenderError(f"Wan2.2 输出视频不存在: {src}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, output_path)
        return output_path


class Wan2T2VPipeline(RenderPipeline):
    """
    Wan2.2 T2V-A14B GGUF — 纯文本→视频（无需参考图）。
    需要模型: wan2.2_t2v_high_noise_14B_Q4_K_M.gguf
    """
    NAME = "wan2_t2v"

    def render(self, shot_payload: dict, output_path: Path) -> Path:
        raise NotImplementedError(
            "Wan2T2VPipeline: 模型 wan2.2_t2v_high_noise_14B_Q4_K_M.gguf 尚未下载。"
            "请先运行: hf download bullerwins/Wan2.2-T2V-A14B-GGUF "
            "wan2.2_t2v_high_noise_14B_Q4_K_M.gguf "
            "--local-dir ~/myworkspace/ComfyUI_models/unet/"
        )


class Wan2VACEPipeline(RenderPipeline):
    """
    Wan2.2 VACE-Fun-A14B GGUF — 视频→视频 / 局部重绘。
    需要模型: Wan2.2-VACE-Fun-A14B-high-noise-Q4_K_M.gguf
    """
    NAME = "wan2_vace"

    def render(self, shot_payload: dict, output_path: Path) -> Path:
        raise NotImplementedError(
            "Wan2VACEPipeline: 模型 Wan2.2-VACE-Fun-A14B-high-noise-Q4_K_M.gguf 尚未下载。"
            "请先运行: hf download QuantStack/Wan2.2-VACE-Fun-A14B-GGUF "
            "'HighNoise/Wan2.2-VACE-Fun-A14B-high-noise-Q4_K_M.gguf' "
            "--local-dir ~/myworkspace/ComfyUI_models/unet/"
        )


_PIPELINE_CLASSES: dict[str, type[RenderPipeline]] = {
    # Wan2.2 核心栈
    "Wan2TI2VPipeline":       Wan2TI2VPipeline,
    "Wan2T2VPipeline":        Wan2T2VPipeline,
    "Wan2VACEPipeline":       Wan2VACEPipeline,
    # 兜底
    "StubPipeline":           StubPipeline,
}


@dataclass
class PipelineStatus:
    available: bool
    missing: list[str] = field(default_factory=list)
    last_error: str = ""


class RenderDispatcher:
    """
    按优先级尝试可用管线，失败自动回退。

    使用示例：
        dispatcher = RenderDispatcher.from_config()
        matrix = dispatcher.probe()      # {name: PipelineStatus}
        path = dispatcher.render(shot_payload, output_path)
    """

    def __init__(self, pipelines: list[tuple[int, RenderPipeline]],
                 comfyui_url: str = "http://127.0.0.1:8188",
                 active_pipeline: str = ""):
        self._pipelines = sorted(pipelines, key=lambda x: x[0])
        self.comfyui_url = comfyui_url
        self._status: dict[str, PipelineStatus] = {}
        self._probed = False
        self.active_pipeline = active_pipeline

    @classmethod
    def from_config(cls, config_path: Optional[Path] = None) -> "RenderDispatcher":
        if config_path is None:
            config_path = _PIPELINES_DIR / "pipeline_config.json"
        cfg = json.loads(config_path.read_text())
        url = cfg.get("comfyui_url", "http://127.0.0.1:8188")
        pipelines: list[tuple[int, RenderPipeline]] = []
        for entry in cfg.get("pipelines", []):
            cls_name = entry.get("class", "")
            pipeline_cls = _PIPELINE_CLASSES.get(cls_name)
            if pipeline_cls is None:
                continue
            p = pipeline_cls(config=entry.get("config", {}), comfyui_url=url)
            p.name = entry["name"]
            pipelines.append((entry.get("priority", 99), p))
        return cls(pipelines, comfyui_url=url, active_pipeline=cfg.get("active_pipeline", ""))

    def probe(self, force: bool = False) -> dict[str, PipelineStatus]:
        """
        调用 ComfyUI /object_info，验证每条管线的节点依赖。
        结果缓存；force=True 强制刷新。
        """
        if self._probed and not force:
            return self._status
        try:
            object_info = get_object_info(self.comfyui_url, force_refresh=force)
        except Exception:
            object_info = {}
        self._status = {}
        for _, pipeline in self._pipelines:
            missing = pipeline.validate(object_info)
            self._status[pipeline.name] = PipelineStatus(
                available=len(missing) == 0,
                missing=missing,
            )
        self._probed = True
        return self._status

    @property
    def available_pipelines(self) -> list[RenderPipeline]:
        """按优先级返回当前可用管线（自动触发 probe）。"""
        if not self._probed:
            self.probe()
        ordered = [
            p for _, p in self._pipelines
            if self._status.get(p.name, PipelineStatus(False)).available
        ]
        if self.active_pipeline:
            ordered.sort(key=lambda p: 0 if p.name == self.active_pipeline else 1)
        return ordered

    def set_active_pipeline(self, pipeline_name: str) -> None:
        self.active_pipeline = pipeline_name

    def render(self, shot_payload: dict, output_path: Path) -> Path:
        """
        按优先级尝试可用管线，全部失败时抛 RenderError。
        首次调用自动触发 probe()。
        """
        candidates = self.available_pipelines
        if not candidates:
            raise RenderError(
                "没有可用的渲染管线（ComfyUI 离线且 ffmpeg 不可用？）\n"
                f"探测结果: {self.capability_matrix()}"
            )
        normalized = normalize_shot_payload(shot_payload)
        allow_fallback = normalized.get("allow_fallback") or os.getenv("STORY_AGENT_ALLOW_RENDER_FALLBACK", "").lower() in {"1", "true", "yes"}
        preferred: Optional[RenderPipeline] = None
        if self.active_pipeline:
            preferred = next((p for p in candidates if p.name == self.active_pipeline), None)
        if preferred is None:
            preferred = candidates[0]
        ordered = [preferred] + [p for p in candidates if p.name != preferred.name]
        requested_name = preferred.name
        errors: list[str] = []
        for idx, pipeline in enumerate(ordered):
            if idx > 0 and not allow_fallback:
                break
            try:
                path = pipeline.render(shot_payload, output_path)
                return RenderResult(
                    path=path,
                    pipeline_name=pipeline.name,
                    requested_pipeline=requested_name,
                    fallback_used=(pipeline.name != requested_name),
                    fallback_from=requested_name if pipeline.name != requested_name else "",
                    render_tier="fallback" if pipeline.name != requested_name else "production",
                )
            except Exception as e:
                err = f"[{pipeline.name}] {e}"
                errors.append(err)
                self._status[pipeline.name].last_error = str(e)
                if idx == 0 and not allow_fallback:
                    raise RenderError(
                        f"首选生产管线失败且已禁止静默降级: {err}\n"
                        "如需显式回退，请在 shot payload 中设置 allow_fallback=true "
                        "或导出 STORY_AGENT_ALLOW_RENDER_FALLBACK=1"
                    ) from e
                print(f"  ⚠️ {err}，尝试下一条管线…")
        raise RenderError("所有管线均失败:\n" + "\n".join(errors))

    def capability_matrix(self) -> dict:
        """返回可序列化的能力矩阵，供 UI 展示。"""
        if not self._probed:
            self.probe()
        return {
            name: {
                "available": s.available,
                "missing": s.missing,
                "last_error": s.last_error,
            }
            for name, s in self._status.items()
        }


# ─── 懒加载全局单例 ────────────────────────────────

_dispatcher: Optional[RenderDispatcher] = None


def get_dispatcher(config_path: Optional[Path] = None) -> RenderDispatcher:
    """返回全局 RenderDispatcher 单例（首次调用时从 pipeline_config.json 初始化）。"""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = RenderDispatcher.from_config(config_path)
    return _dispatcher


def reset_dispatcher() -> None:
    """重置单例（配置变更或测试时使用）。"""
    global _dispatcher
    _dispatcher = None


def load_pipeline_config(config_path: Optional[Path] = None) -> dict:
    if config_path is None:
        config_path = _PIPELINES_DIR / "pipeline_config.json"
    return json.loads(config_path.read_text())


def save_pipeline_config(cfg: dict, config_path: Optional[Path] = None) -> dict:
    if config_path is None:
        config_path = _PIPELINES_DIR / "pipeline_config.json"
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    reset_dispatcher()
    return cfg


def classify_pipeline_missing(missing: list[str]) -> tuple[str, str]:
    if not missing:
        return ("ready", "✅ 可生产")
    text = " ".join(missing).lower()
    if "workflow_file:" in text:
        return ("workflow_refinement_required", "🛠️ Workflow 待接线")
    if "node:" in text:
        return ("missing_nodes", "🧩 节点缺失")
    if any(token in text for token in ("gguf:", "encoder:", "flux_ckpt:", "ffmpeg")):
        return ("missing_models", "📦 模型/依赖缺失")
    return ("blocked", "⚠️ 待排查")


def set_active_pipeline_name(pipeline_name: str, config_path: Optional[Path] = None) -> dict:
    cfg = load_pipeline_config(config_path)
    entries = cfg.get("pipelines", [])
    names = [entry.get("name", "") for entry in entries]
    if pipeline_name not in names:
        raise ValueError(f"未知管线: {pipeline_name}，可用: {names}")
    debug_override = os.getenv("STORY_AGENT_ALLOW_UNREADY_PIPELINES", "").lower() in {"1", "true", "yes"}
    if not debug_override:
        entry = next((item for item in entries if item.get("name") == pipeline_name), {})
        if entry.get("production_ready") is False:
            raise ValueError(f"管线尚未通过生产验证，禁止切换到生产激活位: {pipeline_name}")
        dispatcher = RenderDispatcher.from_config(config_path or (_PIPELINES_DIR / "pipeline_config.json"))
        matrix = dispatcher.probe(force=True)
        status = matrix.get(pipeline_name)
        if status and not status.available:
            state_key, state_text = classify_pipeline_missing(status.missing)
            raise ValueError(
                f"管线未就绪，禁止切换到生产激活位: {pipeline_name} "
                f"({state_text}/{state_key})；阻塞项: {'; '.join(status.missing[:4])}"
            )
    cfg["active_pipeline"] = pipeline_name
    return save_pipeline_config(cfg, config_path)


# ─── 高层 API（供 batch_renderer 等调用方使用）────────────────────────────

def generate_scene_video(
    scene: dict,
    project_name: str = "project",
    seed: int = 0,
    lora_refs: Optional[list] = None,
    controlnet_type: Optional[str] = None,
    controlnet_strength: float = 0.6,
    fixed_seed: bool = False,
) -> dict:
    """
    为单个场景生成视频，通过 RenderDispatcher 自动回退。
    返回 {"scene", "success", "files", "output_path", "seed", [...]}。
    """
    import time as _time
    scene_name = scene.get("name", scene.get("scene_name", scene.get("location", "scene")))
    print(f"\n  === 场景: {scene_name} ===")

    actual_seed = seed if fixed_seed else int(_time.time() * 1000) % (2 ** 31)

    payload = dict(scene)
    payload.setdefault("project_name", project_name)
    if lora_refs:
        payload["lora_refs"] = lora_refs
    if controlnet_type:
        payload["controlnet_type"] = controlnet_type
        payload.setdefault("controlnet_strength", controlnet_strength)

    out_dir = _PROJECT_ROOT / "output" / project_name / "scenes"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = scene_name[:20].replace(" ", "_")
    output_path = out_dir / f"{safe_name}_{actual_seed}.mp4"

    try:
        dispatcher = get_dispatcher()
        render_result = dispatcher.render(payload, output_path)
        out = render_result.path
        print(f"  ✅ 生成完成: {out.name}, seed={actual_seed}, pipeline={render_result.pipeline_name}")
        return {
            "scene": scene_name,
            "success": True,
            "files": [{"filename": out.name, "path": str(out)}],
            "output_path": str(out),
            "pipeline_name": render_result.pipeline_name,
            "requested_pipeline": render_result.requested_pipeline,
            "fallback_used": render_result.fallback_used,
            "fallback_from": render_result.fallback_from,
            "render_tier": render_result.render_tier,
            "seed": actual_seed,
        }
    except RenderError as e:
        print(f"  ❌ 渲染失败: {e}")
        return {
            "scene": scene_name,
            "success": False,
            "error": str(e),
            "seed": actual_seed,
        }
