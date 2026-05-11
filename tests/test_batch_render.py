#!/usr/bin/env python3
"""测试批量渲染：8个镜头 → 合成 → 导出"""
import json
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import requests
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

# 配置
SHOT_IDS = [453, 455, 457, 459, 461, 463, 465, 467]
PROJECT_ROOT = Path("/Users/pengzhan/myworkspace/projects/story-agent-system")
OUTPUT_DIR = PROJECT_ROOT / "output" / "test_batch_1min"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

COMFYUI_BASE = "http://127.0.0.1:8188"
COMFYUI_OUTPUT = Path("/Users/pengzhan/Documents/ComfyUI/output/video/LTX")

# 加载优化版工作流
workflow = json.load(open(PROJECT_ROOT / "pipelines/ltx_t2v_workflow.json"))

# 获取镜头信息
import sqlite3
conn = sqlite3.connect(PROJECT_ROOT / "data/story_agents.db")
cursor = conn.cursor()

print(f"\n{'='*60}")
print(f"测试3: 批量渲染 8个镜头 (~1分钟视频)")
print(f"{'='*60}\n")

# 获取镜头数据
shots = []
for shot_id in SHOT_IDS:
    cursor.execute("SELECT id, location, narration, render_payload FROM shots WHERE id=?", (shot_id,))
    row = cursor.fetchone()
    if row:
        shots.append({
            'id': row[0],
            'location': row[1],
            'narration': row[2],
            'payload': json.loads(row[3]) if row[3] else None
        })

conn.close()

print(f"📋 镜头列表:")
for i, shot in enumerate(shots):
    print(f"   [{i+1}] ID={shot['id']}: {shot['location']} - {shot['narration'][:20]}...")

# 渲染每个镜头
print(f"\n🎬 开始批量渲染...")
rendered_videos = []
start_time = time.time()

for i, shot in enumerate(shots):
    print(f"\n[{i+1}/8] 渲染 shot {shot['id']} - {shot['location']}")
    
    # 准备工作流
    wf = json.loads(json.dumps(workflow))  # 深拷贝
    seed = int(time.time() * 1000) % (2**31) + shot['id']
    
    # 设置参数
    for nid, node in wf.items():
        if node.get('class_type') == 'RandomNoise':
            node['inputs']['noise_seed'] = seed
    
    # 构建 prompt
    prompt = f"{shot['location']}, {shot['narration']}, cinematic, dramatic lighting"
    wf['109']['inputs']['value'] = prompt[:200]  # 限制长度
    
    # 分辨率：竖屏 720×1280
    wf['131']['inputs']['width'] = 360
    wf['131']['inputs']['height'] = 640
    wf['113']['inputs']['value'] = 17  # ~1秒
    
    print(f"   Prompt: {prompt[:50]}...")
    
    # 提交渲染
    try:
        r = requests.post(f"{COMFYUI_BASE}/prompt", json={'prompt': wf}, timeout=10)
        prompt_id = r.json().get('prompt_id')
        print(f"   prompt_id: {prompt_id[:20]}...")
        
        # 等待完成
        shot_start = time.time()
        for j in range(20):
            time.sleep(15)
            hist = requests.get(f"{COMFYUI_BASE}/history/{prompt_id}", timeout=5).json()
            if prompt_id in hist and hist[prompt_id].get('status', {}).get('completed'):
                elapsed = time.time() - shot_start
                # 找最新输出
                latest = max(COMFYUI_OUTPUT.glob('*.mp4'), key=lambda x: x.stat().st_mtime)
                print(f"   ✅ 完成: {latest.name} ({elapsed:.0f}s)")
                
                # 复制到项目输出
                dest = OUTPUT_DIR / f"shot_{shot['id']}.mp4"
                shutil.copy2(latest, dest)
                rendered_videos.append({
                    'shot_id': shot['id'],
                    'path': dest,
                    'narration': shot['narration']
                })
                break
            queue = requests.get(f"{COMFYUI_BASE}/queue", timeout=5).json()
            running = len(queue.get('queue_running', []))
            if running == 0 and j > 2:
                print(f"   ⚠️ 队列已空但未记录完成")
                break
            if j % 2 == 0:
                print(f"   [{j//2+1}/10] 运行中 ({time.time()-shot_start:.0f}s)")
                
    except Exception as e:
        print(f"   ❌ 渲染失败: {e}")
        continue

