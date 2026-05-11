#!/usr/bin/env python3
"""
完整验证脚本：广告项目端到端流程
1. 编剧生成剧本 + dialogue ✅
2. TTS 配音生成（ChatTTS）
3. ACEStep BGM 背景音乐
4. 视频渲染（LTX）
5. 合成导出（视频+配音+BGM）
"""
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
import sqlite3

sys.path.insert(0, str(Path(__file__).parent.parent))

PROJECT_ROOT = Path("/Users/pengzhan/myworkspace/projects/story-agent-system")
OUTPUT_DIR = PROJECT_ROOT / "output" / "test_ad_full"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# Step 1: 编剧生成剧本（已完成，直接读取）
# ─────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"🧪 完整验证：广告项目端到端流程")
print(f"{'='*60}\n")

# 读取已生成的广告项目数据
conn = sqlite3.connect(PROJECT_ROOT / "data/story_agents.db")
cursor = conn.cursor()

# 获取最新项目的剧本
cursor.execute("""
    SELECT s.id, s.acts FROM scripts s 
    JOIN projects p ON s.project_id = p.id 
    WHERE p.name LIKE '%手表%' OR p.name LIKE '%广告%'
    ORDER BY s.id DESC LIMIT 1
""")
script_row = cursor.fetchone()
if not script_row:
    print("❌ 未找到广告项目剧本，请先运行 test_ad_generation.py")
    sys.exit(1)

script_id = script_row[0]
acts_json = script_row[1]
acts = json.loads(acts_json)

print(f"✅ Step 1: 剧本已存在 (script_id={script_id})")

# 提取所有台词
all_dialogues = []
for act in acts:
    for scene in act.get("scenes", []):
        location = scene.get("location", "未知")
        bgm_mood = scene.get("bgm_mood", "")
        for shot in scene.get("shots", []):
            for dialogue in shot.get("dialogue", []):
                all_dialogues.append({
                    "location": location,
                    "character": dialogue.get("character", "?"),
                    "line": dialogue.get("line", ""),
                    "emotion": dialogue.get("emotion", ""),
                    "bgm_mood": bgm_mood,
                })

print(f"   总台词数: {len(all_dialogues)}")
for d in all_dialogues:
    print(f"   [{d['character']}@{d['location']}] {d['line']} ({d['emotion']})")

conn.close()

# ─────────────────────────────────────────────────────────────
# Step 2: TTS 配音生成（ChatTTS）
# ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"🎤 Step 2: TTS 配音生成")
print(f"{'─'*60}\n")

from pipelines.audio_pipeline import generate_tts, _ranked_tts_backends

# 检查可用 TTS 后端
backends = _ranked_tts_backends()
print(f"   可用 TTS 后端: {backends}")

tts_outputs = []
for i, d in enumerate(all_dialogues[:3]):  # 先测试前3条
    text = d["line"]
    char = d["character"]
    emotion = d["emotion"]
    output_path = OUTPUT_DIR / f"tts_{i+1}_{char}.wav"
    
    print(f"\n   [{i+1}] {char}: \"{text}\" ({emotion})")
    
    try:
        success = generate_tts(
            text=text,
            output_path=str(output_path),
            backend="chattts",  # 优先使用 ChatTTS
            voice=char,  # 角色名会自动映射到音色种子
            emotion=emotion,
        )
        if success and output_path.exists():
            tts_outputs.append(output_path)
            # 检查音频时长
            probe = subprocess.run([
                'ffprobe', '-v', 'error', '-show_entries', 'stream=duration',
                '-of', 'csv=p=0', str(output_path)
            ], capture_output=True, text=True)
            duration = probe.stdout.strip().split('\n')[0]
            print(f"   ✅ 生成成功: {output_path.name} ({duration}s)")
        else:
            print(f"   ❌ 生成失败")
    except Exception as e:
        print(f"   ⚠️ ChatTTS 失败: {e}")
        # 回退到 Edge-TTS
        try:
            success = generate_tts(
                text=text,
                output_path=str(output_path),
                backend="edge-tts",
                voice="男" if char in ["李明", "林萧"] else "女",
            )
            if success and output_path.exists():
                tts_outputs.append(output_path)
                print(f"   ✅ Edge-TTS 备用成功: {output_path.name}")
        except Exception as e2:
            print(f"   ❌ Edge-TTS 也失败: {e2}")

