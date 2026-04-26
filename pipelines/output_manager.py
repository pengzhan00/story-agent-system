#!/usr/bin/env python3
"""
Video output management: organize, merge, and export video clips.
增强: Shot 排序（act/scene/shot_number）/ Crossfade 过渡 / 项目级合并入口

Output structure:
  output/
    projects/<project_name>/
      scenes/               # Individual scene videos (from ComfyUI)
      episodes/             # Merged episode videos
      timeline.json         # Episode/scene layout
    exports/                # Final packaged output
"""

import os
import json
import subprocess
import shutil
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = SCRIPT_DIR.parent  # project root
OUTPUT_DIR = BASE_DIR / "output"
PROJECTS_DIR = OUTPUT_DIR / "projects"
EXPORTS_DIR = OUTPUT_DIR / "exports"


def ensure_project_dirs(project_name: str) -> dict:
    """Create project output directories and return paths."""
    base = PROJECTS_DIR / project_name
    paths = {
        "base": base,
        "scenes": base / "scenes",
        "episodes": base / "episodes",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def load_timeline(project_name: str) -> dict:
    """Load project timeline from JSON."""
    tl_path = PROJECTS_DIR / project_name / "timeline.json"
    if tl_path.exists():
        with open(tl_path) as f:
            return json.load(f)
    return {"project": project_name, "episodes": [], "scenes": []}


def save_timeline(project_name: str, timeline: dict):
    """Save project timeline."""
    tl_path = PROJECTS_DIR / project_name / "timeline.json"
    tl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tl_path, "w") as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)


def register_scene(project_name: str, scene_id: str, video_file: str,
                   duration_sec: float, episode: int = 1,
                   prompt: str = ""):
    """Register a generated scene video in the project timeline."""
    tl = load_timeline(project_name)
    tl["scenes"].append({
        "id": scene_id,
        "file": str(video_file),
        "episode": episode,
        "duration": duration_sec,
        "prompt": prompt,
    })
    # Update episode list
    ep = next((e for e in tl["episodes"] if e["number"] == episode), None)
    if not ep:
        ep = {"number": episode, "scenes": [], "total_duration": 0.0}
        tl["episodes"].append(ep)
    ep["scenes"] = [s["id"] for s in tl["scenes"] if s["episode"] == episode]
    ep["total_duration"] = sum(
        s["duration"] for s in tl["scenes"] if s["episode"] == episode
    )
    save_timeline(project_name, tl)
    print(f"Registered scene {scene_id} for project '{project_name}' episode {episode}")


def merge_episode(project_name: str, episode: int = 1,
                  output_file: Optional[str] = None,
                  overwrite: bool = False) -> Optional[str]:
    """
    Merge all scenes of an episode into one video using ffmpeg concat.
    Returns the output file path or None on failure.
    """
    tl = load_timeline(project_name)
    ep_scenes = [s for s in tl["scenes"] if s["episode"] == episode]
    ep_scenes.sort(key=lambda s: s["id"])

    if not ep_scenes:
        print(f"No scenes found for episode {episode}")
        return None

    project_dir = PROJECTS_DIR / project_name
    episode_dir = project_dir / "episodes"
    episode_dir.mkdir(parents=True, exist_ok=True)

    if output_file is None:
        output_file = str(episode_dir / f"ep{episode:03d}_merged.mp4")

    # ffmpeg concat needs a file list
    filelist_path = episode_dir / f"_ep{episode:03d}_filelist.txt"
    with open(filelist_path, "w") as f:
        for scene in ep_scenes:
            scene_path = Path(scene["file"])
            if scene_path.exists():
                # Escape single quotes for ffmpeg
                escaped = str(scene_path.resolve()).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
            else:
                print(f"WARNING: Scene file not found: {scene_path}")

    # Use concat demuxer (no re-encode = fast)
    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", str(filelist_path),
        "-c", "copy",
    ]
    if overwrite:
        cmd.append("-y")

    # Try to use libx264 if copy fails (different codecs)
    try:
        print(f"Merging episode {episode} scenes...")
        result = subprocess.run(
            cmd + [output_file],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            print(f"Fast concat failed, trying re-encode...")
            print(f"ffmpeg stderr: {result.stderr[:500]}")
            # Fall back to re-encode with concat filter
            inputs = [str(Path(s["file"]).resolve()) for s in ep_scenes
                      if Path(s["file"]).exists()]
            filter_parts = []
            for i in range(len(inputs)):
                filter_parts.append(f"[{i}:v:0]")
            filter_str = "".join(filter_parts) + f"concat=n={len(inputs)}:v=1:a=0[outv]"

            cmd2 = [
                "ffmpeg",
            ]
            for inp in inputs:
                cmd2.extend(["-i", inp])
            cmd2.extend([
                "-filter_complex", filter_str,
                "-map", "[outv]",
                "-c:v", "libx264",
                "-preset", "medium",
                "-pix_fmt", "yuv420p",
            ])
            if overwrite:
                cmd2.append("-y")
            cmd2.append(output_file)

            result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=600)
            if result2.returncode != 0:
                print(f"Re-encode also failed: {result2.stderr[:500]}")
                return None

        file_size = os.path.getsize(output_file)
        print(f"Episode {episode} merged: {output_file} ({file_size / 1024 / 1024:.1f} MB)")
        return output_file

    except subprocess.TimeoutExpired:
        print(f"ffmpeg timed out for episode {episode}")
        return None
    finally:
        # Clean up temp filelist
        if filelist_path.exists():
            filelist_path.unlink()


