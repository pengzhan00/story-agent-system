#!/usr/bin/env python3
"""
方案3完整验证：
渲染全部4镜头 → 每镜头配BGM → 每镜头混音 → 拼接成片
"""
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
import sqlite3
import copy
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

PROJECT_ROOT = Path("/Users/pengzhan/myworkspace/projects/story-agent-system")
OUTPUT_DIR = PROJECT_ROOT / "output" / "test_ad_4shots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

COMFYUI_BASE = "http://127.0.0.1:8188"
COMFYUI_OUTPUT = Path("/Users/pengzhan/Documents/ComfyUI/output/video/LTX")

# 加载工作流
workflow = json.load(open(PROJECT_ROOT / "pipelines/ltx_t2v_workflow.json"))

# ─────────────────────────────────────────────────────────────
# 读取剧本数据
# ─────────────────────────────────────────────────────────────
conn = sqlite3.connect(PROJECT_ROOT / "data/story_agents.db")
cursor = conn.cursor()
cursor.execute("""
    SELECT s.acts FROM scripts s 
    JOIN projects p ON s.project_id = p.id 
    WHERE p.name LIKE '%手表%' OR p.name LIKE '%广告%'
    ORDER BY s.id DESC LIMIT 1
""")
acts_json = cursor.fetchone()[0]
acts = json.loads(acts_json)
conn.close()

# 提取所有镜头信息
shots_data = []
for act in acts:
    for scene in act.get("scenes", []):
        location = scene.get("location", "")
        time_of_day = scene.get("time_of_day", "白天")
        weather = scene.get("weather", "晴")
        narration = scene.get("narration", "")
        bgm_mood = scene.get("bgm_mood", "科技感")
        
        for shot in scene.get("shots", []):
            shot_number = shot.get("shot_number", 1)
            camera_angle = shot.get("camera_angle", "中景")
            mood = shot.get("mood", "")
            dialogue = shot.get("dialogue", [])
            
            shots_data.append({
                "shot_number": len(shots_data) + 1,
                "location": location,
                "time_of_day": time_of_day,
                "weather": weather,
                "camera_angle": camera_angle,
                "mood": mood or bgm_mood,
                "narration": narration,
                "bgm_mood": bgm_mood,
                "dialogue": dialogue,
            })

print(f"\n{'='*60}")
print(f"🎬 方案3：批量渲染 + 分镜头配乐 + 拼接")
print(f"{'='*60}\n")

print(f"📋 镜头列表 ({len(shots_data)}个):")
for s in shots_data:
    d_lines = [d.get('line', '') for d in s['dialogue']]
    print(f"   [{s['shot_number']}] {s['location']} - {s['camera_angle']} | BGM:{s['bgm_mood']}")
    if d_lines:
        print(f"       台词: {d_lines[0][:30]}...")

# ─────────────────────────────────────────────────────────────
# Step 1: 批量渲染4个镜头
# ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"🎥 Step 1: 批量渲染4个镜头")
print(f"{'─'*60}\n")

rendered_shots = []
render_start = time.time()

