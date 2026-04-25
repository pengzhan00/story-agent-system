"""
story-agent-system → ComfyUI AnimateDiff 批量管线

从故事系统读取剧本场景，逐个用 AnimateDiff 生成动画视频片段。
"""

import json
import os
import time
import sys
from pathlib import Path

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
    """从场景信息构建生成 prompt"""
    parts = []
    
    # 场景名称和描述
    name = scene.get("name", scene.get("scene_name", ""))
    description = scene.get("description", "")
    setting = scene.get("setting", "")
    time_info = scene.get("time", "")
    weather = scene.get("weather", "")
    
    if description:
        parts.append(description)
    if setting:
        parts.append(setting)
    if time_info:
        parts.append(time_info)
    if weather:
        parts.append(weather)
    
    mood = scene.get("mood", "")
    if mood:
        parts.append(mood)
    
    # 角色信息
    characters = scene.get("characters", "")
    if characters:
        parts.append(characters)
    
    # 动作/镜头
    camera = scene.get("camera", "")
    if camera:
        parts.append(camera)
    
    # 风格标签
    parts.append("anime style, soft colors, detailed background, high quality")
    
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


def generate_scene_video(scene: dict, project_name: str = "project", seed: int = 0) -> dict:
    """为单个场景生成动画视频"""
    import requests
    
    scene_name = scene.get("name", scene.get("scene_name", "scene"))
    print(f"\n  === 场景: {scene_name} ===")
    
    prompt = build_scene_prompt(scene)
    print(f"  提示词: {prompt[:100]}...")
    
    workflow = create_animatediff_workflow(prompt, scene_name, seed)
    prompt_id = submit_workflow(workflow)
    if not prompt_id:
        return {"scene": scene_name, "success": False, "error": "提交失败"}
    
    print(f"  等待渲染 (prompt_id: {prompt_id[:8]})...")
    outputs = wait_for_completion(prompt_id)
    if not outputs:
        return {"scene": scene_name, "success": False, "error": "渲染超时"}
    
    files = get_video_output(outputs)
    result = {
        "scene": scene_name,
        "success": True if files else False,
        "files": files,
        "outputs": outputs
    }
    
    if files:
        print(f"  ✅ 生成完成: {', '.join(f['filename'] for f in files)}")
    else:
        print(f"  ⚠️  无输出文件")
    
    return result


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
