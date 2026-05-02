"""
story-agent-system → ComfyUI AnimateDiff 批量管线（向后兼容入口）

工具函数和管线逻辑已统一到 render_pipeline.py。
本模块保留 generate_scene_video / pipeline_from_db / pipeline_from_json
等高层 API，供旧调用方使用；新代码请直接使用 get_dispatcher()。
"""

import json
import sys
import time
from pathlib import Path
from typing import Optional

# ─── 路径 ─────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── 从 render_pipeline 统一导入（避免重复定义）───────
from pipelines.render_pipeline import (  # noqa: E402
    NEGATIVE_PROMPT,
    AnimateDiffPipeline,
    RenderError,
    build_scene_prompt,
    classify_pipeline_missing,
    find_ksampler_negative_node,
    find_ksampler_positive_node,
    find_nodes_by_type,
    get_dispatcher,
    get_object_info,
    get_video_output,
    inject_controlnet,
    inject_loras,
    inject_prompt,
    inject_seed,
    load_pipeline_config,
    save_pipeline_config,
    set_active_pipeline_name,
    submit_workflow,
    wait_for_completion,
    wait_for_completion_result as render_wait_for_completion_result,
)

# ─── 配置常量 ──────────────────────────────────────────

COMFYUI_URL = "http://127.0.0.1:8188"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
WORKFLOW_FILE = Path(__file__).parent / "animatediff_workflow.json"

# SDXL 默认参数（仍供 create_animatediff_workflow 使用）
SDXL_WIDTH = 1024
SDXL_HEIGHT = 1024
ANIMATION_FRAMES = 16
FRAME_RATE = 8
SAMPLER_STEPS = 20
CFG_SCALE = 7.0


# ─── 向后兼容 shim（供 batch_renderer / render_app / app.py 使用）──────────

def animatediff_available() -> bool:
    """检查 AnimateDiff 管线是否可用（兼容旧 API）。"""
    try:
        matrix = get_dispatcher().probe()
        return any(s.available for n, s in matrix.items() if "animatediff" in n)
    except Exception:
        return False


def wait_for_completion_result(
    prompt_id: str,
    comfyui_url: str = COMFYUI_URL,
    timeout: int = 7200,
) -> dict:
    """等待 ComfyUI 完成，返回 {'status', 'outputs', 'error'}（兼容旧 API）。"""
    result = render_wait_for_completion_result(
        prompt_id,
        comfyui_url=comfyui_url,
        timeout=timeout,
    )
    return {
        "status": result.get("status"),
        "outputs": result.get("outputs", {}),
        "error": result.get("error_message"),
        "error_type": result.get("error_type"),
    }


