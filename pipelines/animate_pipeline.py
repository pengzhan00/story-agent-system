"""
story-agent-system → ComfyUI AnimateDiff 批量管线

从故事系统读取剧本场景，逐个用 AnimateDiff 生成动画视频片段。
支持: 动态节点查找 / LoRA 注入 / ControlNet 管线 / 种子管理
"""

import json
import os
import time
import sys
from pathlib import Path
from typing import Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── 配置 ──────────────────────────────────────────────

COMFYUI_URL = "http://127.0.0.1:8188"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
WORKFLOW_FILE = Path(__file__).parent / "animatediff_workflow.json"

# SDXL 参数
SDXL_WIDTH = 1024
SDXL_HEIGHT = 1024
ANIMATION_FRAMES = 16
FRAME_RATE = 8
SAMPLER_STEPS = 20
CFG_SCALE = 7.0

# 负面提示词
NEGATIVE_PROMPT = (
    "blurry, low quality, distorted, deformed, ugly, bad anatomy, "
    "watermark, text, extra limbs, fused fingers, deformed hands, "
    "poorly drawn face, mutation, mutated, extra limbs, gross proportions"
)

# ─── 动态节点查找 ──────────────────────────────────────

_object_info_cache: Optional[dict] = None


def get_object_info(force_refresh: bool = False) -> dict:
    """从 ComfyUI 获取所有可用节点定义（带缓存）。"""
    global _object_info_cache
    import requests
    if _object_info_cache is None or force_refresh:
        try:
            r = requests.get(f"{COMFYUI_URL}/object_info", timeout=10)
            if r.status_code == 200:
                _object_info_cache = r.json()
        except Exception:
            _object_info_cache = {}
    return _object_info_cache or {}


def find_nodes_by_type(workflow: dict, class_type: str) -> list[str]:
    """在 workflow 中按 class_type 找所有节点 ID。"""
    return [nid for nid, node in workflow.items()
            if node.get("class_type") == class_type]


def find_ksampler_positive_node(workflow: dict) -> Optional[str]:
    """找 KSampler 的 positive 输入连接的那个 CLIPTextEncode 节点 ID。"""
    for kid in find_nodes_by_type(workflow, "KSampler"):
        positive_ref = workflow[kid].get("inputs", {}).get("positive")
        if isinstance(positive_ref, list) and positive_ref:
            return str(positive_ref[0])
    return None


def find_ksampler_negative_node(workflow: dict) -> Optional[str]:
    for kid in find_nodes_by_type(workflow, "KSampler"):
        neg_ref = workflow[kid].get("inputs", {}).get("negative")
        if isinstance(neg_ref, list) and neg_ref:
            return str(neg_ref[0])
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
    """向所有 KSampler 注入种子。"""
    for kid in find_nodes_by_type(workflow, "KSampler"):
        workflow[kid]["inputs"]["seed"] = seed
    return workflow


# ─── LoRA 注入 ─────────────────────────────────────────

def _make_lora_node(node_id: str, lora_name: str, model_ref: list, clip_ref: list,
                    strength_model: float = 0.8, strength_clip: float = 0.8) -> dict:
    return {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": lora_name,
            "strength_model": strength_model,
            "strength_clip": strength_clip,
            "model": model_ref,
            "clip": clip_ref,
        }
    }


def inject_loras(workflow: dict, lora_refs: list[dict]) -> dict:
    """
    向 workflow 注入 LoRA 链。
    lora_refs: [{"name": "char_lora.safetensors", "strength": 0.8}, ...]
    自动接入 CheckpointLoaderSimple → LoraLoader... → KSampler
    """
    if not lora_refs:
        return workflow

    checkpoint_ids = find_nodes_by_type(workflow, "CheckpointLoaderSimple")
    if not checkpoint_ids:
        return workflow

    ckpt_id = checkpoint_ids[0]
    current_model_ref = [ckpt_id, 0]
    current_clip_ref = [ckpt_id, 1]

    existing_ids = set(workflow.keys())
    next_id = max(int(k) for k in existing_ids if k.isdigit()) + 1

    lora_output_model = None
    lora_output_clip = None

    for i, lora in enumerate(lora_refs):
        lora_name = lora.get("name", "")
        if not lora_name:
            continue
        strength = float(lora.get("strength", 0.8))
        nid = str(next_id + i)
        workflow[nid] = _make_lora_node(
            nid, lora_name,
            model_ref=current_model_ref,
            clip_ref=current_clip_ref,
            strength_model=strength,
            strength_clip=strength,
        )
        current_model_ref = [nid, 0]
        current_clip_ref = [nid, 1]
        lora_output_model = current_model_ref
        lora_output_clip = current_clip_ref

    if lora_output_model:
        for kid in find_nodes_by_type(workflow, "KSampler"):
            if isinstance(workflow[kid]["inputs"].get("model"), list):
                workflow[kid]["inputs"]["model"] = lora_output_model
        for kid in find_nodes_by_type(workflow, "ADE_UseEvolvedSampling"):
            if isinstance(workflow[kid]["inputs"].get("model"), list):
                workflow[kid]["inputs"]["model"] = lora_output_model
        for kid in find_nodes_by_type(workflow, "CLIPTextEncode"):
            if isinstance(workflow[kid]["inputs"].get("clip"), list):
                workflow[kid]["inputs"]["clip"] = lora_output_clip

    return workflow