total_render_time = time.time() - start_time
print(f"\n{'='*60}")
print(f"渲染完成: {len(rendered_videos)}/8 镜头")
print(f"总耗时: {total_render_time/60:.1f} 分钟")
print(f"{'='*60}\n")

if len(rendered_videos) < 8:
    print(f"⚠️ 有镜头渲染失败，继续用已完成的镜头合成")

# 合成所有镜头
print(f"\n🎞️ 开始合成...")
narrations = [shot['narration'] for shot in rendered_videos]
full_narration = " ".join(narrations)
print(f"   旁白文本: {full_narration[:50]}...")

# 生成完整旁白 TTS
print(f"\n🔊 生成旁白 TTS...")
tts_output = OUTPUT_DIR / "full_narration.wav"
aiff_tmp = OUTPUT_DIR / "narration_tmp.aiff"
subprocess.run(['say', '-v', 'Tingting', '-o', str(aiff_tmp), full_narration], check=True)
subprocess.run(['ffmpeg', '-y', '-i', str(aiff_tmp), '-ar', '48000', '-ac', '2', str(tts_output)], 
               capture_output=True, check=True)
aiff_tmp.unlink()

probe_tts = subprocess.run([
    'ffprobe', '-v', 'error', '-show_entries', 'stream=duration', '-of', 'csv=p=0', str(tts_output)
], capture_output=True, text=True)
tts_duration = float(probe_tts.stdout.strip().split('\n')[0])
print(f"   TTS 时长: {tts_duration:.1f}s")

# 合并所有视频（concat）
print(f"\n📁 合并视频片段...")
concat_list = OUTPUT_DIR / "concat_list.txt"
with open(concat_list, 'w') as f:
    for video in rendered_videos:
        f.write(f"file '{video['path'].name}'\n")

concat_video = OUTPUT_DIR / "concat_raw.mp4"
subprocess.run([
    'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_list),
    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
    str(concat_video)
], capture_output=True, check=True)

probe_concat = subprocess.run([
    'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=duration', '-of', 'csv=p=0', str(concat_video)
], capture_output=True, text=True)
video_duration = float(probe_concat.stdout.strip())
print(f"   合并视频时长: {video_duration:.1f}s")

# 调整视频时长匹配音频
print(f"\n🎯 最终合成...")
final_output = OUTPUT_DIR / "final_batch_1min.mp4"

if video_duration < tts_duration:
    # 循环视频
    print(f"   循环视频到 {tts_duration:.1f}s")
    loop_count = int(tts_duration / video_duration) + 1
    looped_video = OUTPUT_DIR / "looped_concat.mp4"
    subprocess.run([
        'ffmpeg', '-y', '-stream_loop', str(loop_count), '-i', str(concat_video),
        '-t', str(tts_duration + 0.5),
        '-c:v', 'libx264', '-preset', 'fast',
        str(looped_video)
    ], capture_output=True, check=True)
    concat_video = looped_video

# 合成视频 + 音频
subprocess.run([
    'ffmpeg', '-y',
    '-i', str(concat_video),
    '-i', str(tts_output),
    '-map', '0:v',
    '-map', '1:a',
    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
    '-c:a', 'aac', '-b:a', '128k',
    '-shortest',
    str(final_output)
], capture_output=True, check=True)

print(f"   ✅ 最终输出: {final_output.name}")
print(f"   文件大小: {final_output.stat().st_size/1024/1024:.1f}MB")

# 验证输出
probe_final = subprocess.run([
    'ffprobe', '-v', 'error', '-show_streams', str(final_output)
], capture_output=True, text=True)

print(f"\n📊 最终视频信息:")
for line in probe_final.stdout.split('\n'):
    if line.startswith('width=') or line.startswith('height=') or \
       line.startswith('duration=') or line.startswith('codec_name='):
        print(f"   {line}")

print(f"\n{'='*60}")
print(f"✅ 批量渲染测试完成!")
print(f"输出目录: {OUTPUT_DIR}")
print(f"{'='*60}\n")

subprocess.run(['open', str(OUTPUT_DIR)])