for i, shot in enumerate(shots_data):
    shot_idx = i + 1
    print(f"\n[镜头 {shot_idx}/4] {shot['location']} - {shot['camera_angle']}")
    
    # 构建 render_payload
    from pipelines.render_pipeline import build_pipeline_prompt_bundle, normalize_shot_payload
    
    render_payload = {
        "scene": {
            "location": shot["location"],
            "time_of_day": shot["time_of_day"],
            "weather": shot["weather"],
            "lighting": f"{shot['time_of_day']}，现代办公室灯光，明亮",
            "atmosphere": f"{shot['mood']}, 科技感，高端",
        },
        "story": {
            "beat": shot.get("dialogue", [{}])[0].get("line", shot["narration"]),
            "mood": shot["mood"],
            "narration": shot["narration"],
        },
        "characters": [{
            "name": "李明",
            "appearance": "成功商务人士，西装，自信眼神，佩戴高端智能手表",
        }],
        "subject": {
            "action": "展示手表，自信姿态",
            "emotion": shot.get("dialogue", [{}])[0].get("emotion", "自信"),
        },
        "camera": {
            "shot_type": shot["camera_angle"],
        },
        "style": {
            "style_guide": f"{shot['bgm_mood']}, 科技感，高端，现代，广告风格",
            "visual_style": "commercial advertising",
        },
        "output_spec": {
            "width": 480,
            "height": 832,
            "frames": 49,
            "fps": 16,
        },
    }
    
    normalized = normalize_shot_payload(render_payload)
    bundle = build_pipeline_prompt_bundle(normalized, "ltx")
    prompt_text = bundle["positive_prompt"]
    
    print(f"   Prompt: {prompt_text[:80]}...")
    
    # 注入工作流
    wf = copy.deepcopy(workflow)
    seed = int(time.time() * 1000) % (2**31) + shot_idx * 100
    
    for nid, node in wf.items():
        inputs = node.get("inputs", {})
        if "text" in inputs and isinstance(inputs["text"], str):
            current = inputs["text"].lower()
            if "negative" not in current and "low quality" not in current:
                inputs["text"] = prompt_text
        if "width" in inputs:
            inputs["width"] = bundle["width"]
        if "height" in inputs:
            inputs["height"] = bundle["height"]
        if node.get("class_type") == "RandomNoise":
            inputs["noise_seed"] = seed
    
    # 提交渲染
    shot_start = time.time()
    try:
        r = requests.post(f"{COMFYUI_BASE}/prompt", json={'prompt': wf}, timeout=10)
        result = r.json()
        prompt_id = result.get('prompt_id')
        
        if 'error' in result:
            print(f"   ❌ 提交失败: {result['error']}")
            continue
        
        print(f"   prompt_id: {prompt_id[:20]}...")
        
        # 等待完成
        for j in range(20):
            time.sleep(15)
            elapsed = time.time() - shot_start
            
            hist = requests.get(f"{COMFYUI_BASE}/history/{prompt_id}", timeout=5).json()
            if prompt_id in hist and hist[prompt_id].get('status', {}).get('completed'):
                # 找输出文件
                latest = max(COMFYUI_OUTPUT.glob('*.mp4'), key=lambda x: x.stat().st_mtime)
                output_path = OUTPUT_DIR / f"shot_{shot_idx}.mp4"
                shutil.copy2(latest, output_path)
                rendered_shots.append({
                    "shot_idx": shot_idx,
                    "video": output_path,
                    "data": shot,
                })
                print(f"   ✅ 渲染完成: {output_path.name} ({elapsed:.0f}s)")
                break
            
            if j % 2 == 0:
                print(f"   [{j//2+1}/10] 运行中 ({elapsed:.0f}s)")
        
    except Exception as e:
        print(f"   ❌ 渲染失败: {e}")
        continue

render_time = time.time() - render_start
print(f"\n   渲染完成: {len(rendered_shots)}/4 镜头，总耗时 {render_time/60:.1f} 分钟")

# ─────────────────────────────────────────────────────────────
# Step 2: 每镜头生成BGM
# ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"🎵 Step 2: 每镜头生成BGM")
print(f"{'─'*60}\n")

from pipelines.audio_pipeline import generate_music_acestep_direct

bgm_files = []
for shot in rendered_shots:
    shot_idx = shot["shot_idx"]
    bgm_mood = shot["data"]["bgm_mood"]
    
    print(f"\n[镜头 {shot_idx}] BGM风格: {bgm_mood}")
    
    bgm_path = OUTPUT_DIR / f"bgm_{shot_idx}.mp3"
    
    try:
        success = generate_music_acestep_direct(
            prompt=f"{bgm_mood}, cinematic, modern, advertising background music, electronic",
            output_path=str(bgm_path),
            duration=5,  # 每个镜头约3秒，BGM稍长
        )
        if success and bgm_path.exists():
            bgm_files.append(bgm_path)
            print(f"   ✅ BGM生成: {bgm_path.name}")
        else:
            print(f"   ❌ BGM失败")
    except Exception as e:
        print(f"   ⚠️ ACEStep失败: {e}")

# ─────────────────────────────────────────────────────────────
# Step 3: 每镜头生成TTS配音
# ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"🎤 Step 3: 每镜头生成TTS配音")
print(f"{'─'*60}\n")

from pipelines.audio_pipeline import generate_tts

tts_files = []
for shot in rendered_shots:
    shot_idx = shot["shot_idx"]
    dialogue = shot["data"]["dialogue"]
    
    if not dialogue:
        print(f"[镜头 {shot_idx}] 无台词，跳过")
        continue
    
    print(f"\n[镜头 {shot_idx}] 台词:")
    
    # 合并该镜头所有台词
    lines = [d.get("line", "") for d in dialogue]
    text = " ".join(lines)
    
    tts_path = OUTPUT_DIR / f"tts_{shot_idx}.wav"
    
    try:
        success = generate_tts(
            text=text,
            output_path=str(tts_path),
            backend="edge-tts",
            voice="男",
        )
        if success and tts_path.exists():
            tts_files.append(tts_path)
            print(f"   ✅ TTS: {tts_path.name}")
            print(f"       \"{text[:40]}...\"")
    except Exception as e:
        print(f"   ⚠️ TTS失败: {e}")