# ─── ControlNet 管线 ──────────────────────────────────

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
) -> dict:
    """
    向 workflow 注入 ControlNet。
    control_type: "canny" | "depth" | "openpose" | "lineart" | "tile"
    image_ref: ComfyUI 节点引用 [node_id, output_slot]，None 时跳过。
    """
    if not image_ref:
        return workflow

    cn_name = _CONTROLNET_MAP.get(control_type, f"controlnet_{control_type}.safetensors")
    object_info = get_object_info()
    if "ControlNetLoader" not in object_info:
        print(f"[AnimatePipeline] ComfyUI 未安装 ControlNet 节点，跳过注入")
        return workflow

    existing_ids = set(workflow.keys())
    next_id = max(int(k) for k in existing_ids if k.isdigit()) + 1

    loader_id = str(next_id)
    apply_id = str(next_id + 1)

    workflow[loader_id] = {
        "class_type": "ControlNetLoader",
        "inputs": {"control_net_name": cn_name},
    }

    positive_id = find_ksampler_positive_node(workflow)
    pos_ref = [positive_id, 0] if positive_id else ["3", 0]

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


def submit_workflow(workflow: dict) -> str | None:
    """提交工作流到 ComfyUI，返回 prompt_id 或 None"""
    import requests
    payload = {"prompt": workflow}
    r = requests.post(f"{COMFYUI_URL}/prompt", json=payload, timeout=30)
    r.raise_for_status()
    result = r.json()
    if "prompt_id" in result:
        return result["prompt_id"]
    print(f"  [ERROR] 提交失败: {result}")
    return None


def wait_for_completion(prompt_id: str, timeout: int = 300) -> dict | None:
    """等待 ComfyUI 工作流完成，返回 outputs dict"""
    import requests
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
            if r.status_code == 200 and r.text and r.text != "{}":
                hist = r.json()
                if prompt_id in hist:
                    status = hist[prompt_id].get("status", {})
                    if status.get("completed"):
                        return hist[prompt_id].get("outputs", {})
                    elif status.get("error"):
                        print(f"  [ERROR] 工作流执行错误: {status}")
                        return None
        except Exception as e:
            pass
        
        # 检查队列
        try:
            q = requests.get(f"{COMFYUI_URL}/queue", timeout=5).json()
            if not q.get("queue_running") and not q.get("queue_pending"):
                pass  # 可能完成了或失败了
        except:
            pass
        
        time.sleep(3)
    print(f"  [TIMEOUT] {prompt_id} 超时")
    return None


def build_scene_prompt(scene: dict) -> str:
    """从统一 render_payload 构建生成 prompt"""
    parts = []

    scene_asset = scene.get("scene_asset", {}) or {}
    character_refs = scene.get("characters", []) or []
    dialogue = scene.get("dialogue_snippets", []) or []

    location = scene.get("location", scene_asset.get("name", ""))
    if location:
        parts.append(location)
    if scene_asset.get("description"):
        parts.append(scene_asset["description"])
    if scene_asset.get("lighting"):
        parts.append(scene_asset["lighting"])
    if scene_asset.get("color_palette"):
        parts.append(f"palette {scene_asset['color_palette']}")
    if scene_asset.get("atmosphere"):
        parts.append(scene_asset["atmosphere"])

    time_of_day = scene.get("time_of_day", "")
    weather = scene.get("weather", "")
    mood = scene.get("mood", "")
    camera_angle = scene.get("camera_angle", "")
    narration = scene.get("narration", "")
    style_guide = scene.get("style_guide", "")

    for item in [time_of_day, weather, mood, camera_angle, narration, style_guide]:
        if item:
            parts.append(item)

    for char in character_refs:
        if isinstance(char, dict):
            char_part = ", ".join(
                p for p in [char.get("name", ""), char.get("appearance", "")] if p
            )
            if char_part:
                parts.append(char_part)
        elif char:
            parts.append(str(char))

    if dialogue:
        sample_lines = []
        for line in dialogue[:2]:
            if isinstance(line, dict):
                speaker = line.get("character", "")
                text = line.get("line", "")
                emotion = line.get("emotion", "")
                sample_lines.append(" ".join(p for p in [speaker, text, emotion] if p))
        if sample_lines:
            parts.append("dialogue beat: " + " | ".join(sample_lines))

    parts.append("anime storyboard frame, cinematic composition, detailed background, consistent character design, high quality")

    return ", ".join(p for p in parts if p)


