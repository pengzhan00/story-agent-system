#!/usr/bin/env python3
"""快速端到端测试：剧本→TTS→BGM→合成"""
import sys
import json
import subprocess
import base64
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
PROJECT_ROOT = Path("/Users/pengzhan/myworkspace/projects/story-agent-system")
OUTPUT_DIR = PROJECT_ROOT / "output" / "test_e2e_quick"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 60)
print("🧪 快速端到端测试")
print("=" * 60)

# Step 1: 读取剧本数据
print("\n📖 Step 1: 读取剧本")
import sqlite3
conn = sqlite3.connect(PROJECT_ROOT / "data/story_agents.db")
cursor = conn.cursor()
cursor.execute("""
    SELECT s.id, s.acts FROM scripts s
    JOIN projects p ON s.project_id = p.id
    WHERE p.name LIKE '%手表%' OR p.name LIKE '%广告%'
    ORDER BY s.id DESC LIMIT 1
""")
row = cursor.fetchone()
if not row:
    print("❌ 未找到广告项目")
    sys.exit(1)

script_id, acts_json = row
acts = json.loads(acts_json)
print(f"✅ 剧本 ID={script_id}")

# 提取台词
dialogues = []
bgm_mood = "科技感, 高端, 自信"
for act in acts:
    for scene in act.get("scenes", []):
        bgm_mood = scene.get("bgm_mood", bgm_mood)
        for shot in scene.get("shots", []):
            for d in shot.get("dialogue", []):
                dialogues.append({
                    "character": d.get("character", "?"),
                    "line": d.get("line", ""),
                    "emotion": d.get("emotion", ""),
                    "bgm_mood": bgm_mood,
                })
print(f"   台词数: {len(dialogues)}")
for d in dialogues[:5]:
    print(f"   [{d['character']}] {d['line']} ({d['emotion']})")
conn.close()

# Step 2: TTS 生成
print("\n🎤 Step 2: TTS 配音")
tts_files = []
for i, d in enumerate(dialogues[:3]):  # 测试前3条
    out = OUTPUT_DIR / f"tts_{i+1}.wav"
    try:
        subprocess.run([
            'edge-tts', '--voice', 'zh-CN-YunxiNeural',
            '--text', d['line'], '--write-media', str(out)
        ], capture_output=True, check=True)
        tts_files.append(out)
        print(f"   ✅ Edge-TTS: {out.name}")
    except Exception:
        aiff = OUTPUT_DIR / f"tts_{i+1}.aiff"
        subprocess.run(['say', '-v', 'Tingting', '-o', str(aiff), d['line']], check=True)
        subprocess.run(['ffmpeg', '-y', '-i', str(aiff), str(out)], capture_output=True, check=True)
        aiff.unlink()
        tts_files.append(out)
        print(f"   ✅ say: {out.name}")

# Step 3: ACEStep BGM
print("\n🎵 Step 3: ACEStep BGM")
bgm = OUTPUT_DIR / "bgm.mp3"
try:
    r = requests.post('http://127.0.0.1:8001/v1/chat/completions', json={
        'model': 'acestep-v15-turbo',
        'messages': [{'role': 'user', 'content': f'{bgm_mood}, advertising background music, modern electronic'}],
        'max_tokens': 10,
    }, timeout=120)
    msg = r.json()['choices'][0]['message']
    audio_list = msg.get('audio', [])
    if audio_list:
        url = audio_list[0].get('audio_url', {}).get('url', '')
        if 'base64,' in url:
            b64_data = url.split('base64,')[1]
            audio_data = base64.b64decode(b64_data)
            bgm.write_bytes(audio_data)
            print(f"   ✅ ACEStep BGM: {bgm.name} ({len(audio_data)/1024:.1f}KB)")
except Exception as e:
    print(f"   ⚠️ ACEStep 失败: {e}")
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '10', str(bgm)], capture_output=True, check=True)
    print(f"   ✅ 静音 BGM 兜底")

# Step 4: 合成
print("\n🎞️ Step 4: 音频合成")
concat_file = OUTPUT_DIR / "concat.txt"
with open(concat_file, 'w') as f:
    for t in tts_files:
        f.write(f"file '{t.name}'\n")
combined = OUTPUT_DIR / "voice.wav"
subprocess.run([
    'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_file),
    str(combined)
], capture_output=True, check=True)
print(f"   ✅ 合并: {combined.name}")

# 获取时长
probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=duration', '-of', 'csv=p=0', str(combined)], capture_output=True, text=True)
voice_duration = float(probe.stdout.strip()) if probe.stdout.strip() else 5.0
print(f"   配音时长: {voice_duration:.1f}s")

final = OUTPUT_DIR / "final_audio.mp3"
subprocess.run([
    'ffmpeg', '-y', '-i', str(combined), '-i', str(bgm),
    '-filter_complex', f'[0:a]volume=1.0[a1];[1:a]volume=0.3,atrim=0:{voice_duration+2}[a2];[a1][a2]amix=inputs=2:duration=first',
    str(final)
], capture_output=True, check=True)
print(f"   ✅ 最终: {final.name} ({final.stat().st_size/1024:.1f}KB)")

print("\n" + "=" * 60)
print("✅ 快速测试完成")
print("=" * 60)
subprocess.run(['open', str(OUTPUT_DIR)])