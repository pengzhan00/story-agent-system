"""
Compositor — Shot 级音视频合成 + 字幕 + 剧集合成
依赖: ffmpeg（系统级）
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
import sys

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.database as db

OUTPUT_DIR = PROJECT_ROOT / "output"


# ── 工具函数 ──────────────────────────────────────────────

def _ffmpeg(*args, timeout: int = 600) -> tuple[bool, str]:
    cmd = ["ffmpeg", "-y"] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return False, result.stderr[-500:]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "ffmpeg 超时"
    except FileNotFoundError:
        return False, "ffmpeg 未安装"


def _get_duration(path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _has_audio_stream(path: str) -> bool:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


# ── 字幕生成 ──────────────────────────────────────────────

def dialogue_to_srt(dialogue_list: list, audio_files: list[dict]) -> str:
    """
    把对白列表 + 对应 TTS 音频信息转成 SRT 字幕字符串。
    audio_files: [{"line_idx": 0, "duration": 2.5, ...}]
    """
    lines = []
    cursor = 0.0
    idx = 0

    duration_map = {a["line_idx"]: a.get("duration", 2.0) for a in audio_files}

    for i, line in enumerate(dialogue_list):
        if not isinstance(line, dict):
            continue
        text = line.get("line", "").strip()
        character = line.get("character", "")
        if not text:
            continue

        dur = duration_map.get(i, max(len(text) * 0.12, 1.5))
        start = cursor
        end = cursor + dur
        cursor = end + 0.3  # 间隔

        idx += 1
        start_str = _seconds_to_srt_time(start)
        end_str = _seconds_to_srt_time(end)
        display = f"[{character}] {text}" if character else text
        lines.append(f"{idx}\n{start_str} --> {end_str}\n{display}\n")

    return "\n".join(lines)


def _seconds_to_srt_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    ms = int((s - int(s)) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def burn_subtitles(video_path: str, srt_path: str, output_path: str) -> bool:
    """将 SRT 字幕烧录进视频。"""
    srt_escaped = srt_path.replace(":", "\\:").replace("'", "\\'")
    ok, err = _ffmpeg(
        "-i", video_path,
        "-vf", f"subtitles='{srt_escaped}':force_style='FontSize=24,PrimaryColour=&H00FFFFFF'",
        "-c:a", "copy",
        output_path,
    )
    if not ok:
        print(f"[Compositor] 字幕烧录失败: {err}")
    return ok


# ── Shot 级合成 ───────────────────────────────────────────

def compose_shot(
    shot_id: int,
    video_path: str,
    tts_files: list[dict],
    music_path: Optional[str],
    sfx_paths: list[str],
    output_path: str,
    burn_subs: bool = True,
    project_id: int = 0,
) -> Optional[str]:
    """
    把单个 shot 的视频 + TTS 配音 + 配乐 + 音效合并成带字幕的视频。
    返回输出文件路径或 None。
    """
    if not Path(video_path).exists():
        print(f"[Compositor] 视频不存在: {video_path}")
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. 拼接 TTS 音轨（多行对白顺序拼接）
        tts_combined = None
        if tts_files:
            tts_paths = [f["file"] for f in tts_files if Path(f["file"]).exists()]
            if tts_paths:
                if len(tts_paths) == 1:
                    tts_combined = tts_paths[0]
                else:
                    tts_combined = str(tmp / "tts_combined.mp3")
                    concat_list = tmp / "tts_list.txt"
                    concat_list.write_text("\n".join(f"file '{p}'" for p in tts_paths))
                    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(concat_list),
                            "-c", "copy", tts_combined)

        # 2. 获取视频时长
        video_dur = _get_duration(video_path)

        # 3. 构建 ffmpeg 多轨混音命令
        inputs = ["-i", video_path]
        filter_parts = []
        audio_labels = []

        if tts_combined and Path(tts_combined).exists():
            inputs += ["-i", tts_combined]
            tts_idx = len(inputs) // 2 - 1
            filter_parts.append(f"[{tts_idx}:a]volume=1.0,apad=whole_dur={video_dur}[tts]")
            audio_labels.append("[tts]")

        if music_path and Path(music_path).exists():
            inputs += ["-i", music_path]
            mus_idx = len(inputs) // 2 - 1
            filter_parts.append(f"[{mus_idx}:a]volume=0.25,aloop=loop=-1:size=2e+09,atrim=duration={video_dur}[bgm]")
            audio_labels.append("[bgm]")

        for sfx_path in sfx_paths:
            if Path(sfx_path).exists():
                inputs += ["-i", sfx_path]
                sfx_idx = len(inputs) // 2 - 1
                filter_parts.append(f"[{sfx_idx}:a]volume=0.5,apad=whole_dur={video_dur}[sfx{sfx_idx}]")
                audio_labels.append(f"[sfx{sfx_idx}]")

        # 先生成带音频的中间视频
        mixed_video = str(tmp / "mixed.mp4")
        if audio_labels:
            mix_filter = ";".join(filter_parts)
            mix_filter += f";{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=first[aout]"
            cmd_args = inputs + [
                "-filter_complex", mix_filter,
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                mixed_video,
            ]
        else:
            # 无音频 — 静默音轨
            cmd_args = inputs + [
                "-an",
                "-c:v", "copy",
                mixed_video,
            ]

        ok, err = _ffmpeg(*cmd_args)
        if not ok:
            print(f"[Compositor] 混音失败: {err}")
            return None

        # 4. 字幕处理
        if burn_subs and tts_files:
            shot = db.get_shot(shot_id) if shot_id else None
            if shot:
                try:
                    dialogue_list = json.loads(shot.dialogue) if shot.dialogue else []
                except Exception:
                    dialogue_list = []
                srt_content = dialogue_to_srt(dialogue_list, tts_files)
                srt_path = str(tmp / "subs.srt")
                Path(srt_path).write_text(srt_content, encoding="utf-8")
                final_video = str(tmp / "final.mp4")
                if burn_subtitles(mixed_video, srt_path, final_video):
                    import shutil
                    shutil.copy2(final_video, output_path)
                    return output_path

        # 字幕失败或不需要 — 直接用混音结果
        import shutil
        shutil.copy2(mixed_video, output_path)
        return output_path


# ── 剧集合成 ──────────────────────────────────────────────

def compose_episode(
    project_name: str,
    episode: int,
    shot_videos: list[str],
    output_path: str,
    crossfade_duration: float = 0.5,
) -> Optional[str]:
    """
    把多个 shot 视频按顺序合并为一集，支持 crossfade 转场。
    """
    valid_videos = [v for v in shot_videos if Path(v).exists()]
    if not valid_videos:
        print(f"[Compositor] 无有效视频文件")
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        if len(valid_videos) == 1:
            import shutil
            shutil.copy2(valid_videos[0], output_path)
            return output_path

        if crossfade_duration > 0:
            return _crossfade_concat(valid_videos, output_path, crossfade_duration, tmp)
        else:
            return _simple_concat(valid_videos, output_path, tmp)


def _simple_concat(videos: list[str], output_path: str, tmp: Path) -> Optional[str]:
    """快速拼接（无过渡）。"""
    concat_list = tmp / "concat.txt"
    concat_list.write_text("\n".join(f"file '{v}'" for v in videos))
    ok, err = _ffmpeg(
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        output_path,
    )
    if not ok:
        # 回退 re-encode
        inputs_flat = []
        for v in videos:
            inputs_flat += ["-i", v]
        n = len(videos)
        filter_v = "".join(f"[{i}:v]" for i in range(n))
        filter_a = "".join(f"[{i}:a]" for i in range(n) if _has_audio_stream(v))
        has_audio = any(_has_audio_stream(v) for v in videos)
        filter_complex = f"{filter_v}concat=n={n}:v=1:a={1 if has_audio else 0}[outv]"
        if has_audio:
            filter_complex += f";{filter_a}concat=n={n}:v=0:a=1[outa]"

        map_args = ["-map", "[outv]"]
        if has_audio:
            map_args += ["-map", "[outa]"]

        ok2, err2 = _ffmpeg(
            *inputs_flat,
            "-filter_complex", filter_complex,
            *map_args,
            "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            output_path,
        )
        if not ok2:
            print(f"[Compositor] 拼接失败: {err2}")
            return None

    return output_path if Path(output_path).exists() else None


def _crossfade_concat(videos: list[str], output_path: str, cf: float, tmp: Path) -> Optional[str]:
    """带 crossfade 的视频拼接（逐对合并）。"""
    current = videos[0]
    for i, next_v in enumerate(videos[1:], 1):
        dur = _get_duration(current)
        offset = max(dur - cf, 0)
        out = str(tmp / f"stage_{i}.mp4")

        filter_complex = (
            f"[0:v][1:v]xfade=transition=fade:duration={cf}:offset={offset}[outv]"
        )
        has_a0 = _has_audio_stream(current)
        has_a1 = _has_audio_stream(next_v)
        map_args = ["-map", "[outv]"]

        if has_a0 and has_a1:
            filter_complex += f";[0:a][1:a]acrossfade=d={cf}[outa]"
            map_args += ["-map", "[outa]"]

        ok, err = _ffmpeg(
            "-i", current, "-i", next_v,
            "-filter_complex", filter_complex,
            *map_args,
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            out,
        )
        if not ok:
            print(f"[Compositor] crossfade {i} 失败: {err}，回退简单拼接")
            return _simple_concat(videos, output_path, tmp)
        current = out

    import shutil
    shutil.copy2(current, output_path)
    return output_path if Path(output_path).exists() else None


# ── 全项目合成管线 ────────────────────────────────────────

def run_compositor_pipeline(
    project_id: int,
    episode: int = 1,
    burn_subs: bool = True,
    crossfade: float = 0.5,
    progress_fn=None,
) -> dict:
    """
    完整合成管线：读取所有已渲染 shot → 合成 → 字幕 → 剧集合并。
    """
    def _progress(msg, pct=0.0):
        print(f"[Compositor] {msg}")
        if progress_fn:
            progress_fn(msg, pct)

    proj = db.get_project(project_id)
    if not proj:
        return {"success": False, "error": f"项目 {project_id} 不存在"}

    proj_out = OUTPUT_DIR / "projects" / proj.name
    audio_dir = proj_out / "audio"
    composed_dir = proj_out / "composed"
    composed_dir.mkdir(parents=True, exist_ok=True)

    shots = db.list_shots(project_id=project_id)
    shots = [s for s in shots if s.status in ("rendered", "approved")]
    if not shots:
        return {"success": False, "error": "没有已渲染的 shot"}

    _progress(f"合成 {len(shots)} 个 shot...", 0.0)

    # 获取项目音乐（取第一个 bgm）
    music_list = db.list_music(project_id)
    bgm_path = None
    for m in music_list:
        if m.type in ("bgm", "theme") and m.file_path and Path(m.file_path).exists():
            bgm_path = m.file_path
            break

    from core.asset_registry import get_shot_video, get_shot_tts, is_shot_composed

    composed_videos = []
    for i, shot in enumerate(shots):
        pct = 0.1 + 0.7 * i / max(len(shots), 1)

        out_path = str(composed_dir / f"shot_{shot.id:04d}_composed.mp4")

        # ── 复用检查：已合成文件存在，直接使用 ────────────────
        if is_shot_composed(proj.name, shot.id):
            _progress(f"  ♻️  shot {shot.id} 已合成，复用", pct)
            composed_videos.append(out_path)
            continue

        _progress(f"合成 shot {shot.id} ({i+1}/{len(shots)})", pct)

        # 从 asset_registry 获取视频和 TTS
        video_path = get_shot_video(project_id, shot.id, proj.name)
        if not video_path or not Path(video_path).exists():
            _progress(f"  shot {shot.id} 无视频，跳过")
            continue

        tts_files = get_shot_tts(project_id, shot.id)

        result = compose_shot(
            shot_id=shot.id,
            video_path=video_path,
            tts_files=tts_files,
            music_path=bgm_path,
            sfx_paths=[],
            output_path=out_path,
            burn_subs=burn_subs,
            project_id=project_id,
        )
        if result:
            composed_videos.append(result)

    if not composed_videos:
        return {"success": False, "error": "没有合成成功的视频"}

    # 剧集合并
    _progress("合并剧集...", 0.85)
    ep_dir = proj_out / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    ep_path = str(ep_dir / f"ep{episode:03d}_final.mp4")

    final = compose_episode(
        project_name=proj.name,
        episode=episode,
        shot_videos=composed_videos,
        output_path=ep_path,
        crossfade_duration=crossfade,
    )

    _progress("合成管线完成", 1.0)
    return {
        "success": bool(final),
        "episode_file": final,
        "composed_shots": len(composed_videos),
        "total_shots": len(shots),
    }