def create_animatediff_workflow(positive_prompt: str, scene_name: str, seed: int = 0) -> dict:
    """创建 AnimateDiff 工作流 JSON (使用 ADE_LoadAnimateDiffModel + ADE_ApplyAnimateDiffModelSimple)"""
    prefix = f"scene_{scene_name[:20].replace(' ', '_')}"
    wf = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}
        },
        "2": {
            "class_type": "ADE_EmptyLatentImageLarge",
            "inputs": {
                "batch_size": ANIMATION_FRAMES,
                "width": SDXL_WIDTH,
                "height": SDXL_HEIGHT
            }
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": positive_prompt}
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": NEGATIVE_PROMPT}
        },
        "5": {
            "class_type": "ADE_LoadAnimateDiffModel",
            "inputs": {"model_name": "hsxl_temporal_layers.f16.safetensors"}
        },
        "6": {
            "class_type": "ADE_ApplyAnimateDiffModelSimple",
            "inputs": {"motion_model": ["5", 0]}
        },
        "7": {
            "class_type": "ADE_UseEvolvedSampling",
            "inputs": {
                "model": ["1", 0],
                "beta_schedule": "autoselect",
                "m_models": ["6", 0]
            }
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["7", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["2", 0],
                "steps": SAMPLER_STEPS,
                "cfg": CFG_SCALE,
                "sampler_name": "euler",
                "scheduler": "normal",
                "seed": seed,
                "denoise": 1.0
            }
        },
        "9": {
            "class_type": "VAEDecodeTiled",
            "inputs": {
                "samples": ["8", 0],
                "vae": ["1", 2],
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 64,
                "temporal_overlap": 8
            }
        },
        "10": {
            "class_type": "VHS_VideoCombine",
            "inputs": {
                "images": ["9", 0],
                "frame_rate": FRAME_RATE,
                "loop_count": 0,
                "filename_prefix": prefix,
                "format": "video/h264-mp4",
                "pingpong": False,
                "save_output": True
            }
        },
    }

    return wf