if len(tts_outputs) == 0:
    print(f"\n   ⚠️ TTS 全部失败，使用系统 say 命令生成测试音频")
    for i, d in enumerate(all_dialogues[:3]):
        output_path = OUTPUT_DIR / f"tts_{i+1}_{d['character']}.wav"
        aiff_tmp = OUTPUT_DIR / f"tts_{i+1}.aiff"
        subprocess.run(['say', '-v', 'Tingting', '-o', str(aiff_tmp), d['line']], check=True)
        subprocess.run(['ffmpeg', '-y', '-i', str(aiff_tmp), '-ar', '48000', '-ac', '2', str(output_path)],
                       capture_output=True, check=True)
        aiff_tmp.unlink()
        tts_outputs.append(output_path)
        print(f"   ✅ say 命令生成: {output_path.name}")

print(f"\n   TTS 输出文件数: {len(tts_outputs)}")

# ─────────────────────────────────────────────────────────────
# Step 3: ACEStep BGM 背景音乐
# ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"🎵 Step 3: ACEStep BGM 背景音乐")
print(f"{'─'*60}\n")

from pipelines.audio_pipeline import generate_music, generate_music_acestep_direct

# 检查 ACEStep 服务状态
try:
    import requests
    r = requests.get("http://127.0.0.1:8001/health", timeout=5)
    acestep_online = r.status_code == 200
    print(f"   ACEStep 服务状态: {'✅ 在线' if acestep_online else '❌ 离线'}")
except:
    acestep_online = False
    print(f"   ACEStep 服务状态: ❌ 离线")

# 收集 BGM mood
bgm_moods = [d.get("bgm_mood", "科技感") for d in all_dialogues]
bgm_mood = bgm_moods[0] if bgm_moods else "科技感, 高端, 自信"
bgm_duration = 30  # 30秒背景音乐

bgm_output = OUTPUT_DIR / "bgm_ad.mp3"

if acestep_online:
    print(f"\n   生成 BGM: \"{bgm_mood}\" ({bgm_duration}s)")
    try:
        success = generate_music_acestep_direct(
            prompt=f"{bgm_mood}, cinematic, electronic, modern, advertising background music",
            output_path=str(bgm_output),
            duration=bgm_duration,
        )
        if success and bgm_output.exists():
            print(f"   ✅ ACEStep BGM 生成成功: {bgm_output.name}")
        else:
            print(f"   ❌ ACEStep 生成失败")
    except Exception as e:
        print(f"   ⚠️ ACEStep 失败: {e}")

# 如果 ACEStep 失败，使用备用方案
if not bgm_output.exists():
    print(f"\n   使用备用音乐生成...")
    try:
        success = generate_music(
            prompt=f"{bgm_mood}, advertising background",
            output_path=str(bgm_output),
            duration=bgm_duration,
        )
        if success and bgm_output.exists():
            print(f"   ✅ 备用音乐生成成功: {bgm_output.name}")
    except Exception as e:
        print(f"   ⚠️ 备用也失败: {e}")

# 最后兜底：生成静音音频
if not bgm_output.exists():
    print(f"\n   生成静音 BGM 作为兜底")
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
        '-t', str(bgm_duration), str(bgm_output)
    ], capture_output=True, check=True)
    print(f"   ⚠️ 已生成静音 BGM: {bgm_output.name}")

# ─────────────────────────────────────────────────────────────
# Step 4: 视频渲染（LTX）
# ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"🎬 Step 4: 视频渲染（LTX）")
print(f"{'─'*60}\n")

from pipelines.render_pipeline import build_pipeline_prompt_bundle, normalize_shot_payload
import requests