def prepare_workflow_for_scene(
    scene: dict,
    seed: int = 0,
    lora_refs: Optional[list] = None,
    controlnet_type: Optional[str] = None,
    controlnet_strength: float = 0.6,
    fixed_seed: bool = False,
    render_config: Optional[dict] = None,
) -> dict:
    """
    为场景构建 ComfyUI 工作流（兼容旧 API，委托给 AnimateDiffPipeline）。
    返回 {"workflow", "prompt", "pipeline_name", "pipeline_id", "seed"}。
    """
    actual_seed = seed if fixed_seed else int(time.time() * 1000) % (2 ** 31)
    prompt = build_scene_prompt(scene)
    scene_name = scene.get("name", scene.get("scene_name", "scene"))

    # 尝试从 dispatcher 取 AnimateDiff pipeline 实例（复用其配置）
    pipeline: Optional[AnimateDiffPipeline] = None
    pipeline_id = "animatediff"
    try:
        for _, pipe in get_dispatcher()._pipelines:
            if isinstance(pipe, AnimateDiffPipeline):
                pipeline = pipe
                pipeline_id = pipe.name
                break
    except Exception:
        pass

    if pipeline is not None:
        wf = pipeline._build_workflow(prompt, scene_name, actual_seed)
    else:
        wf = {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "animagine-xl-3.1/animagine-xl-3.1.safetensors"}},
            "2": {"class_type": "ADE_EmptyLatentImageLarge",
                  "inputs": {"batch_size": 8, "width": SDXL_WIDTH, "height": SDXL_HEIGHT}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["1", 1], "text": prompt}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["1", 1], "text": NEGATIVE_PROMPT}},
            "5": {"class_type": "ADE_LoadAnimateDiffModel",
                  "inputs": {"model_name": "hsxl_temporal_layers.f16.safetensors"}},
            "6": {"class_type": "ADE_ApplyAnimateDiffModelSimple",
                  "inputs": {"motion_model": ["5", 0]}},
            "7": {"class_type": "ADE_UseEvolvedSampling",
                  "inputs": {"model": ["1", 0], "beta_schedule": "autoselect",
                             "m_models": ["6", 0]}},
            "8": {"class_type": "KSampler",
                  "inputs": {"model": ["7", 0], "positive": ["3", 0],
                             "negative": ["4", 0], "latent_image": ["2", 0],
                             "steps": SAMPLER_STEPS, "cfg": CFG_SCALE,
                             "sampler_name": "euler", "scheduler": "normal",
                             "seed": actual_seed, "denoise": 1.0}},
            "9": {"class_type": "VAEDecodeTiled",
                  "inputs": {"samples": ["8", 0], "vae": ["1", 2],
                             "tile_size": 512, "overlap": 64,
                             "temporal_size": 64, "temporal_overlap": 8}},
            "10": {"class_type": "VHS_VideoCombine",
                   "inputs": {"images": ["9", 0], "frame_rate": FRAME_RATE,
                              "loop_count": 0, "filename_prefix": "scene",
                              "format": "video/h264-mp4", "pingpong": False,
                              "save_output": True}},
        }

    wf = inject_prompt(wf, prompt, NEGATIVE_PROMPT)
    wf = inject_seed(wf, actual_seed)
    if lora_refs:
        wf = inject_loras(wf, lora_refs)
    if controlnet_type:
        wf = inject_controlnet(wf, controlnet_type, strength=controlnet_strength)

    return {
        "workflow": wf,
        "prompt": prompt,
        "pipeline_name": pipeline_id,
        "pipeline_id": pipeline_id,
        "seed": actual_seed,
    }


# ─── 管线配置 UI compat（render_app.py 使用）─────────

_PIPELINE_LABEL_MAP = {
    "animatediff_animagine": ("C", "AnimateDiff + Animagine XL 3.1"),
    "animatediff_sdxl":      ("D", "AnimateDiff + SDXL Base"),
    "flux_wan2_twostage":    ("A", "Flux 2 Klein 9B → Wan2.2 TI2V"),
    "wan2_ti2v":             ("B", "Wan2.2 TI2V 5B GGUF"),
    "static_frame":          ("E", "Static Frame (txt2img)"),
    "stub":                  ("F", "Stub (黑帧占位)"),
}
_REVERSE_PIPELINE_LABEL_MAP = {label: name for name, (label, _) in _PIPELINE_LABEL_MAP.items()}


def get_pipeline_config() -> dict:
    """兼容 render_app UI：返回管线配置（适配旧 A/B/C 格式）。"""
    try:
        cfg_raw = load_pipeline_config()
        matrix = get_dispatcher().probe()
        options: dict = {}
        active_name = cfg_raw.get("active_pipeline", "")
        active: Optional[str] = None
        for entry in cfg_raw.get("pipelines", []):
            name = entry.get("name", "")
            status = matrix.get(name)
            if status is None:
                continue
            label, display = _PIPELINE_LABEL_MAP.get(name, (name, name))
            options[label] = {
                "name": display,
                "pipeline_name": name,
                "base_model": name,
                "workflow_file": entry.get("config", {}).get("workflow_file", ""),
                "width": entry.get("config", {}).get("width", "?"),
                "height": entry.get("config", {}).get("height", "?"),
                "frame_rate": entry.get("config", {}).get("fps", "?"),
                "available": status.available,
                "missing": status.missing,
                "last_error": status.last_error,
                "production_ready": entry.get("production_ready", True),
                "description": entry.get("description", ""),
            }
            if name == active_name:
                active = label
        if active is None:
            for label, pipe in options.items():
                if pipe.get("available"):
                    active = label
                    break
        return {"active": active or "C", "options": options}
    except Exception:
        return {"active": "C", "options": {}}