def get_video_output(comfyui_outputs: dict) -> list[dict]:
    """从 ComfyUI 输出中提取视频文件信息"""
    files = []
    for node_id, node_out in comfyui_outputs.items():
        for key in ("gifs", "videos", "images"):
            items = node_out.get(key, [])
            for item in items:
                if isinstance(item, dict):
                    fn = item.get("filename", "")
                    sub = item.get("subfolder", "")
                    tp = item.get("type", "output")
                    if fn:
                        files.append({
                            "filename": fn,
                            "subfolder": sub,
                            "type": tp,
                            "node": node_id
                        })
    return files


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
    为单个场景生成动画视频，支持 LoRA / ControlNet / 种子管理。
    lora_refs: [{"name": "char.safetensors", "strength": 0.8}, ...]
    controlnet_type: "canny" | "depth" | "openpose" | None
    fixed_seed: True 时使用传入 seed（复现），False 时每次随机
    """
    import requests

    scene_name = scene.get("name", scene.get("scene_name", "scene"))
    print(f"\n  === 场景: {scene_name} ===")

    prompt = build_scene_prompt(scene)
    print(f"  提示词: {prompt[:100]}...")

    # 加载工作流
    if WORKFLOW_FILE.exists():
        workflow = json.loads(WORKFLOW_FILE.read_text())
    else:
        workflow = create_animatediff_workflow(prompt, scene_name, seed)

    # 动态注入 prompt（不再硬编码节点 ID）
    workflow = inject_prompt(workflow, prompt, NEGATIVE_PROMPT)

    # 种子管理
    actual_seed = seed if fixed_seed else int(time.time() * 1000) % (2**31)
    workflow = inject_seed(workflow, actual_seed)

    # LoRA 注入
    if lora_refs:
        workflow = inject_loras(workflow, lora_refs)

    # ControlNet 注入（需要参考图时使用）
    if controlnet_type:
        ref_image = scene.get("controlnet_image_ref")
        if ref_image:
            workflow = inject_controlnet(workflow, controlnet_type,
                                         image_ref=ref_image,
                                         strength=controlnet_strength)

    prompt_id = submit_workflow(workflow)
    if not prompt_id:
        return {"scene": scene_name, "success": False, "error": "提交失败", "seed": actual_seed}

    print(f"  等待渲染 (prompt_id: {prompt_id[:8]}, seed: {actual_seed})...")
    outputs = wait_for_completion(prompt_id)
    if not outputs:
        return {"scene": scene_name, "success": False, "error": "渲染超时", "seed": actual_seed}

    files = get_video_output(outputs)
    result = {
        "scene": scene_name,
        "success": True if files else False,
        "files": files,
        "outputs": outputs,
        "seed": actual_seed,
    }

    if files:
        print(f"  ✅ 生成完成: {', '.join(f['filename'] for f in files)}, seed={actual_seed}")
    else:
        print(f"  ⚠️  无输出文件")

    return result


def build_lora_refs_from_scene(scene: dict, project_id: int = 0) -> list[dict]:
    """从场景的 render_payload 中提取 LoRA 引用。"""
    try:
        import core.database as db
        payload = scene.get("render_payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)

        loras = []
        # 角色 LoRA
        for char_ref in payload.get("characters", []):
            char_name = char_ref.get("name", "")
            if project_id and char_name:
                chars = db.list_characters(project_id)
                char = next((c for c in chars if c.name == char_name), None)
                if char and char.lora_ref:
                    loras.append({"name": char.lora_ref, "strength": 0.8, "type": "character"})

        # 场景 LoRA
        scene_name = payload.get("scene_asset", {}).get("name", "") or scene.get("location", "")
        if project_id and scene_name:
            scene_assets = db.list_scene_assets(project_id)
            sa = next((s for s in scene_assets if s.name == scene_name), None)
            if sa and sa.lora_ref:
                loras.append({"name": sa.lora_ref, "strength": 0.6, "type": "scene"})

        return loras
    except Exception:
        return []


def pipeline_from_db(project_id: int, db_module=None, seed: int = 42) -> list[dict]:
    """从数据库读取场景并批量渲染"""
    print(f"开始批量管线: project_id={project_id}")
    
    results = []
    
    if db_module:
        project = db_module.get_project(project_id)
        if not project:
            print(f"[ERROR] 找不到项目: {project_id}")
            return results
        
        project_name = project.name if hasattr(project, 'name') else str(project_id)
        print(f"项目: {project_name}")
        
        # 获取剧本
        script = db_module.get_script(project_id)
        if script:
            print(f"剧本: {script.name if hasattr(script, 'name') else 'unknown'}")
        
        # 获取所有场景
        scenes = db_module.get_scenes(project_id)
        print(f"场景数: {len(scenes)}")
        
        for i, scene in enumerate(scenes):
            current_seed = seed + i * 1000
            r = generate_scene_video(scene, project_name, current_seed)
            results.append(r)
    
    return results


def pipeline_from_json(json_path: str, seed: int = 42) -> list[dict]:
    """从 JSON 场景列表文件批量渲染"""
    with open(json_path) as f:
        scenes = json.load(f)
    
    project_name = Path(json_path).stem
    results = []
    
    for i, scene in enumerate(scenes):
        current_seed = seed + i * 1000
        r = generate_scene_video(scene, project_name, current_seed)
        results.append(r)
    
    return results


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="漫剧动画批量管线")
    parser.add_argument("--project", type=int, help="从数据库加载项目 ID")
    parser.add_argument("--json", type=str, help="从 JSON 文件加载场景列表")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    
    args = parser.parse_args()
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    if args.project:
        # 从数据库导入
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import core.database as db
        db.init_db()
        results = pipeline_from_db(args.project, db_module=db, seed=args.seed)
        
        # 保存结果摘要
        summary = {
            "project_id": args.project,
            "results": results,
            "success_count": sum(1 for r in results if r.get("success")),
            "total_scenes": len(results)
        }
        summary_path = OUTPUT_DIR / "pipeline_result.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"\n结果已保存: {summary_path}")
        print(f"成功: {summary['success_count']}/{summary['total_scenes']}")
        
    elif args.json:
        results = pipeline_from_json(args.json, seed=args.seed)
        summary_path = OUTPUT_DIR / "pipeline_result.json"
        with open(summary_path, "w") as f:
            json.dump({"results": results}, f, indent=2, ensure_ascii=False)
        print(f"\n结果已保存: {summary_path}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
