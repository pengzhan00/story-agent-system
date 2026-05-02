"""
Batch Renderer — 批量场景渲染管线
从数据库读取剧本场景 → 并行渲染队列 → 重试 → 收集视频 → 输出管理
"""
import json
import os
import time
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Callable

# ─── 路径配置 ──────────────────────────────────────────

import sys
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.render_pipeline import (
    build_scene_prompt,
    generate_scene_video,
    get_video_output,
)
from pipelines.output_manager import (
    ensure_project_dirs, register_scene, load_timeline, save_timeline
)

RENDER_TIMEOUT = 7200  # 2小时超时
COMFYUI_URL = "http://127.0.0.1:8188"
OUTPUT_DIR = PROJECT_ROOT / "output"
COMFYUI_OUTPUT_DIR = Path(os.path.expanduser("~/Documents/ComfyUI/output"))


class BatchRenderer:
    """批量场景渲染器"""

    def __init__(self, project_name: str, project_id: int = 0):
        self.project_name = project_name
        self.project_id = project_id
        self.project_dirs = ensure_project_dirs(project_name)
        self.results = []
        self._progress_callback: Optional[Callable[[str, float], None]] = None

    def set_progress_callback(self, fn: Callable[[str, float], None]):
        self._progress_callback = fn

    def _progress(self, msg: str, pct: float):
        if self._progress_callback:
            self._progress_callback(msg, pct)

    def check_comfyui(self) -> bool:
        """检查 ComfyUI 是否可访问"""
        import requests
        try:
            r = requests.get(f"{COMFYUI_URL}/queue", timeout=5)
            return r.status_code == 200
        except:
            return False

    def wait_for_queue_empty(self, timeout: int = 300, poll_interval: int = 5):
        """等待 ComfyUI 队列清空"""
        import requests
        start = time.time()
        while time.time() - start < timeout:
            try:
                r = requests.get(f"{COMFYUI_URL}/queue", timeout=5)
                q = r.json()
                if not q.get("queue_running") and not q.get("queue_pending"):
                    return True
                time.sleep(poll_interval)
            except:
                time.sleep(poll_interval)
        return False

    def _extend_static_frame(self, video_path: str, duration_sec: float = 3.0) -> Optional[str]:
        """用 ffmpeg 将单帧（或短帧）视频延长到指定秒数。返回临时文件路径，失败返回 None。"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True, timeout=10,
            )
            current_dur = float(result.stdout.strip() or "0")
        except Exception:
            current_dur = 0.0

        if current_dur >= duration_sec - 0.1:
            return video_path  # 已够长，不用处理

        tmp = str(Path(video_path).with_suffix(".extended.mp4"))
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path,
                 "-vf", f"tpad=stop_mode=clone:stop_duration={duration_sec}",
                 "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                 "-an", tmp],
                capture_output=True, timeout=60,
            )
            if Path(tmp).exists() and Path(tmp).stat().st_size > 1000:
                return tmp
        except Exception:
            pass
        return None

    def get_latest_video(self, prefix: str = "story_anim") -> Optional[str]:
        """从 ComfyUI output 目录找到最新的视频文件（向后兼容）"""
        if not COMFYUI_OUTPUT_DIR.exists():
            return None
        videos = list(COMFYUI_OUTPUT_DIR.glob(f"{prefix}*.mp4"))
        if not videos:
            return None
        latest = max(videos, key=os.path.getmtime)
        return str(latest)

    def build_prompt_from_scene(self, scene: dict) -> str:
        """从统一 render_payload 构建 ComfyUI prompt"""
        return build_scene_prompt(scene)

    def _find_video_from_outputs(self, outputs: dict) -> Optional[str]:
        """从 ComfyUI history outputs 中解析真实视频文件路径（不像 glob 那样靠猜）"""
        files = get_video_output(outputs)
        if not files:
            self._progress(f"  ComfyUI history 无视频记录", 0)
            return None
        vf = files[0]
        filename = vf["filename"]
        subfolder = vf.get("subfolder", "")
        file_type = vf.get("type", "output")
        if subfolder:
            video_path = COMFYUI_OUTPUT_DIR / subfolder / filename
        else:
            video_path = COMFYUI_OUTPUT_DIR / filename
        if video_path.exists():
            return str(video_path)
        self._progress(f"  文件 {video_path} 不存在（可能被清理）", 0)
        return None

    def render_scene(self, scene: dict, scene_id: str = "",
                     timeout: int = RENDER_TIMEOUT) -> Optional[str]:
        """渲染单个场景，等待完成后通过 history 精确匹配输出文件。
        渲染前先查 asset_registry，已完成则直接返回已有路径。
        """
        from core.database import create_render_job, update_render_job, update_shot, create_asset_version
        from pipelines.quality_gate import validate_render_output

        if not scene_id:
            scene_id = scene.get("location", f"scene_{int(time.time())}")
        shot_id = scene.get("shot_id", 0)

        # ── 复用检查：shot 已渲染且视频存在，直接返回 ──────────
        if shot_id and self.project_id:
            from core.asset_registry import get_shot_video, is_shot_rendered
            if is_shot_rendered(shot_id):
                existing = get_shot_video(self.project_id, shot_id, self.project_name)
                if existing:
                    self._progress(f"  ♻️  shot {shot_id} 已渲染，复用 {Path(existing).name}", 0)
                    if existing not in self.results:
                        self.results.append(existing)
                    return existing

        render_job_id = create_render_job({
            "project_id": self.project_id,
            "shot_id": shot_id,
            "status": "running",
            "workflow_name": "dispatcher",
            "requested_pipeline": "",
            "used_pipeline": "",
            "fallback_used": 0,
            "fallback_from": "",
            "render_tier": "production",
        }) if self.project_id and shot_id else 0

        prompt_text = self.build_prompt_from_scene(scene)
        if shot_id:
            update_shot(shot_id, {"status": "rendering"})
        dest = self.project_dirs["scenes"] / f"{scene_id}.mp4"
        scene["project_name"] = self.project_name
        scene.setdefault("scene_id", scene_id)

        result = generate_scene_video(
            scene,
            project_name=self.project_name,
            seed=int(time.time()) % (2**31),
            lora_refs=scene.get("_lora_refs"),
            controlnet_type=scene.get("controlnet_type"),
            controlnet_strength=float(scene.get("controlnet_strength", 0.6)),
            fixed_seed=False,
        )
        if not result.get("success") or not result.get("output_path"):
            if render_job_id:
                update_render_job(render_job_id, {
                    "status": "failed",
                    "error": result.get("error", "渲染失败"),
                    "requested_pipeline": result.get("requested_pipeline", ""),
                    "used_pipeline": result.get("pipeline_name", ""),
                    "fallback_used": int(bool(result.get("fallback_used"))),
                    "fallback_from": result.get("fallback_from", ""),
                    "render_tier": result.get("render_tier", "production"),
                })
            if shot_id:
                update_shot(shot_id, {"status": "ready"})
            return None

        src_path = Path(result["output_path"])
        if src_path != dest and src_path.exists():
            import shutil
            shutil.copy2(src_path, dest)
        elif src_path == dest:
            dest = src_path
        else:
            if render_job_id:
                update_render_job(render_job_id, {"status": "failed", "error": "输出文件不存在"})
            if shot_id:
                update_shot(shot_id, {"status": "ready"})
            return None

        self.results.append(str(dest))
        qc = validate_render_output(str(dest))
        if not qc.passed:
            qc_error = "; ".join(qc.errors)[:500]
            if render_job_id:
                update_render_job(render_job_id, {
                    "status": "failed",
                    "error": f"质检失败: {qc_error}",
                    "output_path": str(dest),
                    "requested_pipeline": result.get("requested_pipeline", ""),
                    "used_pipeline": result.get("pipeline_name", ""),
                    "fallback_used": int(bool(result.get("fallback_used"))),
                    "fallback_from": result.get("fallback_from", ""),
                    "render_tier": result.get("render_tier", "production"),
                    "output_meta": {
                        "seed": result.get("seed", 0),
                        "files": result.get("files", []),
                        "quality_gate": qc.metrics,
                    },
                })
            if shot_id:
                update_shot(shot_id, {"status": "qc_failed", "error": qc_error})
            return None
        if render_job_id:
            update_render_job(render_job_id, {
                "status": "completed",
                "output_path": str(dest),
                "requested_pipeline": result.get("requested_pipeline", ""),
                "used_pipeline": result.get("pipeline_name", ""),
                "fallback_used": int(bool(result.get("fallback_used"))),
                "fallback_from": result.get("fallback_from", ""),
                "render_tier": result.get("render_tier", "production"),
                "output_meta": {
                    "seed": result.get("seed", 0),
                    "files": result.get("files", []),
                    "quality_gate": qc.metrics,
                },
            })
            create_asset_version({
                "project_id": self.project_id,
                "shot_id": shot_id,
                "asset_type": "render",
                "asset_ref_id": render_job_id,
                "source_stage": "render_pipeline",
                "file_path": str(dest),
                "content_json": {
                    "output_path": str(dest),
                    "requested_pipeline": result.get("requested_pipeline", ""),
                    "used_pipeline": result.get("pipeline_name", ""),
                    "fallback_used": bool(result.get("fallback_used")),
                    "render_tier": result.get("render_tier", "production"),
                    "seed": result.get("seed", 0),
                },
                "notes": "render completed",
            })
        if shot_id:
            update_shot(shot_id, {"status": "rendered"})
        register_scene(
            self.project_name,
            scene_id=scene_id,
            video_file=str(dest),
            duration_sec=2.0,
            episode=scene.get("episode_number", 1),
            prompt=prompt_text,
        )

        return str(dest)

    def render_scene_with_retry(
        self,
        scene: dict,
        scene_id: str = "",
        timeout: int = RENDER_TIMEOUT,
        max_retries: int = 2,
    ) -> Optional[str]:
        """渲染单个场景，失败时自动重试 max_retries 次。"""
        for attempt in range(max_retries + 1):
            if attempt > 0:
                self._progress(f"  🔄 重试 {attempt}/{max_retries}...", 0)
                time.sleep(5)
            result = self.render_scene(scene, scene_id=scene_id, timeout=timeout)
            if result:
                return result
        return None

    def render_multi_scene(
        self,
        scenes: list[dict],
        start_index: int = 0,
        max_workers: int = 1,
        max_retries: int = 2,
    ) -> list[str]:
        """
        批量渲染多个场景，支持从指定索引继续 + 并行 + 自动重试。
        max_workers=1: 串行（ComfyUI 单实例推荐）
        max_workers>1: 并行（需要多 ComfyUI 实例）
        """
        self.results = []
        self._results_lock = threading.Lock()
        from core.database import add_prompt_log

        if not self.check_comfyui():
            if self.project_id:
                add_prompt_log(self.project_id, "batch_renderer", "error",
                              "ComfyUI 不可用", "ComfyUI not reachable")
            return []

        total = len(scenes)
        if start_index > 0:
            self._progress(f"🔄 从第 {start_index+1}/{total} 个场景继续",
                           start_index / max(total, 1))

        pending = [
            (idx, scenes[idx], scenes[idx].get("scene_id") or f"scene_{idx+1:03d}")
            for idx in range(start_index, total)
        ]

        if max_workers <= 1:
            for idx, scene, scene_id in pending:
                pct = idx / max(total, 1)
                shot_label = scene.get("location", scene.get("scene_asset", {}).get("name", ""))
                self._progress(f"🎬 渲染 {idx+1}/{total}: {shot_label}", pct)
                result = self.render_scene_with_retry(
                    scene, scene_id=scene_id,
                    timeout=RENDER_TIMEOUT, max_retries=max_retries
                )
                if result:
                    self.results.append(result)
        else:
            completed_count = [0]
            lock = threading.Lock()

            def _worker(item):
                idx, scene, scene_id = item
                shot_label = scene.get("location", "")
                result = self.render_scene_with_retry(
                    scene, scene_id=scene_id,
                    timeout=RENDER_TIMEOUT, max_retries=max_retries
                )
                with lock:
                    completed_count[0] += 1
                    pct = completed_count[0] / max(total, 1)
                    status = "✅" if result else "❌"
                    self._progress(f"🎬 [{completed_count[0]}/{total}] {shot_label} {status}", pct)
                return result

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_worker, item) for item in pending]
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        with self._results_lock:
                            self.results.append(result)

        return self.results

    def merge_episode(self, episode: int = 1, output_name: str = "") -> Optional[str]:
        """合并当前批次为单集视频"""
        if not self.results:
            return None
        if not output_name:
            output_name = f"{self.project_name}_EP{episode:02d}"

        from pipelines.output_manager import merge_project
        return merge_project(self.project_name, episode=episode, output_name=output_name)


# ─── 简易调试点 ────────────────────────────────────────

if __name__ == "__main__":
    print("Batch Renderer — 测试模式")
    test_scenes = [
        {"location": "古老森林", "mood": "神秘", "weather": "薄雾", 
         "time_of_day": "黄昏", "narration": "主角独自走在森林中",
         "characters": ["小明"]},
    ]
    renderer = BatchRenderer("测试项目")
    print(f"ComfyUI 状态: {'✅ 在线' if renderer.check_comfyui() else '❌ 离线'}")
    print(f"场景 prompt: {renderer.build_prompt_from_scene(test_scenes[0])}")