def inspect_pipeline_capability(pipeline_id: str) -> dict:
    """兼容 render_app UI：检查特定管线是否就绪。"""
    try:
        cfg = get_pipeline_config()
        pipe = cfg.get("options", {}).get(pipeline_id, {})
        avail = pipe.get("available", False)
        missing = pipe.get("missing", []) if not avail else []
        prod_ready = pipe.get("production_ready", True)
        if avail and not prod_ready:
            state_key, state_text = ("validation_pending", "🧪 验证中")
        else:
            state_key, state_text = classify_pipeline_missing(missing)
        return {
            "ready": avail and prod_ready,
            "errors": missing,
            "status_text": "✅ 可生产" if (avail and prod_ready) else state_text,
            "state_key": "ready" if (avail and prod_ready) else state_key,
            "production_tier": "production" if (avail and prod_ready) else ("experimental" if state_key in {"workflow_refinement_required", "validation_pending"} else "blocked"),
            "last_error": pipe.get("last_error", ""),
        }
    except Exception:
        return {"ready": False, "errors": ["检查失败"],
                "status_text": "❌ 检查失败", "production_tier": "unknown", "state_key": "unknown"}


def get_active_pipeline() -> str:
    """兼容 render_app UI：返回当前激活管线 ID。"""
    return get_pipeline_config().get("active", "C")


def list_pipelines_with_capabilities() -> list:
    """兼容 render_app UI：列出所有管线及状态。"""
    cfg = get_pipeline_config()
    active = cfg.get("active", "")
    return [
        {
            "id": pid,
            "name": pipe.get("name", pid),
            "status_text": inspect_pipeline_capability(pid).get("status_text", "未知"),
            "active": pid == active,
        }
        for pid, pipe in cfg.get("options", {}).items()
    ]


def set_active_pipeline(pipeline_id: str) -> dict:
    """兼容 render_app UI：持久切换当前激活管线。"""
    pipeline_name = _REVERSE_PIPELINE_LABEL_MAP.get(pipeline_id, pipeline_id)
    set_active_pipeline_name(pipeline_name)
    return get_pipeline_config()


# ─── 工作流构建（保留供直接调试用）───────────────────

def create_animatediff_workflow(positive_prompt: str, scene_name: str,
                                seed: int = 0) -> dict:
    """创建 AnimateDiff SDXL 工作流 JSON（调试/测试用，生产路径走 dispatcher）。"""
    prefix = f"scene_{scene_name[:20].replace(' ', '_')}"
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "2": {"class_type": "ADE_EmptyLatentImageLarge",
              "inputs": {"batch_size": ANIMATION_FRAMES,
                         "width": SDXL_WIDTH, "height": SDXL_HEIGHT}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": positive_prompt}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": NEGATIVE_PROMPT}},
        "5": {"class_type": "ADE_LoadAnimateDiffModel",
              "inputs": {"model_name": "hsxl_temporal_layers.f16.safetensors"}},
        "6": {"class_type": "ADE_ApplyAnimateDiffModelSimple",
              "inputs": {"motion_model": ["5", 0]}},
        "7": {"class_type": "ADE_UseEvolvedSampling",
              "inputs": {"model": ["1", 0], "beta_schedule": "autoselect",
                         "m_models": ["6", 0]}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["7", 0], "positive": ["3", 0],
                         "negative": ["4", 0], "latent_image": ["2", 0],
                         "steps": SAMPLER_STEPS, "cfg": CFG_SCALE,
                         "sampler_name": "euler", "scheduler": "normal",
                         "seed": seed, "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeTiled",
              "inputs": {"samples": ["8", 0], "vae": ["1", 2],
                         "tile_size": 512, "overlap": 64,
                         "temporal_size": 64, "temporal_overlap": 8}},
        "10": {"class_type": "VHS_VideoCombine",
               "inputs": {"images": ["9", 0], "frame_rate": FRAME_RATE,
                          "loop_count": 0, "filename_prefix": prefix,
                          "format": "video/h264-mp4",
                          "pingpong": False, "save_output": True}},
    }