def copy_to_scenes(project_name: str, scene_id: str, source_path: str) -> str:
    """Copy a generated video from ComfyUI output to project scenes dir."""
    paths = ensure_project_dirs(project_name)
    ext = Path(source_path).suffix if Path(source_path).suffix else ".mp4"
    dest = paths["scenes"] / f"{scene_id}{ext}"
    shutil.copy2(source_path, dest)
    print(f"Copied {source_path} -> {dest}")
    return str(dest)


def export_project(project_name: str, episode: int = 1,
                   dest: Optional[str] = None) -> Optional[str]:
    """Export a merged episode to the exports directory."""
    tl = load_timeline(project_name)
    ep = next((e for e in tl["episodes"] if e["number"] == episode), None)
    if not ep:
        print(f"Episode {episode} not found in timeline")
        return None

    episode_file = PROJECTS_DIR / project_name / "episodes" / f"ep{episode:03d}_merged.mp4"
    if not episode_file.exists():
        print(f"Episode file not found, merging first: {episode_file}")
        merge_episode(project_name, episode)

    if not episode_file.exists():
        return None

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if dest is None:
        dest = str(EXPORTS_DIR / f"{project_name}_ep{episode:03d}{episode_file.suffix}")

    shutil.copy2(str(episode_file), dest)
    print(f"Exported: {dest}")
    return dest


# ── Shot 排序合并（项目级入口）────────────────────────────

def sort_shots_by_order(shots: list) -> list:
    """按 act_number / scene_number / shot_number 排序 shot 列表。"""
    return sorted(shots, key=lambda s: (
        getattr(s, "act_number", 0),
        getattr(s, "scene_number", 0),
        getattr(s, "shot_number", 0),
        getattr(s, "id", 0),
    ))


def get_shot_video_path(project_name: str, shot_id: int) -> Optional[str]:
    """从 composed 或 scenes 目录查找 shot 视频。"""
    proj_dir = PROJECTS_DIR / project_name
    # 优先找已合成的（带字幕）
    composed = proj_dir / "composed" / f"shot_{shot_id:04d}_composed.mp4"
    if composed.exists():
        return str(composed)
    # 其次找原始渲染
    scenes = proj_dir / "scenes" / f"scene_{shot_id:04d}.mp4"
    if scenes.exists():
        return str(scenes)
    # glob 更宽松匹配
    for p in (proj_dir / "composed").glob(f"shot_{shot_id:04d}*"):
        return str(p)
    for p in (proj_dir / "scenes").glob(f"*{shot_id}*"):
        return str(p)
    return None


def merge_project(
    project_name: str,
    episode: int = 1,
    output_name: str = "",
    crossfade: float = 0.5,
    use_db_order: bool = True,
    project_id: int = 0,
) -> Optional[str]:
    """
    项目级合并：按 shot 顺序（DB 排序）找到所有视频，crossfade 合并为一集。
    """
    episode_dir = PROJECTS_DIR / project_name / "episodes"
    episode_dir.mkdir(parents=True, exist_ok=True)

    if not output_name:
        output_name = f"ep{episode:03d}_merged"
    output_file = str(episode_dir / f"{output_name}.mp4")

    # 尝试从 DB 获取有序 shot 列表
    if use_db_order and project_id:
        try:
            import sys
            sys.path.insert(0, str(BASE_DIR))
            import core.database as db
            shots = db.list_shots(project_id=project_id)
            shots = sort_shots_by_order(shots)
            video_paths = []
            for shot in shots:
                vp = get_shot_video_path(project_name, shot.id)
                if vp:
                    video_paths.append(vp)
        except Exception as e:
            print(f"[OutputManager] DB 排序失败，退回 timeline: {e}")
            video_paths = _get_videos_from_timeline(project_name, episode)
    else:
        video_paths = _get_videos_from_timeline(project_name, episode)

    if not video_paths:
        print(f"[OutputManager] 无视频文件可合并")
        return None

    print(f"[OutputManager] 合并 {len(video_paths)} 个视频 (crossfade={crossfade}s)...")

    if crossfade > 0:
        return _crossfade_concat(video_paths, output_file, crossfade)
    else:
        return _simple_ffmpeg_concat(video_paths, output_file)


