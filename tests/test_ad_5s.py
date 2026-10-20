#!/usr/bin/env python3
"""快速5秒广告视频测试"""
import json, time, shutil, requests, copy, sys, os
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output" / "test_ad_5s"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

COMFYUI_BASE = "http://127.0.0.1:13201"

# 加载工作流
workflow = json.load(open(ROOT / "pipelines/ltx_t2v_workflow.json"))

# 广告场景 prompt
ad_prompt = "A sleek black smartphone rotating slowly on a reflective glass surface, studio lighting with soft key light and rim light, product photography style, clean minimalist background with subtle gradient, 8K detail, shallow depth of field, commercial advertisement quality, cinematic"

negative = "low quality, blurry, watermark, ugly, distorted, deformed, abstract noise, branch-like artifacts, text, logo"

# 注入
wf = copy.deepcopy(workflow)
for nid, node in wf.items():
    inputs = node.get("inputs", {})
    if "text" in inputs and isinstance(inputs["text"], str):
        if any(kw in inputs["text"].lower() for kw in ["blurry", "ugly", "watermark"]):
            inputs["text"] = negative
        else:
            inputs["text"] = ad_prompt
    if "width" in inputs and isinstance(inputs["width"], (int, float)):
        inputs["width"] = 384
    if "height" in inputs and isinstance(inputs["height"], (int, float)):
        inputs["height"] = 672
    if "length" in inputs and isinstance(inputs["length"], (int, float)):
        inputs["length"] = 75  # ~2.5s @ 30fps
    if node.get("class_type") == "RandomNoise":
        inputs["noise_seed"] = int(time.time() * 1000) % (2**31)

print("提交渲染...")
r = requests.post(f"{COMFYUI_BASE}/prompt", json={"prompt": wf}, timeout=10)
result = r.json()
pid = result.get("prompt_id")
print(f"  prompt_id: {pid}")

start = time.time()
for i in range(40):
    time.sleep(15)
    hist = requests.get(f"{COMFYUI_BASE}/history/{pid}", timeout=5).json()
    if pid in hist:
        status = hist[pid].get("status", {})
        if status.get("completed"):
            outputs = hist[pid].get("outputs", {})
            for node_id, node_out in outputs.items():
                if "videos" in node_out:
                    for vid in node_out["videos"]:
                        fn = vid["filename"]
                        sub = vid.get("subfolder", "")
                        src = Path(f"/Users/pengzhan/Documents/ComfyUI/output/video/LTX/{fn}" if not sub else f"/Users/pengzhan/Documents/ComfyUI/output/video/LTX/{sub}/{fn}")
                        if src.exists():
                            dest = OUTPUT_DIR / fn
                            shutil.copy2(src, dest)
                            print(f"\n完成! 耗时: {time.time()-start:.0f}s")
                            print(f"  输出: {dest}")
                            print(f"  大小: {dest.stat().st_size//1024}KB")
                            probe = subprocess.run(["ffprobe","-v","error","-show_entries","stream=width,height,duration","-of","default=noprint_wrappers=1", str(dest)], capture_output=True, text=True)
                            print(f"  信息:\n{probe.stdout}")
                            subprocess.run(["open", str(OUTPUT_DIR)])
                            sys.exit(0)
    print(f"  [{i+1}/40] 运行中 ({time.time()-start:.0f}s)")

print("超时")
