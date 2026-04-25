#!/usr/bin/env python3
"""
Video output management: organize, merge, and export video clips.

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