def _get_videos_from_timeline(project_name: str, episode: int) -> list[str]:
    tl = load_timeline(project_name)
    ep_scenes = [s for s in tl["scenes"] if s.get("episode") == episode]
    ep_scenes.sort(key=lambda s: s.get("id", ""))
    return [s["file"] for s in ep_scenes if Path(s["file"]).exists()]


def _simple_ffmpeg_concat(videos: list[str], output_file: str) -> Optional[str]:
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for v in videos:
            escaped = str(Path(v).resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
        filelist = f.name

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", filelist, "-c", "copy", output_file],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode == 0:
            return output_file
        # 回退 re-encode
        inputs_flat = []
        for v in videos:
            inputs_flat += ["-i", v]
        n = len(videos)
        filter_str = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"
        result2 = subprocess.run(
            ["ffmpeg", "-y"] + inputs_flat + [
                "-filter_complex", filter_str,
                "-map", "[outv]",
                "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
                output_file
            ],
            capture_output=True, text=True, timeout=600
        )
        return output_file if result2.returncode == 0 else None
    finally:
        Path(filelist).unlink(missing_ok=True)


def _crossfade_concat(videos: list[str], output_file: str, cf: float) -> Optional[str]:
    """带 crossfade 过渡的视频拼接（逐对合并）。"""
    import tempfile

    def _duration(path):
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=10
            )
            return float(r.stdout.strip())
        except Exception:
            return 5.0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        current = videos[0]
        for i, next_v in enumerate(videos[1:], 1):
            dur = _duration(current)
            offset = max(dur - cf, 0)
            out = str(tmp / f"stage_{i}.mp4")
            result = subprocess.run(
                ["ffmpeg", "-y",
                 "-i", current, "-i", next_v,
                 "-filter_complex",
                 f"[0:v][1:v]xfade=transition=fade:duration={cf}:offset={offset}[outv]",
                 "-map", "[outv]",
                 "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                 out],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                print(f"[OutputManager] crossfade 失败，回退简单拼接")
                return _simple_ffmpeg_concat(videos, output_file)
            current = out

        import shutil
        shutil.copy2(current, output_file)

    return output_file if Path(output_file).exists() else None


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "init":
        project = sys.argv[2] if len(sys.argv) > 2 else "demo"
        paths = ensure_project_dirs(project)
        print(f"Initialized project '{project}':")
        for k, v in paths.items():
            print(f"  {k}: {v}")

    elif cmd == "merge":
        project = sys.argv[2] if len(sys.argv) > 2 else "demo"
        ep = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        merge_episode(project, ep, overwrite=True)

    elif cmd == "export":
        project = sys.argv[2] if len(sys.argv) > 2 else "demo"
        ep = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        export_project(project, ep)

    elif cmd == "status":
        project = sys.argv[2] if len(sys.argv) > 2 else "demo"
        tl = load_timeline(project)
        print(f"Project: {project}")
        print(f"Scenes: {len(tl['scenes'])}")
        for s in tl["scenes"]:
            exists = "✓" if Path(s["file"]).exists() else "✗"
            print(f"  [{exists}] {s['id']} ({s['duration']}s, ep{s['episode']})")
        print(f"Episodes: {len(tl['episodes'])}")
        for e in tl["episodes"]:
            merged = PROJECTS_DIR / project / "episodes" / f"ep{e['number']:03d}_merged.mp4"
            m = "✓ merged" if merged.exists() else "✗ not merged"
            print(f"  Ep {e['number']}: {e['total_duration']:.1f}s, {len(e['scenes'])} scenes, {m}")

    else:
        print("Usage: python3 output_manager.py <command> [project] [episode]")
        print("Commands: init | merge | export | status")
