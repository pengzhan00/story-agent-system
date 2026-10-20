#!/usr/bin/env python3
"""
端到端渲染测试：生成一个 6 秒测试视频
验证分辨率 720×1280 是否正确输出
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pipelines.render_pipeline import (
    RenderDispatcher,
    get_format_preset,
    normalize_shot_payload,
)
from core.service_ports import comfyui_api_base

# ── 测试配置 ──────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent / "output" / "test_videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def test_simple_render():
    """测试最简单的 T2V 渲染"""
    print("=" * 60)
    print("端到端渲染测试 - 6秒竖屏视频")
    print("=" * 60)
    
    # 1. 检查 ComfyUI 状态
    print("\n[1] 检查 ComfyUI...")
    try:
        import requests
        base = comfyui_api_base()
        r = requests.get(f"{base}/system_stats", timeout=5)
        print(f"   ✅ ComfyUI 运行中: {base}")
    except Exception as e:
        print(f"   ❌ ComfyUI 未响应: {e}")
        return False
    
    # 2. 检查管线可用性
    print("\n[2] 检查渲染管线...")
    dispatcher = RenderDispatcher()
    capabilities = dispatcher.capability_matrix()
    print(f"   可用管线: {capabilities}")
    
    # 3. 构建 shot payload
    print("\n[3] 构建 shot payload...")
    shot_payload = {
        "project_format": "short_drama",  # 红果标准 720×1280
        "narration": "一位神秘侠客站在都市霓虹灯下",
        "location": "都市夜景",
        "mood": "神秘、紧张",
        "time_of_day": "夜晚",
        "weather": "晴",
        "shot_type": "中景",
        "camera_movement": "缓慢推进",
        "style_guide": "赛博朋克风格，霓虹光效",
        "characters": [{"name": "侠客", "appearance": "黑衣蒙面，手持利剑"}],
        "dialogue": [],
        "width": 720,
        "height": 1280,
        "frames": 97,  # ~6秒 @ 16fps
        "fps": 16,
        "duration_sec": 6.0,
        "allow_fallback": True,  # 允许降级
    }
    
    # Normalize payload
    normalized = normalize_shot_payload(shot_payload)
    print(f"   normalized width: {normalized.get('width')}")
    print(f"   normalized height: {normalized.get('height')}")
    print(f"   normalized frames: {normalized.get('frames')}")
    
    # 检查 format preset
    preset = get_format_preset("short_drama")
    print(f"   preset: {preset}")
    
    # 4. 执行渲染
    print("\n[4] 执行渲染...")
    output_path = OUTPUT_DIR / f"test_6s_{int(time.time())}.mp4"
    
    try:
        result = dispatcher.render(
            shot_payload=shot_payload,
            output_path=output_path,
            tier="test",  # 测试模式
        )
        print(f"   渲染结果: {result}")
    except Exception as e:
        print(f"   ❌ 渲染异常: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # 5. 检查输出
    print("\n[5] 检查输出视频...")
    if not output_path.exists():
        print(f"   ❌ 输出文件不存在: {output_path}")
        return False
    
    # 用 ffprobe 检查分辨率
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=width,height,r_frame_rate,duration",
             "-of", "csv=p=0", str(output_path)],
            capture_output=True, text=True, timeout=10
        )
        info = result.stdout.strip().split(",")
        width, height, fps, duration = info[0], info[1], info[2], info[3]
        print(f"   分辨率: {width}×{height}")
        print(f"   帧率: {fps}")
        print(f"   时长: {duration}s")
        
        # QC 检查
        if int(width) >= 720 and int(height) >= 1280:
            print(f"   ✅ 分辨率达标！")
            return True
        else:
            print(f"   ❌ 分辨率不足: 期望 720×1280, 实际 {width}×{height}")
            return False
    except Exception as e:
        print(f"   ❌ ffprobe 失败: {e}")
        return False


def test_direct_workflow():
    """直接提交 ComfyUI workflow 测试（LTX 2.3）"""
    print("\n" + "=" * 60)
    print("直接 ComfyUI Workflow 测试（LTX 2.3）")
    print("=" * 60)

    import requests

    # 加载 LTX 工作流
    wf_path = Path(__file__).parent / "pipelines" / "ltx_t2v_workflow.json"
    if not wf_path.exists():
        print(f"   ❌ 工作流文件不存在: {wf_path}")
        return False
    wf = json.load(open(wf_path))

    # 检查分辨率相关节点
    # LTX 工作流使用 EmptyImage + ImageScaleBy + GetImageSize 组合
    empty_img_node = wf.get("131", {})  # EmptyImage
    print(f"\n工作流节点 131 (EmptyImage) 配置:")
    print(f"   class_type: {empty_img_node.get('class_type')}")
    print(f"   width: {empty_img_node['inputs'].get('width')}")
    print(f"   height: {empty_img_node['inputs'].get('height')}")

    # 设置测试参数
    seed = int(time.time() * 1000) % (2 ** 31)

    # 修改分辨率到竖屏
    wf["131"]["inputs"]["width"] = 288  # smoke test 小尺寸
    wf["131"]["inputs"]["height"] = 512
    wf["113"]["inputs"]["value"] = 17  # frames
    wf["109"]["inputs"]["value"] = "A cinematic scene with a mysterious figure standing under neon lights in a modern city at night."

    # 设置 seed
    for nid, node in wf.items():
        if node.get("class_type") == "RandomNoise":
            node["inputs"]["noise_seed"] = seed

    print(f"\n修改后:")
    print(f"   width: {wf['131']['inputs']['width']}")
    print(f"   height: {wf['131']['inputs']['height']}")
    print(f"   frames: {wf['113']['inputs']['value']}")

    # 提交到 ComfyUI
    base = comfyui_api_base()
    print(f"\n提交到 ComfyUI: {base}")

    try:
        r = requests.post(f"{base}/prompt", json={"prompt": wf}, timeout=10)
        if r.status_code == 200:
            prompt_id = r.json().get("prompt_id")
            print(f"   ✅ 提交成功: prompt_id={prompt_id}")

            # 等待完成
            print(f"\n等待渲染完成 (最多 5 分钟)...")
            for i in range(30):
                time.sleep(10)
                hist = requests.get(f"{base}/history/{prompt_id}", timeout=5).json()
                if prompt_id in hist:
                    status = hist[prompt_id].get("status", {})
                    if status.get("completed"):
                        print(f"   ✅ 渲染完成!")
                        # 检查输出
                        outputs = hist[prompt_id].get("outputs", {})
                        for node_id, out in outputs.items():
                            for key, files in out.items():
                                for f in files:
                                    if "filename" in f:
                                        fname = f["filename"]
                                        # 检查 ComfyUI 输出目录
                                        comfy_out = Path("~/Documents/ComfyUI/output").expanduser() / fname
                                        if comfy_out.exists():
                                            # 用 ffprobe 检查
                                            result = subprocess.run(
                                                ["ffprobe", "-v", "error", "-select_streams", "v",
                                                 "-show_entries", "stream=width,height",
                                                 "-of", "csv=p=0", str(comfy_out)],
                                                capture_output=True, text=True, timeout=10
                                            )
                                            res = result.stdout.strip()
                                            print(f"   输出文件: {comfy_out}")
                                            print(f"   分辨率: {res}")
                                            return True
                        return True
                    elif status.get("status_str") == "error":
                        print(f"   ❌ 渲染错误: {status}")
                        return False
                print(f"   [{i+1}/30] 等待中...")

            print(f"   ⏰ 超时")
            return False
        else:
            print(f"   ❌ 提交失败: {r.status_code} {r.text}")
            return False
    except Exception as e:
        print(f"   ❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # 先测试直接 workflow
    result1 = test_direct_workflow()
    
    # 再测试完整管线
    result2 = test_simple_render()
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"直接 Workflow: {'✅ 成功' if result1 else '❌ 失败'}")
    print(f"完整管线: {'✅ 成功' if result2 else '❌ 失败'}")