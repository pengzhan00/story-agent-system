"""
Batch Renderer — 批量场景渲染管线
从数据库读取剧本场景 → 逐个渲染 → 收集视频 → 输出管理
"""
import json
import os
import time
import subprocess
from pathlib import Path
from typing import Optional, Callable

# ─── 路径配置 ──────────────────────────────────────────

import sys
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.animate_pipeline import submit_workflow, wait_for_completion
from pipelines.output_manager import (
    ensure_project_dirs, register_scene, load_timeline, save_timeline
)

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

    def get_latest_video(self, prefix: str = "story_anim") -> Optional[str]:
        """从 ComfyUI output 目录找到最新的视频文件"""
        if not COMFYUI_OUTPUT_DIR.exists():
            return None
        videos = list(COMFYUI_OUTPUT_DIR.glob(f"{prefix}*.mp4"))
        if not videos:
            return None
        latest = max(videos, key=os.path.getmtime)
        return str(latest)

    def build_prompt_from_scene(self, scene: dict) -> str:
        """从场景数据构建 ComfyUI prompt"""
        location = scene.get("location", "未知场景")
        mood = scene.get("mood", "平静")
        weather = scene.get("weather", "晴")
        time_of_day = scene.get("time_of_day", "白天")
        narration = scene.get("narration", "")
        characters = scene.get("characters", [])

        # 从演员描述构建 prompt
        char_desc = ", ".join(characters) if characters else "一位角色"

        # 主 prompt
        prompt = (
            f"anime style, {location}, {weather} weather, {time_of_day}, "
            f"{mood} atmosphere, {char_desc}, {narration}, "
            f"cinematic lighting, detailed background, story illustration style, "
            f"high quality, 2D anime art style"
        )
        return prompt

    def render_scene(self, scene: dict, scene_id: str = "") -> Optional[str]:
        """渲染单个场景"""
        import requests as req

        if not scene_id:
            scene_id = scene.get("location", f"scene_{int(time.time())}")

        prompt_text = self.build_prompt_from_scene(scene)

        # 构建 ComfyUI 工作流
        from pipelines.animate_pipeline import WORKFLOW_FILE
        workflow = json.loads(WORKFLOW_FILE.read_text())

        # 注入 prompt
        for node_id, node in workflow.items():
            if node.get("class_type") == "CLIPTextEncode":
                input_key = node.get("_meta", {}).get("input_key", "text")
                # 第一个 CLIPTextEncode 是 positive
                if node.get("_is_positive", False) or (
                    "positive" not in str(node_id) and
                    workflow.get(node_id, {}).get("inputs", {}).get("text", "").startswith("positive")
                ):
                    # 这个判断不靠谱，更好的方法：找连接到 KSampler positive 的那个
                    pass

        # 更可靠的方案：按节点 ID 约定
        # 我们约定节点 3 = positive prompt, 节点 4 = negative prompt
        if "3" in workflow and workflow["3"].get("class_type") == "CLIPTextEncode":
            workflow["3"]["inputs"]["text"] = prompt_text
        if "8" in workflow and workflow["8"].get("class_type") == "KSampler":
            workflow["8"]["inputs"]["seed"] = int(time.time())  # 随机种子

        # 提交
        prompt_id = submit_workflow(workflow)
        if not prompt_id:
            return None

        # 等待完成
        outputs = wait_for_completion(prompt_id, timeout=300)
        if not outputs:
            return None

        # 找生成的视频
        video_path = self.get_latest_video()
        if not video_path:
            return None

        # 复制到项目输出目录
        dest = self.project_dirs["scenes"] / f"{scene_id}.mp4"
        import shutil
        shutil.copy2(video_path, dest)
        self.results.append(str(dest))

        return str(dest)

    def render_multi_scene(self, scenes: list[dict]) -> list[str]:
        """批量渲染多个场景"""
        self.results = []
        from core.database import add_prompt_log

        if not self.check_comfyui():
            if self.project_id:
                add_prompt_log(self.project_id, "batch_renderer", "error",
                              "ComfyUI 不可用", "ComfyUI not reachable")
            return []

        for idx, scene in enumerate(scenes):
            pct = idx / max(len(scenes), 1)
            self._progress(f"渲染 {idx+1}/{len(scenes)}: {scene.get('location','')}", pct)
            self.render_scene(scene, scene_id=f"scene_{idx+1:03d}")

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
