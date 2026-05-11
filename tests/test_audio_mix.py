#!/usr/bin/env python3
"""
测试正确的混音合成：
1. 保留视频原音频（LTX生成的）
2. 添加 TTS 配音
3. 添加 BGM 背景
4. 三者混音合并
"""
import subprocess
from pathlib import Path

OUTPUT_DIR = Path("/Users/pengzhan/myworkspace/projects/story-agent-system/output/test_ad_full")

# 已有素材
video = OUTPUT_DIR / "shot_1.mp4"       # LTX渲染的视频（有原音频）
tts_combined = OUTPUT_DIR / "tts_combined.wav"  # TTS配音
bgm = OUTPUT_DIR / "bgm_ad.mp3"         # BGM

print(f"\n{'='*60}")
print(f"🧪 测试：正确的音频混音合成")
print(f"{'='*60}\n")

# 检查素材
print(f"素材检查:")
print(f"  视频: {video.name} ({video.stat().st_size/1024:.0f}KB)")
print(f"  TTS: {tts_combined.name} ({tts_combined.stat().st_size/1024:.0f}KB)")
print(f"  BGM: {bgm.name} ({bgm.stat().st_size/1024:.0f}KB)")

# 获取各音频时长
def get_duration(file):
    probe = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'stream=duration',
        '-of', 'csv=p=0', str(file)
    ], capture_output=True, text=True)
    lines = probe.stdout.strip().split('\n')
    return float(lines[0]) if lines and lines[0] else 0.0

video_duration = get_duration(video)
tts_duration = get_duration(tts_combined)
bgm_duration = get_duration(bgm)

print(f"\n时长信息:")
print(f"  视频: {video_duration:.1f}s")
print(f"  TTS: {tts_duration:.1f}s")
print(f"  BGM: {bgm_duration:.1f}s")

# 策略：视频很短（1.1s），需要循环到TTS时长
# 同时保留视频原音频

final_output = OUTPUT_DIR / "final_mix_correct.mp4"

# Step 1: 提取视频原音频
print(f"\nStep 1: 提取视频原音频...")
video_audio = OUTPUT_DIR / "video_original_audio.wav"
subprocess.run([
    'ffmpeg', '-y', '-i', str(video), '-vn', '-acodec', 'pcm_s16le',
    '-ar', '48000', '-ac', '2', str(video_audio)
], capture_output=True, check=True)
print(f"  ✅ 提取完成: {video_audio.name}")

# Step 2: 循环视频原音频到目标时长
target_duration = tts_duration  # 以TTS时长为准
print(f"\nStep 2: 循环视频音频到 {target_duration:.1f}s...")
video_audio_loop = OUTPUT_DIR / "video_audio_loop.wav"
loop_count = int(target_duration / video_duration) + 1
subprocess.run([
    'ffmpeg', '-y', '-stream_loop', str(loop_count), '-i', str(video_audio),
    '-t', str(target_duration), str(video_audio_loop)
], capture_output=True, check=True)
print(f"  ✅ 循环完成")

# Step 3: 循环视频到目标时长
print(f"\nStep 3: 循环视频画面...")
video_loop = OUTPUT_DIR / "video_loop.mp4"
subprocess.run([
    'ffmpeg', '-y', '-stream_loop', str(loop_count), '-i', str(video),
    '-t', str(target_duration), '-c:v', 'libx264', '-preset', 'fast',
    '-an',  # 不包含音频，后面单独处理
    str(video_loop)
], capture_output=True, check=True)
print(f"  ✅ 视频循环完成")

# Step 4: 切割BGM到目标时长
print(f"\nStep 4: 切割BGM...")
bgm_cut = OUTPUT_DIR / "bgm_cut.mp3"
subprocess.run([
    'ffmpeg', '-y', '-i', str(bgm), '-t', str(target_duration),
    str(bgm_cut)
], capture_output=True, check=True)
print(f"  ✅ BGM切割完成")

# Step 5: 三轨混音：视频原音频 + TTS + BGM
print(f"\nStep 5: 三轨混音...")
# 音量比例：
#   - 视频原音频：0.5（环境音效）
#   - TTS配音：1.0（主声）
#   - BGM：0.3（背景）
subprocess.run([
    'ffmpeg', '-y',
    '-i', str(video_audio_loop),  # 视频1原音频
    '-i', str(tts_combined),       # TTS配音
    '-i', str(bgm_cut),            # BGM
    '-filter_complex',
    '[0:a]volume=0.5[a0];[1:a]volume=1.0[a1];[2:a]volume=0.3[a2];[a0][a1][a2]amix=inputs=3:duration=longest:dropout_transition=2[aout]',
    '-map', '0:a', '-map', '1:a', '-map', '2:a',
    str(OUTPUT_DIR / "audio_mix.wav")
], capture_output=True, check=True)
print(f"  ✅ 混音完成")

# Step 6: 最终合成：视频画面 + 混合音频
print(f"\nStep 6: 最终合成...")
audio_mix = OUTPUT_DIR / "audio_mix.wav"
subprocess.run([
    'ffmpeg', '-y',
    '-i', str(video_loop),      # 视频画面（无音频）
    '-i', str(audio_mix),       # 混合音频
    '-map', '0:v',              # 视频
    '-map', '1:a',              # 音频
    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
    '-c:a', 'aac', '-b:a', '192k',
    str(final_output)
], capture_output=True, check=True)

print(f"\n{'='*60}")
print(f"✅ 正确混音合成完成!")
print(f"输出: {final_output.name} ({final_output.stat().st_size/1024/1024:.1f}MB)")
print(f"{'='*60}\n")

# 验证输出
probe = subprocess.run([
    'ffprobe', '-v', 'error', '-show_streams', '-show_format', str(final_output)
], capture_output=True, text=True)

print(f"最终视频信息:")
for line in probe.stdout.split('\n'):
    if line.startswith('width=') or line.startswith('height=') or \
       line.startswith('duration=') or line.startswith('codec_name=') or \
       line.startswith('bit_rate='):
        print(f"  {line}")

# 打开输出目录
subprocess.run(['open', str(OUTPUT_DIR)])