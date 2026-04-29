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
    RenderError,
    build_scene_prompt,
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
    submit_workflow,
    wait_for_completion,
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
        out = dispatcher.render(payload, output_path)
        print(f"  ✅ 生成完成: {out.name}, seed={actual_seed}")
        return {
            "scene": scene_name,
            "success": True,
            "files": [{"filename": out.name, "path": str(out)}],
            "output_path": str(out),
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
