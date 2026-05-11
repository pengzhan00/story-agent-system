#!/usr/bin/env python3
"""测试单镜头渲染 - 使用正确的 render_payload 构建 prompt"""
import json
import time
import shutil
import subprocess
from pathlib import Path
import sys
import requests
import sqlite3

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipelines.render_pipeline import build_pipeline_prompt_bundle, normalize_shot_payload

# 配置
SHOT_ID = 453
PROJECT_ROOT = Path("/Users/pengzhan/myworkspace/projects/story-agent-system")
OUTPUT_DIR = PROJECT_ROOT / "output" / "test_proper_prompt"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

COMFYUI_BASE = "http://127.0.0.1:8188"
COMFYUI_OUTPUT = Path("/Users/pengzhan/Documents/ComfyUI/output/video/LTX")

# 加载工作流
workflow = json.load(open(PROJECT_ROOT / "pipelines/ltx_t2v_workflow.json"))

print(f"\n{'='*60}")
print(f"测试：使用正确 render_payload 构建 prompt")
print(f"{'='*60}\n")

# 从数据库获取 shot 数据
conn = sqlite3.connect(PROJECT_ROOT / "data/story_agents.db")
cursor = conn.cursor()
cursor.execute("SELECT id, render_payload, narration FROM shots WHERE id=?", (SHOT_ID,))
row = cursor.fetchone()
conn.close()

shot_id = row[0]
render_payload_str = row[1]
narration = row[2]

render_payload = json.loads(render_payload_str)
print(f"📋 Shot ID: {shot_id}")
print(f"📍 Location: {render_payload.get('location', 'N/A')}")

# 使用正确的 prompt 构建函数
normalized = normalize_shot_payload(render_payload)
bundle = build_pipeline_prompt_bundle(normalized, "ltx")

print(f"\n📝 生成的 Prompt:")
print(f"{'─'*60}")
prompt = bundle["positive_prompt"]
# 显示前500字符
print(f"{prompt[:500]}...")
print(f"{'─'*60}")
print(f"分辨率: {bundle['width']}×{bundle['height']}")
print(f"帧数: {bundle['frames']} @ {bundle['fps']}fps")

# 构建工作流
import copy
wf = copy.deepcopy(workflow)

# 设置参数
seed = int(time.time() * 1000) % (2**31)

# 遍历节点注入 prompt
for nid, node in wf.items():
    inputs = node.get("inputs", {})
    
    # 文本 prompt
    if "text" in inputs and isinstance(inputs["text"], str):
        current = inputs["text"].lower()
        if "negative" in current or "low quality" in current:
            inputs["text"] = bundle.get("negative_prompt", "")
        else:
            inputs["text"] = prompt
    
    # 分辨率
    if "width" in inputs and isinstance(inputs["width"], (int, float)):
        inputs["width"] = bundle["width"]
    if "height" in inputs and isinstance(inputs["height"], (int, float)):
        inputs["height"] = bundle["height"]
    
    # 帧数
    if "value" in inputs and node.get("class_type") == 'PrimitiveInt':
        # 找到帧数节点 (node 113)
        if nid == '113':
            inputs["value"] = bundle["frames"]
    
    # 随机种子
    if node.get("class_type") == 'RandomNoise':
        inputs["noise_seed"] = seed

print(f"\n🎬 提交渲染...")
try:
    r = requests.post(f"{COMFYUI_BASE}/prompt", json={'prompt': wf}, timeout=10)
    result = r.json()
    prompt_id = result.get('prompt_id')
    print(f"   prompt_id: {prompt_id}")
    
    if 'error' in result:
        print(f"   ❌ 错误: {result['error']}")
        sys.exit(1)
except Exception as e:
    print(f"   ❌ 提交失败: {e}")
    sys.exit(1)

# 等待完成
print(f"\n⏳ 等待渲染完成...")
start_time = time.time()

for i in range(20):
    time.sleep(15)
    elapsed = time.time() - start_time
    
    hist = requests.get(f"{COMFYUI_BASE}/history/{prompt_id}", timeout=5).json()
    if prompt_id in hist:
        status = hist[prompt_id].get('status', {})
        if status.get('completed'):
            # 找输出文件
            outputs = hist[prompt_id].get('outputs', {})
            for node_id, node_out in outputs.items():
                if 'videos' in node_out:
                    for vid in node_out['videos']:
                        filename = vid['filename']
                        subpath = vid.get('subfolder', '')
                        full_path = COMFYUI_OUTPUT / filename if not subpath else Path(f"{COMFYUI_OUTPUT}/{subpath}/{filename}")
                        print(f"\n✅ 渲染完成!")
                        print(f"   输出: {filename}")
                        print(f"   耗时: {elapsed:.1f}s")
                        
                        # 复制到项目输出
                        dest = OUTPUT_DIR / f"shot_{shot_id}_proper.mp4"
                        shutil.copy2(full_path, dest)
                        
                        # 分析视频
                        probe = subprocess.run([
                            'ffprobe', '-v', 'error', '-show_streams', str(dest)
                        ], capture_output=True, text=True)
                        
                        print(f"\n📊 视频信息:")
                        for line in probe.stdout.split('\n'):
                            if line.startswith('width=') or line.startswith('height=') or \
                               line.startswith('duration=') or line.startswith('codec_name='):
                                print(f"   {line}")
                        
                        # 打开输出目录
                        subprocess.run(['open', str(OUTPUT_DIR)])
                        sys.exit(0)
    
    # 检查队列
    queue = requests.get(f"{COMFYUI_BASE}/queue", timeout=5).json()
    running = queue.get('queue_running', [])
    if len(running) == 0 and i > 2:
        # 队列空了但没记录完成，可能是已完成
        latest = max(COMFYUI_OUTPUT.glob('*.mp4'), key=lambda x: x.stat().st_mtime)
        if latest.stat().st_mtime > start_time:
            dest = OUTPUT_DIR / f"shot_{shot_id}_proper.mp4"
            shutil.copy2(latest, dest)
            print(f"\n✅ 渲染完成!")
            print(f"   输出: {latest.name}")
            print(f"   耗时: {elapsed:.1f}s")
            subprocess.run(['open', str(OUTPUT_DIR)])
            sys.exit(0)
    
    print(f"   [{i+1}/20] 运行中 ({elapsed:.0f}s)")

print(f"\n❌ 渲染超时")
sys.exit(1)