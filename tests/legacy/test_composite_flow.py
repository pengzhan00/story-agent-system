#!/usr/bin/env python3
"""测试完整合成流程：渲染视频 + TTS旁白 + BGM → 最终输出"""
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# 项目路径
PROJECT_ROOT = Path("/Users/pengzhan/myworkspace/projects/story-agent-system")
OUTPUT_DIR = PROJECT_ROOT / "output" / "test_composite"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 源视频（刚才渲染的）
SOURCE_VIDEO = Path("/Users/pengzhan/Documents/ComfyUI/output/video/LTX/storyagent_ltx_t2v_00015_.mp4")

# 旁白文本
NARRATION = "林萧跪地受辱，未婚妻冷漠，反派嚣张。"

print(f"\n{'='*60}")
print(f"完整合成流程测试")
print(f"{'='*60}\n")

# 步骤1: 复制渲染视频到项目输出
print(f"🎬 步骤1: 准备渲染视频")
shot_video = OUTPUT_DIR / "shot_453.mp4"
shutil.copy2(SOURCE_VIDEO, shot_video)
print(f"   ✅ 复制: {shot_video.name} ({shot_video.stat().st_size/1024:.0f}KB)")

# 检查视频参数
probe = subprocess.run([
    'ffprobe', '-v', 'error', '-show_streams', str(shot_video)
], capture_output=True, text=True)
for line in probe.stdout.split('\n'):
    if line.startswith('width=') or line.startswith('height=') or line.startswith('duration='):
        print(f"   {line}")

# 步骤2: 生成 TTS 旁白
print(f"\n🔊 步骤2: 生成 TTS 旁白")
tts_output = OUTPUT_DIR / "narration_453.wav"

# 使用 ChatTTS 或系统的 TTS
# 检查是否有 TTS 服务
tts_available = False
try:
    # 尝试 ACEStep TTS
    r = subprocess.run(['curl', '-s', 'http://127.0.0.1:8001/health'], capture_output=True, text=True, timeout=5)
    if r.returncode == 0:
        tts_available = True
        print(f"   ACEStep 服务可用")
except:
    pass

if not tts_available:
    # 使用系统 say 命令生成临时音频
    print(f"   使用系统 TTS (say)")
    aiff_tmp = OUTPUT_DIR / "narration_tmp.aiff"
    subprocess.run(['say', '-v', 'Tingting', '-o', str(aiff_tmp), NARRATION], check=True)
    # 转换为 wav
    subprocess.run(['ffmpeg', '-y', '-i', str(aiff_tmp), '-ar', '48000', '-ac', '2', str(tts_output)], 
                   capture_output=True, check=True)
    aiff_tmp.unlink()
    print(f"   ✅ TTS 输出: {tts_output.name}")
else:
    # TODO: 调用 ACEStep TTS API
    print(f"   ⚠️ ACEStep TTS 需要配置，使用备用方案")
    aiff_tmp = OUTPUT_DIR / "narration_tmp.aiff"
    subprocess.run(['say', '-v', 'Tingting', '-o', str(aiff_tmp), NARRATION], check=True)
    subprocess.run(['ffmpeg', '-y', '-i', str(aiff_tmp), '-ar', '48000', '-ac', '2', str(tts_output)], 
                   capture_output=True, check=True)
    aiff_tmp.unlink()
    print(f"   ✅ TTS 输出: {tts_output.name}")

# 检查 TTS 时长
probe_tts = subprocess.run([
    'ffprobe', '-v', 'error', '-show_entries', 'stream=duration', '-of', 'csv=p=0', str(tts_output)
], capture_output=True, text=True)
tts_duration = float(probe_tts.stdout.strip())
print(f"   TTS 时长: {tts_duration:.2f}s")

# 步骤3: 合成视频 + 音频
print(f"\n🎞️ 步骤3: 合成视频 + 音频")
final_output = OUTPUT_DIR / "final_shot_453.mp4"

# 视频时长
probe_video = subprocess.run([
    'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=duration', '-of', 'csv=p=0', str(shot_video)
], capture_output=True, text=True)
video_duration = float(probe_video.stdout.strip().split('\n')[0]) if probe_video.stdout.strip() else 1.0
print(f"   视频时长: {video_duration:.2f}s")

# 调整音频时长匹配视频（如果需要）
if tts_duration > video_duration:
    # 音频比视频长，延长视频
    print(f"   音频较长，延长视频循环")
    # 循环视频到音频长度
    looped_video = OUTPUT_DIR / "looped_video.mp4"
    subprocess.run([
        'ffmpeg', '-y', '-stream_loop', '-1', '-i', str(shot_video),
        '-t', str(tts_duration + 0.5),
        '-c:v', 'libx264', '-preset', 'fast',
        str(looped_video)
    ], capture_output=True, check=True)
    shot_video = looped_video
elif tts_duration < video_duration:
    # 音频比视频短，截取视频
    print(f"   视频较长，截取到音频长度")

# 合成：视频 + TTS 音频
subprocess.run([
    'ffmpeg', '-y',
    '-i', str(shot_video),
    '-i', str(tts_output),
    '-map', '0:v',
    '-map', '1:a',
    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
    '-c:a', 'aac', '-b:a', '128k',
    '-shortest',
    str(final_output)
], capture_output=True, check=True)

print(f"   ✅ 合成完成: {final_output.name}")
print(f"   文件大小: {final_output.stat().st_size/1024:.0f}KB")

# 步骤4: 检查最终输出
print(f"\n📦 步骤4: 验证最终输出")
probe_final = subprocess.run([
    'ffprobe', '-v', 'error', '-show_streams', str(final_output)
], capture_output=True, text=True)

print(f"   最终视频信息:")
for line in probe_final.stdout.split('\n'):
    if line.startswith('width=') or line.startswith('height=') or \
       line.startswith('duration=') or line.startswith('codec_name='):
        print(f"   {line}")

print(f"\n{'='*60}")
print(f"✅ 完整合成流程测试完成!")
print(f"输出目录: {OUTPUT_DIR}")
print(f"{'='*60}\n")

# 打开输出目录
subprocess.run(['open', str(OUTPUT_DIR)])