# ─── 高层 API ──────────────────────────────────────────

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
    为单个场景生成动画视频，通过 RenderDispatcher 自动回退。
    返回 {"scene", "success", "files", "output_path", "seed", ["error"]}。
    """
    scene_name = scene.get("name", scene.get("scene_name", "scene"))
    print(f"\n  === 场景: {scene_name} ===")

    actual_seed = seed if fixed_seed else int(time.time() * 1000) % (2 ** 31)

    # 合并渲染参数到 payload
    payload = dict(scene)
    payload.setdefault("project_name", project_name)
    if lora_refs:
        payload["lora_refs"] = lora_refs
    if controlnet_type:
        payload["controlnet_type"] = controlnet_type
        payload.setdefault("controlnet_strength", controlnet_strength)

    out_dir = OUTPUT_DIR / project_name / "scenes"
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


def build_lora_refs_from_scene(scene: dict, project_id: int = 0) -> list[dict]:
    """从场景的 render_payload 中提取 LoRA 引用。"""
    try:
        import core.database as db
        payload = scene.get("render_payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        loras = []
        for char_ref in payload.get("characters", []):
            char_name = char_ref.get("name", "")
            if project_id and char_name:
                chars = db.list_characters(project_id)
                char = next((c for c in chars if c.name == char_name), None)
                if char and char.lora_ref:
                    loras.append({"name": char.lora_ref, "strength": 0.8,
                                  "type": "character"})
        scene_name = (payload.get("scene_asset", {}).get("name", "")
                      or scene.get("location", ""))
        if project_id and scene_name:
            scene_assets = db.list_scene_assets(project_id)
            sa = next((s for s in scene_assets if s.name == scene_name), None)
            if sa and sa.lora_ref:
                loras.append({"name": sa.lora_ref, "strength": 0.6,
                              "type": "scene"})
        return loras
    except Exception:
        return []


def pipeline_from_db(project_id: int, db_module=None,
                     seed: int = 42) -> list[dict]:
    """从数据库读取场景并批量渲染（走 dispatcher 回退链）。"""
    print(f"开始批量管线: project_id={project_id}")
    results = []
    if not db_module:
        return results
    project = db_module.get_project(project_id)
    if not project:
        print(f"[ERROR] 找不到项目: {project_id}")
        return results
    project_name = getattr(project, "name", str(project_id))
    print(f"项目: {project_name}")
    scenes = db_module.get_scenes(project_id)
    print(f"场景数: {len(scenes)}")
    for i, scene in enumerate(scenes):
        r = generate_scene_video(scene, project_name, seed=seed + i * 1000)
        results.append(r)
    return results


def pipeline_from_json(json_path: str, seed: int = 42) -> list[dict]:
    """从 JSON 场景列表文件批量渲染。"""
    with open(json_path) as f:
        scenes = json.load(f)
    project_name = Path(json_path).stem
    return [
        generate_scene_video(scene, project_name, seed=seed + i * 1000)
        for i, scene in enumerate(scenes)
    ]


def main():
    import argparse

    parser = argparse.ArgumentParser(description="漫剧动画批量管线")
    parser.add_argument("--project", type=int, help="从数据库加载项目 ID")
    parser.add_argument("--json", type=str, help="从 JSON 文件加载场景列表")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--probe", action="store_true", help="探测可用管线并退出")
    args = parser.parse_args()

    if args.probe:
        dispatcher = get_dispatcher()
        matrix = dispatcher.probe(force=True)
        print("\n管线能力矩阵:")
        for name, status in matrix.items():
            icon = "✅" if status.available else "❌"
            print(f"  {icon} {name}")
            if status.missing:
                print(f"     缺失: {', '.join(status.missing)}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.project:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import core.database as db
        db.init_db()
        results = pipeline_from_db(args.project, db_module=db, seed=args.seed)
        summary = {
            "project_id": args.project,
            "results": results,
            "success_count": sum(1 for r in results if r.get("success")),
            "total_scenes": len(results),
        }
        summary_path = OUTPUT_DIR / "pipeline_result.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False)
        )
        print(f"\n结果已保存: {summary_path}")
        print(f"成功: {summary['success_count']}/{summary['total_scenes']}")

    elif args.json:
        results = pipeline_from_json(args.json, seed=args.seed)
        summary_path = OUTPUT_DIR / "pipeline_result.json"
        summary_path.write_text(
            json.dumps({"results": results}, indent=2, ensure_ascii=False)
        )
        print(f"\n结果已保存: {summary_path}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
