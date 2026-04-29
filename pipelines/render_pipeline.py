"""
能力声明式渲染管线

  RenderPipeline (ABC)
    ├── AnimateDiffPipeline  — ComfyUI AnimateDiff 动态帧 (animagine-xl-3.1 主力)
    ├── Wan2TI2VPipeline     — Wan2.2 TI2V 5B GGUF，文本+参考图→视频（模型就绪后最优）
    ├── StaticFramePipeline  — ComfyUI 单帧 txt2img → ffmpeg 成片（降级）
    └── StubPipeline         — ffmpeg 纯黑帧占位（兜底，无 ComfyUI 依赖）

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
    import requests as req
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = req.get(f"{comfyui_url}/history/{prompt_id}", timeout=10)
            if r.status_code == 200 and r.text not in ("", "{}"):
                hist = r.json()
                if prompt_id in hist:
                    status = hist[prompt_id].get("status", {})
                    if status.get("completed"):
                        return hist[prompt_id].get("outputs", {})
                    if status.get("error"):
                        return None
        except Exception:
            pass
        time.sleep(3)
    return None


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
    return files


def build_scene_prompt(scene: dict) -> str:
    """从统一 render_payload 构建 ComfyUI 生成 prompt。"""
    parts: list[str] = []
    scene_asset = scene.get("scene_asset", {}) or {}
    for key in ("description", "lighting", "atmosphere"):
        v = scene_asset.get(key, "")
        if v:
            parts.append(v)
    color = scene_asset.get("color_palette", "")
    if color:
        parts.append(f"palette {color}")
    location = scene.get("location", scene_asset.get("name", ""))
    if location:
        parts.insert(0, location)
    for key in ("time_of_day", "weather", "mood", "camera_angle", "narration", "style_guide"):
        v = scene.get(key, "")
        if v:
            parts.append(v)
    for char in scene.get("characters", []):
        if isinstance(char, dict):
            s = ", ".join(p for p in (char.get("name", ""), char.get("appearance", "")) if p)
            if s:
                parts.append(s)
        elif char:
            parts.append(str(char))
    dialogue = scene.get("dialogue_snippets", [])
    if dialogue:
        lines = []
        for line in dialogue[:2]:
            if isinstance(line, dict):
                l = " ".join(
                    p for p in (line.get("character", ""), line.get("line", ""),
                                line.get("emotion", "")) if p
                )
                if l:
                    lines.append(l)
        if lines:
            parts.append("dialogue beat: " + " | ".join(lines))
    parts.append(
        "anime storyboard frame, cinematic composition, detailed background, "
        "consistent character design, high quality"
    )
    return ", ".join(p for p in parts if p)


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

    # 找当前 positive / negative / model 引用点
    ckpt_ids = find_nodes_by_type(workflow, "CheckpointLoaderSimple")
    pos_id = find_ksampler_positive_node(workflow)
    neg_id = find_ksampler_negative_node(workflow)
    model_ref = [ckpt_ids[0], 0] if ckpt_ids else ["1", 0]
    pos_ref = [pos_id, 0] if pos_id else ["3", 0]
    neg_ref = [neg_id, 0] if neg_id else ["4", 0]

    # ApplyInstantID → 输出 [0]=model, [1]=positive, [2]=negative
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
            "ip_weight": ip_weight,
            "cn_strength": cn_strength,
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
    def render(self, shot_payload: dict, output_path: Path) -> Path:
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
# AnimateDiffPipeline
# ══════════════════════════════════════════════════════

class AnimateDiffPipeline(RenderPipeline):
    """ComfyUI AnimateDiff 动态帧管线（首选）。"""

    name = "animatediff"
    required_nodes = [
        "CheckpointLoaderSimple",
        "ADE_LoadAnimateDiffModel",
        "ADE_ApplyAnimateDiffModelSimple",
        "ADE_UseEvolvedSampling",
        "KSampler",
        "CLIPTextEncode",
        "VAEDecodeTiled",
        "VHS_VideoCombine",
    ]

    def _build_workflow(self, prompt: str, scene_name: str, seed: int) -> dict:
        cfg = self.config
        checkpoint = cfg.get("checkpoint", "sd_xl_base_1.0.safetensors")
        motion_model = cfg.get("motion_model", "hsxl_temporal_layers.f16.safetensors")
        width = cfg.get("width", 1024)
        height = cfg.get("height", 1024)
        frames = cfg.get("frames", 16)
        fps = cfg.get("fps", 8)
        steps = cfg.get("steps", 20)
        cfg_scale = cfg.get("cfg", 7.0)
        prefix = f"scene_{scene_name[:20].replace(' ', '_')}"

        # 优先加载 workflow 文件，覆盖其中的模型名
        wf_file = cfg.get("workflow_file")
        if wf_file:
            wf_path = _PIPELINES_DIR / wf_file
            if wf_path.exists():
                wf = json.loads(wf_path.read_text())
                for node in wf.values():
                    ct = node.get("class_type", "")
                    if ct == "CheckpointLoaderSimple":
                        node["inputs"]["ckpt_name"] = checkpoint
                    elif ct == "ADE_LoadAnimateDiffModel":
                        node["inputs"]["model_name"] = motion_model
                return wf

        # 无 workflow 文件时内联构建
        return {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": checkpoint}},
            "2": {"class_type": "ADE_EmptyLatentImageLarge",
                  "inputs": {"batch_size": frames, "width": width, "height": height}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["1", 1], "text": prompt}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["1", 1], "text": NEGATIVE_PROMPT}},
            "5": {"class_type": "ADE_LoadAnimateDiffModel",
                  "inputs": {"model_name": motion_model}},
            "6": {"class_type": "ADE_ApplyAnimateDiffModelSimple",
                  "inputs": {"motion_model": ["5", 0]}},
            "7": {"class_type": "ADE_UseEvolvedSampling",
                  "inputs": {"model": ["1", 0], "beta_schedule": "autoselect",
                             "m_models": ["6", 0]}},
            "8": {"class_type": "KSampler",
                  "inputs": {"model": ["7", 0], "positive": ["3", 0],
                             "negative": ["4", 0], "latent_image": ["2", 0],
                             "steps": steps, "cfg": cfg_scale,
                             "sampler_name": "euler", "scheduler": "normal",
                             "seed": seed, "denoise": 1.0}},
            "9": {"class_type": "VAEDecodeTiled",
                  "inputs": {"samples": ["8", 0], "vae": ["1", 2],
                             "tile_size": 512, "overlap": 64,
                             "temporal_size": 64, "temporal_overlap": 8}},
            "10": {"class_type": "VHS_VideoCombine",
                   "inputs": {"images": ["9", 0], "frame_rate": fps, "loop_count": 0,
                              "filename_prefix": prefix, "format": "video/h264-mp4",
                              "pingpong": False, "save_output": True}},
        }

    def render(self, shot_payload: dict, output_path: Path) -> Path:
        scene_name = shot_payload.get("location", shot_payload.get("name", "scene"))
        prompt = build_scene_prompt(shot_payload)
        seed = int(time.time() * 1000) % (2 ** 31)

        wf = self._build_workflow(prompt, scene_name, seed)
        wf = inject_prompt(wf, prompt, NEGATIVE_PROMPT)
        wf = inject_seed(wf, seed)

        lora_refs = shot_payload.get("lora_refs") or []
        if lora_refs:
            wf = inject_loras(wf, lora_refs)

        cn_type = shot_payload.get("controlnet_type")
        cn_ref = shot_payload.get("controlnet_image_ref")
        if cn_type and cn_ref:
            wf = inject_controlnet(wf, cn_type, image_ref=cn_ref,
                                   comfyui_url=self.comfyui_url)

        # ── InstantID：有参考人脸图时自动注入，提升角色一致性 ──
        ref_face = shot_payload.get("reference_face_image")
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
                print(f"[InstantID] 注入失败（{e}），继续无 InstantID 渲染")

        prompt_id = submit_workflow(wf, self.comfyui_url)
        if not prompt_id:
            raise RenderError("ComfyUI 拒绝提交 AnimateDiff 工作流")

        timeout = self.config.get("timeout", 7200)
        outputs = wait_for_completion(prompt_id, self.comfyui_url, timeout=timeout)
        if not outputs:
            raise RenderError(f"AnimateDiff 渲染超时 (prompt_id={prompt_id[:8]})")

        files = get_video_output(outputs)
        if not files:
            raise RenderError("ComfyUI 输出中无视频文件")

        vf = files[0]
        subfolder = vf.get("subfolder", "")
        src = (_COMFYUI_OUTPUT_DIR / subfolder / vf["filename"]
               if subfolder else _COMFYUI_OUTPUT_DIR / vf["filename"])
        if not src.exists():
            raise RenderError(f"输出视频文件不存在: {src}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, output_path)
        return output_path


# ══════════════════════════════════════════════════════
# StaticFramePipeline
# ══════════════════════════════════════════════════════

class StaticFramePipeline(RenderPipeline):
    """ComfyUI 单帧 txt2img → ffmpeg 成片，无 AnimateDiff 依赖（降级选项）。"""

    name = "static_frame"
    required_nodes = [
        "CheckpointLoaderSimple",
        "EmptyLatentImage",
        "KSampler",
        "CLIPTextEncode",
        "VAEDecode",
        "SaveImage",
    ]

    def validate(self, object_info: dict) -> list[str]:
        missing = super().validate(object_info)
        if not shutil.which("ffmpeg"):
            missing.append("ffmpeg (not installed)")
        return missing

    def _build_workflow(self, prompt: str, seed: int) -> dict:
        cfg = self.config
        checkpoint = cfg.get("checkpoint", "v1-5-pruned-emaonly.safetensors")
        width = cfg.get("width", 512)
        height = cfg.get("height", 768)
        steps = cfg.get("steps", 20)
        cfg_scale = cfg.get("cfg", 7.0)
        return {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": checkpoint}},
            "2": {"class_type": "EmptyLatentImage",
                  "inputs": {"batch_size": 1, "width": width, "height": height}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["1", 1], "text": prompt}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["1", 1], "text": NEGATIVE_PROMPT}},
            "5": {"class_type": "KSampler",
                  "inputs": {"model": ["1", 0], "positive": ["3", 0],
                             "negative": ["4", 0], "latent_image": ["2", 0],
                             "steps": steps, "cfg": cfg_scale,
                             "sampler_name": "euler_a", "scheduler": "karras",
                             "seed": seed, "denoise": 1.0}},
            "6": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage",
                  "inputs": {"images": ["6", 0], "filename_prefix": "static_frame"}},
        }

    def render(self, shot_payload: dict, output_path: Path) -> Path:
        prompt = build_scene_prompt(shot_payload)
        seed = int(time.time() * 1000) % (2 ** 31)
        lora_refs = shot_payload.get("lora_refs") or []

        wf = self._build_workflow(prompt, seed)
        if lora_refs:
            wf = inject_loras(wf, lora_refs)

        prompt_id = submit_workflow(wf, self.comfyui_url)
        if not prompt_id:
            raise RenderError("ComfyUI 拒绝提交静态帧工作流")

        timeout = self.config.get("timeout", 300)
        outputs = wait_for_completion(prompt_id, self.comfyui_url, timeout=timeout)
        if not outputs:
            raise RenderError("静态帧渲染超时")

        img_path: Optional[Path] = None
        for node_out in outputs.values():
            for item in node_out.get("images", []):
                if isinstance(item, dict) and item.get("filename"):
                    subfolder = item.get("subfolder", "")
                    p = (_COMFYUI_OUTPUT_DIR / subfolder / item["filename"]
                         if subfolder else _COMFYUI_OUTPUT_DIR / item["filename"])
                    if p.exists():
                        img_path = p
                        break
            if img_path:
                break
        if not img_path:
            raise RenderError("静态帧输出图像文件不存在")

        duration = self.config.get("duration_sec", 3.0)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["ffmpeg", "-y", "-loop", "1", "-i", str(img_path),
             "-t", str(duration),
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output_path)],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RenderError(f"ffmpeg 静态帧转视频失败: {r.stderr.decode()[:300]}")
        return output_path


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
      - Wan2.2-TI2V-5B-Q4_K_M.gguf ≥ 3 GB（当前文件仅 49 MB，下载不完整）
      - UMT5-XXL text encoder
      - wan2_ti2v_workflow.json 工作流文件（手动配置）

    validate() 会检测模型文件大小并给出明确提示。
    """

    name = "wan2_ti2v"
    required_nodes = [
        "UnetLoaderGGUF",
        "VHS_VideoCombine",
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
            min_enc_mb = self.config.get("min_encoder_size_mb", 9000)
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

        prompt = build_scene_prompt(shot_payload)
        seed = int(time.time() * 1000) % (2 ** 31)

        # Wan2 工作流拓扑：CLIPTextEncode → WanImageToVideo → KSampler
        # inject_prompt 只追踪 KSampler.positive 一跳，会落到 WanImageToVideo（无 text 字段）
        # 因此直接向 CLIPTextEncode 节点注入文本
        clip_ids = find_nodes_by_type(wf, "CLIPTextEncode")
        if clip_ids:
            wf[clip_ids[0]]["inputs"]["text"] = prompt   # 第一个 = positive
        else:
            wf = inject_prompt(wf, prompt, "")           # fallback
        wf = inject_seed(wf, seed)

        # 注入参考图（TI2V 必须）
        ref_image = shot_payload.get("reference_image_path")
        if not ref_image or not Path(str(ref_image)).exists():
            raise RenderError(
                "Wan2TI2VPipeline 需要 shot_payload['reference_image_path']，"
                "当前 shot 无参考图，回退至 AnimateDiff"
            )
        uploaded_name = self._upload_image(ref_image)
        # 找 LoadImage 节点并注入文件名
        for node in wf.values():
            if node.get("class_type") == "LoadImage":
                node["inputs"]["image"] = uploaded_name
                break

        # 注入 GGUF 模型路径
        gguf_name = Path(os.path.expanduser(self.config.get("gguf_path", ""))).name
        for node in wf.values():
            if node.get("class_type") == "UnetLoaderGGUF":
                node["inputs"]["unet_name"] = gguf_name
                break

        prompt_id = submit_workflow(wf, self.comfyui_url)
        if not prompt_id:
            raise RenderError("ComfyUI 拒绝提交 Wan2.2 工作流")

        timeout = self.config.get("timeout", 7200)
        outputs = wait_for_completion(prompt_id, self.comfyui_url, timeout=timeout)
        if not outputs:
            raise RenderError(f"Wan2.2 渲染超时 (prompt_id={prompt_id[:8]})")

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


# ══════════════════════════════════════════════════════
# FluxWan2TwoStagePipeline
# ══════════════════════════════════════════════════════

class FluxWan2TwoStagePipeline(RenderPipeline):
    """
    两阶段高质量管线：
      Stage 1 — Flux 2 Klein 9B txt2img → 高质量参考帧
      Stage 2 — Wan2.2 TI2V 5B GGUF    → 参考帧动画化

    所需模型（全部就绪后自动激活）：
      • checkpoints/flux_2_klein_9B/flux-2-klein-9b.safetensors  ✅ 已下载 17GB
      • unet/Wan2.2-TI2V-5B-Q4_K_M.gguf                         ❌ 下载不完整 (49MB/3.2GB)
      • text_encoders/wan2.2_umt5/models_t5_umt5-xxl-enc-bf16.pth ❌ 缺失

    工作流文件：
      • flux_txt2img_workflow.json  — Flux Stage 1
      • wan2_ti2v_workflow.json     — Wan2.2 Stage 2
    """

    name = "flux_wan2_twostage"
    required_nodes = ["UnetLoaderGGUF", "VHS_VideoCombine"]

    def validate(self, object_info: dict) -> list[str]:
        missing = super().validate(object_info)

        # Stage 1: Flux workflow
        flux_wf = _PIPELINES_DIR / self.config.get("flux_workflow_file", "flux_txt2img_workflow.json")
        if not flux_wf.exists():
            missing.append(f"workflow_file:{flux_wf.name}")

        # Stage 1: Flux checkpoint
        flux_ckpt = Path(os.path.expanduser(
            self.config.get("flux_checkpoint", "")
        ))
        if flux_ckpt.name:
            min_flux_gb = self.config.get("min_flux_size_gb", 10)
            if not flux_ckpt.exists():
                missing.append(f"flux_ckpt:{flux_ckpt.name} (不存在)")
            elif flux_ckpt.stat().st_size / (1024 ** 3) < min_flux_gb:
                missing.append(f"flux_ckpt:{flux_ckpt.name} 下载不完整")

        # Stage 2: Wan2.2 GGUF
        wan_wf = _PIPELINES_DIR / self.config.get("wan2_workflow_file", "wan2_ti2v_workflow.json")
        if not wan_wf.exists():
            missing.append(f"workflow_file:{wan_wf.name}")

        gguf_raw = self.config.get("gguf_path", "")
        if gguf_raw:
            gguf_path = Path(os.path.expanduser(gguf_raw))
            min_mb = self.config.get("min_gguf_size_mb", 3000)
            if not gguf_path.exists():
                missing.append(f"gguf:{gguf_path.name} (不存在)")
            elif gguf_path.stat().st_size / (1024 * 1024) < min_mb:
                actual = gguf_path.stat().st_size / (1024 * 1024)
                missing.append(f"gguf:{gguf_path.name} 下载不完整 ({actual:.0f}MB < {min_mb}MB)")

        enc_raw = self.config.get("text_encoder", "")
        if enc_raw:
            enc_path = Path(os.path.expanduser(enc_raw))
            min_enc_mb = self.config.get("min_encoder_size_mb", 9000)
            if not enc_path.exists():
                missing.append(f"encoder:{enc_path.name} (不存在)")
            elif enc_path.stat().st_size / (1024 * 1024) < min_enc_mb:
                missing.append(f"encoder:{enc_path.name} 下载不完整")

        return missing

    def render(self, shot_payload: dict, output_path: Path) -> Path:
        """Stage 1: Flux 生成参考帧 → Stage 2: Wan2.2 动画化。"""
        import tempfile

        # ── Stage 1: Flux txt2img ─────────────────────────────
        flux_wf_file = _PIPELINES_DIR / self.config.get("flux_workflow_file", "flux_txt2img_workflow.json")
        flux_wf = json.loads(flux_wf_file.read_text())
        prompt = build_scene_prompt(shot_payload)
        seed = int(time.time() * 1000) % (2 ** 31)

        # Flux 工作流使用 CLIPTextEncodeFlux（字段名 t5xxl/clip_l，无 text 字段）
        # inject_prompt 无法正确注入；直接写 t5xxl / clip_l
        flux_clip_ids = find_nodes_by_type(flux_wf, "CLIPTextEncodeFlux")
        if flux_clip_ids:
            flux_wf[flux_clip_ids[0]]["inputs"]["t5xxl"] = prompt
            flux_wf[flux_clip_ids[0]]["inputs"]["clip_l"] = prompt
        else:
            flux_wf = inject_prompt(flux_wf, prompt, "")  # fallback
        flux_wf = inject_seed(flux_wf, seed)

        flux_id = submit_workflow(flux_wf, self.comfyui_url)
        if not flux_id:
            raise RenderError("Flux Stage1 提交失败")

        flux_outputs = wait_for_completion(flux_id, self.comfyui_url,
                                           timeout=self.config.get("flux_timeout", 600))
        if not flux_outputs:
            raise RenderError("Flux Stage1 渲染超时")

        # 找生成的图片
        ref_img_path: Optional[Path] = None
        for node_out in flux_outputs.values():
            for item in node_out.get("images", []):
                if isinstance(item, dict) and item.get("filename"):
                    sub = item.get("subfolder", "")
                    p = (_COMFYUI_OUTPUT_DIR / sub / item["filename"]
                         if sub else _COMFYUI_OUTPUT_DIR / item["filename"])
                    if p.exists():
                        ref_img_path = p
                        break
            if ref_img_path:
                break
        if not ref_img_path:
            raise RenderError("Flux Stage1 未生成图片")

        # ── Stage 2: Wan2.2 TI2V ─────────────────────────────
        wan_wf_file = _PIPELINES_DIR / self.config.get("wan2_workflow_file", "wan2_ti2v_workflow.json")
        wan_wf = json.loads(wan_wf_file.read_text())
        # Wan2 工作流同样需要直接写 CLIPTextEncode（与 Wan2TI2VPipeline.render 保持一致）
        wan_clip_ids = find_nodes_by_type(wan_wf, "CLIPTextEncode")
        if wan_clip_ids:
            wan_wf[wan_clip_ids[0]]["inputs"]["text"] = prompt
        else:
            wan_wf = inject_prompt(wan_wf, prompt, "")
        wan_wf = inject_seed(wan_wf, seed + 1)

        uploaded = self._upload_image(str(ref_img_path))
        for node in wan_wf.values():
            if node.get("class_type") == "LoadImage":
                node["inputs"]["image"] = uploaded
                break

        gguf_name = Path(os.path.expanduser(self.config.get("gguf_path", ""))).name
        for node in wan_wf.values():
            if node.get("class_type") == "UnetLoaderGGUF":
                node["inputs"]["unet_name"] = gguf_name
                break

        wan_id = submit_workflow(wan_wf, self.comfyui_url)
        if not wan_id:
            raise RenderError("Wan2.2 Stage2 提交失败")

        wan_outputs = wait_for_completion(wan_id, self.comfyui_url,
                                          timeout=self.config.get("timeout", 7200))
        if not wan_outputs:
            raise RenderError("Wan2.2 Stage2 渲染超时")

        files = get_video_output(wan_outputs)
        if not files:
            raise RenderError("Wan2.2 Stage2 无视频输出")

        vf = files[0]
        sub = vf.get("subfolder", "")
        src = (_COMFYUI_OUTPUT_DIR / sub / vf["filename"]
               if sub else _COMFYUI_OUTPUT_DIR / vf["filename"])
        if not src.exists():
            raise RenderError(f"Wan2.2 Stage2 视频文件不存在: {src}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, output_path)
        return output_path


# ══════════════════════════════════════════════════════
# Dispatcher
# ══════════════════════════════════════════════════════

_PIPELINE_CLASSES: dict[str, type[RenderPipeline]] = {
    "AnimateDiffPipeline": AnimateDiffPipeline,
    "Wan2TI2VPipeline": Wan2TI2VPipeline,
    "FluxWan2TwoStagePipeline": FluxWan2TwoStagePipeline,
    "StaticFramePipeline": StaticFramePipeline,
    "StubPipeline": StubPipeline,
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
                 comfyui_url: str = "http://127.0.0.1:8188"):
        self._pipelines = sorted(pipelines, key=lambda x: x[0])
        self.comfyui_url = comfyui_url
        self._status: dict[str, PipelineStatus] = {}
        self._probed = False

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
        return cls(pipelines, comfyui_url=url)

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
        return [
            p for _, p in self._pipelines
            if self._status.get(p.name, PipelineStatus(False)).available
        ]

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
        errors: list[str] = []
        for pipeline in candidates:
            try:
                return pipeline.render(shot_payload, output_path)
            except Exception as e:
                err = f"[{pipeline.name}] {e}"
                errors.append(err)
                self._status[pipeline.name].last_error = str(e)
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