COMFYUI_BASE = "http://127.0.0.1:8188"
COMFYUI_OUTPUT = Path("/Users/pengzhan/Documents/ComfyUI/output/video/LTX")

# 检查 ComfyUI 状态
try:
    r = requests.get(f"{COMFYUI_BASE}/system_stats", timeout=5)
    comfyui_online = r.status_code == 200
    print(f"   ComfyUI 服务状态: {'✅ 在线' if comfyui_online else '❌ 离线'}")
except:
    comfyui_online = False
    print(f"   ComfyUI 服务状态: ❌ 禯线")

if not comfyui_online:
    print(f"\n   ⚠️ ComfyUI 禯线，跳过渲染步骤")
    print(f"   请手动启动 ComfyUI 后重试")
else:
    # 加载工作流
    workflow = json.load(open(PROJECT_ROOT / "pipelines/ltx_t2v_workflow.json"))
    
    # 从第一个场景构建 render_payload
    first_scene = acts[0]["scenes"][0]
    first_shot = first_scene["shots"][0]
    
    # 构建 render_payload（简化版）
    render_payload = {
        "scene": {
            "location": first_scene.get("location", "会议室"),
            "time_of_day": first_scene.get("time_of_day", "白天"),
            "weather": first_scene.get("weather", "晴"),
            "lighting": "现代办公室灯光，明亮",
            "atmosphere": "科技感，高端，自信",
        },
        "story": {
            "beat": first_shot.get("dialogue", [{}])[0].get("line", ""),
            "mood": first_shot.get("mood", "自信"),
            "narration": first_scene.get("narration", ""),
        },
        "characters": [{
            "name": "李明",
            "appearance": "成功商务人士，西装，自信眼神",
        }],
        "subject": {
            "action": "展示手表",
            "emotion": first_shot.get("dialogue", [{}])[0].get("emotion", "自信"),
        },
        "camera": {
            "shot_type": first_shot.get("camera_angle", "中景"),
        },
        "style": {
            "style_guide": "科技感，高端，现代，广告风格",
            "visual_style": "commercial advertising",
        },
        "output_spec": {
            "width": 480,
            "height": 832,
            "frames": 49,
            "fps": 16,
        },
    }
    
    # 构建 prompt
    normalized = normalize_shot_payload(render_payload)
    bundle = build_pipeline_prompt_bundle(normalized, "ltx")
    prompt_text = bundle["positive_prompt"]
    
    print(f"\n   生成的 Prompt:")
    print(f"   {prompt_text[:200]}...")
    
    # 注入工作流
    import copy
    wf = copy.deepcopy(workflow)
    seed = int(time.time() * 1000) % (2**31)
    
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
    print(f"\n   提交渲染任务...")
    try:
        r = requests.post(f"{COMFYUI_BASE}/prompt", json={'prompt': wf}, timeout=10)
        result = r.json()
        prompt_id = result.get('prompt_id')
        print(f"   prompt_id: {prompt_id}")
        
        # 等待完成
        print(f"\n   等待渲染完成（约5分钟）...")
        start_time = time.time()
        
        for i in range(20):
            time.sleep(15)
            elapsed = time.time() - start_time
            
            hist = requests.get(f"{COMFYUI_BASE}/history/{prompt_id}", timeout=5).json()
            if prompt_id in hist and hist[prompt_id].get('status', {}).get('completed'):
                # 找输出文件
                latest = max(COMFYUI_OUTPUT.glob('*.mp4'), key=lambda x: x.stat().st_mtime)
                video_output = OUTPUT_DIR / "shot_1.mp4"
                shutil.copy2(latest, video_output)
                print(f"\n   ✅ 渲染完成: {video_output.name} ({elapsed:.0f}s)")
                
                # 分析视频
                probe = subprocess.run([
                    'ffprobe', '-v', 'error', '-show_streams', str(video_output)
                ], capture_output=True, text=True)
                for line in probe.stdout.split('\n'):
                    if line.startswith('width=') or line.startswith('height=') or line.startswith('duration='):
                        print(f"   {line}")
                break
            
            if i % 2 == 0:
                print(f"   [{i//2+1}/10] 运行中 ({elapsed:.0f}s)")
        
    except Exception as e:
        print(f"   ❌ 渲染失败: {e}")