# ─────────────────────────────────────────────────────────────
# Step 4: 每镜头混音合成
# ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"🎧 Step 4: 每镜头混音合成")
print(f"{'─'*60}\n")

mixed_shots = []

for shot in rendered_shots:
    shot_idx = shot["shot_idx"]
    video_path = shot["video"]
    
    print(f"\n[镜头 {shot_idx}] 混音合成")
    
    # 提取视频原音频
    video_audio = OUTPUT_DIR / f"video_audio_{shot_idx}.wav"
    subprocess.run([
        'ffmpeg', '-y', '-i', str(video_path), '-vn', '-acodec', 'pcm_s16le',
        '-ar', '48000', '-ac', '2', str(video_audio)
    ], capture_output=True, check=True)
    
    # 对应BGM和TTS
    bgm_path = OUTPUT_DIR / f"bgm_{shot_idx}.mp3"
    tts_path = OUTPUT_DIR / f"tts_{shot_idx}.wav"
    
    # 如果没有TTS，BGM音量调高
    has_tts = tts_path.exists()
    bgm_volume = 0.2 if has_tts else 0.5
    
    # 混音
    audio_mix = OUTPUT_DIR / f"audio_mix_{shot_idx}.wav"
    
    inputs = [str(video_audio)]
    filter_parts = ["[0:a]volume=0.3[a0]"]
    mix_inputs = ["[a0]"]
    
    if has_tts:
        inputs.append(str(tts_path))
        filter_parts.append(f"[1:a]volume=1.0[a1]")
        mix_inputs.append("[a1]")
    
    if bgm_path.exists():
        inputs.append(str(bgm_path))
        filter_parts.append(f"[{len(inputs)-1}:a]volume={bgm_volume}[a{len(inputs)-1}]")
        mix_inputs.append(f"[a{len(inputs)-1}]")
    
    filter_complex = ";".join(filter_parts) + f";{','.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=longest[aout]"
    
    # 构建 ffmpeg 参数
    args = ['ffmpeg', '-y']
    for inp in inputs:
        args.extend(['-i', inp])
    args.extend(['-filter_complex', filter_complex, '-map', '[aout]', str(audio_mix)])
    subprocess.run(args, capture_output=True, check=True)
    
    # 合成视频+混音
    mixed_video = OUTPUT_DIR / f"mixed_{shot_idx}.mp4"
    subprocess.run([
        'ffmpeg', '-y',
        '-i', str(video_path),
        '-i', str(audio_mix),
        '-map', '0:v', '-map', '1:a',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '128k',
        str(mixed_video)
    ], capture_output=True, check=True)
    
    mixed_shots.append(mixed_video)
    print(f"   ✅ 混音完成: {mixed_video.name}")

# ─────────────────────────────────────────────────────────────
# Step 5: 拼接所有镜头
# ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"🎞️ Step 5: 拼接所有镜头")
print(f"{'─'*60}\n")

# 创建concat列表
concat_list = OUTPUT_DIR / "concat.txt"
with open(concat_list, 'w') as f:
    for mixed in mixed_shots:
        f.write(f"file '{mixed.name}'\n")

# 拼接
final_output = OUTPUT_DIR / "final_ad_4shots.mp4"
subprocess.run([
    'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
    '-i', str(concat_list),
    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
    '-c:a', 'aac', '-b:a', '128k',
    str(final_output)
], capture_output=True, check=True)

print(f"   ✅ 拼接完成: {final_output.name}")
print(f"   文件大小: {final_output.stat().st_size/1024/1024:.1f}MB")

# 验证输出
probe = subprocess.run([
    'ffprobe', '-v', 'error', '-show_streams', '-show_format', str(final_output)
], capture_output=True, text=True)

print(f"\n最终视频信息:")
for line in probe.stdout.split('\n'):
    if line.startswith('width=') or line.startswith('height=') or \
       line.startswith('duration=') or line.startswith('bit_rate='):
        print(f"   {line}")

# ─────────────────────────────────────────────────────────────
# 完成
# ─────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"✅ 方案3验证完成!")
print(f"   渲染镜头: {len(rendered_shots)}/4")
print(f"   BGM生成: {len(bgm_files)}")
print(f"   TTS生成: {len(tts_files)}")
print(f"   最终输出: {final_output.name}")
print(f"输出目录: {OUTPUT_DIR}")
print(f"{'='*60}\n")

subprocess.run(['open', str(OUTPUT_DIR)])