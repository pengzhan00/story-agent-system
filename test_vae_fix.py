#!/usr/bin/env python3
"""测试 VAE 修复后的渲染"""
import json
import time
import subprocess
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8188"

def test_render():
    wf_path = Path("pipelines/wan2_t2v_fp16_workflow.json")
    wf = json.load(open(wf_path))

    # 检查配置
    for nid, node in wf.items():
        if node.get("class_type") == "VAELoader":
            print(f"VAE: {node['inputs'].get('vae_name')}")
        if node.get("class_type") == "Wan22ImageToVideoLatent":
            print(f"分辨率: {node['inputs'].get('width')}x{node['inputs'].get('height')}")

    # 设置 seed
    seed = int(time.time() * 1000) % (2**31)
    for nid, node in wf.items():
        if node.get("class_type") == "KSampler":
            node["inputs"]["seed"] = seed

    # 提交
    r = requests.post(f"{BASE}/prompt", json={"prompt": wf}, timeout=10)
    if r.status_code != 200:
        print(f"提交失败: {r.status_code}")
        return False

    pid = r.json().get("prompt_id")
    print(f"任务ID: {pid}")

    # 等待完成
    for i in range(60):
        time.sleep(3)
        hist = requests.get(f"{BASE}/history/{pid}", timeout=5).json()
        if pid in hist:
            status = hist[pid].get("status", {}).get("status_str")
            if status == "success":
                # 查找输出
                outputs = hist[pid].get("outputs", {})
                for nid, out in outputs.items():
                    for k, files in out.items():
                        for f in (files if isinstance(files, list) else [files]):
                            if isinstance(f, dict) and "filename" in f:
                                fname = f["filename"]
                                comfy_out = Path("~/Documents/ComfyUI/output").expanduser() / fname
                                if comfy_out.exists():
                                    result = subprocess.run(
                                        ["ffprobe", "-v", "error", "-select_streams", "v",
                                         "-show_entries", "stream=width,height,r_frame_rate,duration",
                                         "-of", "csv=p=0", str(comfy_out)],
                                        capture_output=True, text=True, timeout=10
                                    )
                                    info = result.stdout.strip().split(",")
                                    print(f"\n✅ 成功!")
                                    print(f"文件: {fname}")
                                    print(f"分辨率: {info[0]}x{info[1]}")
                                    print(f"帧率: {info[2]}")
                                    print(f"时长: {info[3]}s")
                                    return True
            elif status == "error":
                messages = hist[pid].get("status", {}).get("messages", [])
                for msg in messages:
                    if msg[0] == "execution_error":
                        err = msg[1].get("exception_message", "")[:150]
                        print(f"\n❌ 错误: {err}")
                        return False
        print(f"[{i+1}/60] 等待渲染...")

    print("\n⏰ 超时")
    return False

if __name__ == "__main__":
    test_render()