# ─────────────────────────────────────────────────────────────
# Step 5: 合成导出
# ─────────────────────────────────────────────────────────────
print(f"\n{'─'*60}")
print(f"🎞️ Step 5: 合成导出（视频+配音+BGM）")
print(f"{'─'*60}\n")

video_output = OUTPUT_DIR / "shot_1.mp4"
final_output = OUTPUT_DIR / "final_ad.mp4"

if not video_output.exists():
    print(f"   ⚠️ 视频不存在，跳过合成")
else:
    # 合并所有 TTS 音频
    print(f"   合并 TTS 音频...")
    tts_combined = OUTPUT_DIR / "tts_combined.wav"
    if len(tts_outputs) > 1:
        # 用 ffmpeg concat
        concat_list = OUTPUT_DIR / "tts_concat.txt"
        with open(concat_list, 'w') as f:
            for tts in tts_outputs:
                f.write(f"file '{tts.name}'\n")
        subprocess.run([
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_list),
            str(tts_combined)
        ], capture_output=True, check=True)
    elif len(tts_outputs) == 1:
        shutil.copy2(tts_outputs[0], tts_combined)
    else:
        # 生成静音
        subprocess.run([
            'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
            '-t', '3', str(tts_combined)
        ], capture_output=True, check=True)
    
    # 获取时长
    probe_tts = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'stream=duration',
        '-of', 'csv=p=0', str(tts_combined)
    ], capture_output=True, text=True)
    tts_duration = float(probe_tts.stdout.strip().split('\n')[0]) if probe_tts.stdout.strip() else 3.0
    
    probe_video = subprocess.run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=duration', '-of', 'csv=p=0', str(video_output)
    ], capture_output=True, text=True)
    video_duration = float(probe_video.stdout.strip()) if probe_video.stdout.strip() else 3.0
    
    print(f"   TTS时长: {tts_duration:.1f}s, 视频时长: {video_duration:.1f}s")
    
    # 如果 TTS 更长，循环视频
    if tts_duration > video_duration:
        looped_video = OUTPUT_DIR / "looped_video.mp4"
        loop_count = int(tts_duration / video_duration) + 1
        subprocess.run([
            'ffmpeg', '-y', '-stream_loop', str(loop_count), '-i', str(video_output),
            '-t', str(tts_duration + 0.5), '-c:v', 'libx264', '-preset', 'fast',
            str(looped_video)
        ], capture_output=True, check=True)
        video_output = looped_video
        print(f"   视频已循环到 {tts_duration:.1f}s")
    
    # 最终合成：视频 + TTS + BGM
    print(f"\n   最终合成...")
    subprocess.run([
        'ffmpeg', '-y',
        '-i', str(video_output),
        '-i', str(tts_combined),
        '-i', str(bgm_output),
        '-filter_complex', '[1:a]volume=1.0[a1];[2:a]volume=0.3[a2];[a1][a2]amix=inputs=2:duration=longest[aout]',
        '-map', '0:v',
        '-map', '[aout]',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '128k',
        '-shortest',
        str(final_output)
    ], capture_output=True, check=True)
    
    print(f"\n   ✅ 最终输出: {final_output.name}")
    print(f"   文件大小: {final_output.stat().st_size/1024/1024:.1f}MB")
    
    # 验证输出
    probe_final = subprocess.run([
        'ffprobe', '-v', 'error', '-show_streams', str(final_output)
    ], capture_output=True, text=True)
    print(f"\n   最终视频信息:")
    for line in probe_final.stdout.split('\n'):
        if line.startswith('width=') or line.startswith('height=') or line.startswith('duration='):
            print(f"   {line}")

# ─────────────────────────────────────────────────────────────
# 完成
# ─────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"✅ 验证完成！")
print(f"输出目录: {OUTPUT_DIR}")
print(f"{'='*60}\n")

subprocess.run(['open', str(OUTPUT_DIR)])