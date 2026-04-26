"""
Audio Pipeline — TTS + 音乐生成 + 音效生成
支持: Edge-TTS（免费在线）/ Kokoro（本地）/ OpenAI TTS（API）
     HeartMuLa / audiocraft（本地音乐/音效）
"""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional
import sys

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.database as db

OUTPUT_DIR = PROJECT_ROOT / "output"

# ── 可用 TTS 后端优先级 ────────────────────────────────────

def _check_edge_tts() -> bool:
    try:
        import edge_tts
        return True
    except ImportError:
        return False


def _check_kokoro() -> bool:
    try:
        import kokoro
        return True
    except ImportError:
        return False


def _pick_tts_backend() -> str:
    if _check_edge_tts():
        return "edge_tts"
    if _check_kokoro():
        return "kokoro"
    return "pyttsx3"


# ── Edge-TTS ──────────────────────────────────────────────

async def _edge_tts_generate(text: str, voice: str, output_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def generate_tts_edge(text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> bool:
    try:
        asyncio.run(_edge_tts_generate(text, voice, output_path))
        return Path(output_path).exists()
    except Exception as e:
        print(f"[EdgeTTS] 失败: {e}")
        return False


# ── Kokoro TTS ────────────────────────────────────────────

def generate_tts_kokoro(text: str, output_path: str, voice: str = "af_heart") -> bool:
    try:
        from kokoro import KPipeline
        import soundfile as sf
        import numpy as np

        pipeline = KPipeline(lang_code="z")
        samples, sample_rate = [], 24000
        for _, _, audio in pipeline(text, voice=voice):
            samples.append(audio)
        if samples:
            audio_data = np.concatenate(samples)
            sf.write(output_path, audio_data, sample_rate)
            return True
        return False
    except Exception as e:
        print(f"[Kokoro] 失败: {e}")
        return False


# ── pyttsx3 回退 ──────────────────────────────────────────

def generate_tts_pyttsx3(text: str, output_path: str) -> bool:
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.save_to_file(text, output_path)
        engine.runAndWait()
        return Path(output_path).exists()
    except Exception as e:
        print(f"[pyttsx3] 失败: {e}")
        return False


# ── 统一 TTS 接口 ──────────────────────────────────────────

_VOICE_MAP = {
    "男": "zh-CN-YunxiNeural",
    "女": "zh-CN-XiaoxiaoNeural",
    "男孩": "zh-CN-YunjianNeural",
    "女孩": "zh-CN-XiaoyiNeural",
    "旁白": "zh-CN-YunyangNeural",
    "default": "zh-CN-XiaoxiaoNeural",
}


def get_voice_for_character(character_name: str, project_id: int) -> str:
    chars = db.list_characters(project_id)
    char = next((c for c in chars if c.name == character_name), None)
    if char:
        profile = (char.voice_profile or "").lower()
        for key, voice in _VOICE_MAP.items():
            if key in profile:
                return voice
        # 通过性别判断
        if char.gender in ("男", "男性"):
            return _VOICE_MAP["男"]
        if char.gender in ("女", "女性"):
            return _VOICE_MAP["女"]
    return _VOICE_MAP["default"]


def generate_tts(
    text: str,
    output_path: str,
    voice: str = "",
    backend: str = "",
) -> bool:
    if not backend:
        backend = _pick_tts_backend()
    if not voice:
        voice = _VOICE_MAP["default"]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if backend == "edge_tts":
        return generate_tts_edge(text, output_path, voice)
    elif backend == "kokoro":
        return generate_tts_kokoro(text, output_path)
    else:
        return generate_tts_pyttsx3(text, output_path)


# ── Shot TTS 批量生成 ──────────────────────────────────────

def generate_shot_tts(
    project_id: int,
    shot_id: int,
    output_dir: Path,
    backend: str = "",
) -> list[dict]:
    """
    为单个 shot 的所有对白生成 TTS 音频。
    返回生成的音频文件列表。
    """
    shot = db.get_shot(shot_id)
    if not shot:
        return []

    try:
        dialogue_list = json.loads(shot.dialogue) if shot.dialogue else []
    except Exception:
        dialogue_list = []

    results = []
    shot_audio_dir = output_dir / f"shot_{shot_id:04d}" / "tts"
    shot_audio_dir.mkdir(parents=True, exist_ok=True)

    for idx, line in enumerate(dialogue_list):
        if not isinstance(line, dict):
            continue
        text = line.get("line", "").strip()
        character = line.get("character", "旁白")
        if not text:
            continue

        voice = get_voice_for_character(character, project_id)
        out_path = str(shot_audio_dir / f"line_{idx:03d}_{character}.mp3")

        success = generate_tts(text, out_path, voice=voice, backend=backend)
        if success:
            duration = _get_audio_duration(out_path)
            results.append({
                "line_idx": idx,
                "character": character,
                "text": text,
                "file": out_path,
                "duration": duration,
            })
            db.create_audio_asset({
                "project_id": project_id,
                "shot_id": shot_id,
                "asset_type": "tts",
                "file_path": out_path,
                "duration_sec": duration,
                "metadata": json.dumps({"character": character, "line_idx": idx}, ensure_ascii=False),
                "created_at": _now(),
            })

    return results


# ── 音乐生成 ──────────────────────────────────────────────

def generate_music_heartmula(prompt: str, output_path: str, duration: int = 30) -> bool:
    """
    尝试调用本地 HeartMuLa 服务生成音乐。
    HeartMuLa 通常监听 http://127.0.0.1:7860 或类似端口。
    """
    import requests
    endpoints = [
        "http://127.0.0.1:7861/generate",
        "http://127.0.0.1:7862/generate",
        "http://127.0.0.1:8080/generate",
    ]
    for url in endpoints:
        try:
            r = requests.post(url, json={"prompt": prompt, "duration": duration}, timeout=120)
            if r.status_code == 200:
                data = r.json()
                audio_url = data.get("audio_url") or data.get("file")
                if audio_url:
                    audio_r = requests.get(audio_url, timeout=30)
                    Path(output_path).write_bytes(audio_r.content)
                    return True
        except Exception:
            continue
    return False


def generate_music_audiocraft(prompt: str, output_path: str, duration: int = 30) -> bool:
    """使用 audiocraft/MusicGen CLI 生成音乐（如果已安装）。"""
    try:
        cmd = [
            "python", "-m", "audiocraft.generate",
            "--model", "musicgen-small",
            "--text", prompt,
            "--duration", str(duration),
            "--output", output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return result.returncode == 0 and Path(output_path).exists()
    except Exception as e:
        print(f"[audiocraft] 失败: {e}")
        return False


def generate_music(
    prompt: str,
    output_path: str,
    duration: int = 30,
    project_id: int = 0,
    music_id: int = 0,
) -> bool:
    """统一音乐生成接口，按优先级尝试各后端。"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if generate_music_heartmula(prompt, output_path, duration):
        _register_audio(project_id, 0, "music", output_path, music_id)
        return True

    if generate_music_audiocraft(prompt, output_path, duration):
        _register_audio(project_id, 0, "music", output_path, music_id)
        return True

    print(f"[AudioPipeline] 音乐生成失败（无可用后端）: {prompt[:60]}")
    return False


def generate_project_music(project_id: int, output_dir: Path) -> list[dict]:
    """为项目所有音乐主题生成音频文件。"""
    music_list = db.list_music(project_id)
    music_dir = output_dir / "music"
    music_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for m in music_list:
        if m.file_path and Path(m.file_path).exists():
            results.append({"id": m.id, "name": m.name, "file": m.file_path, "skipped": True})
            continue
        prompt = m.prompt_for_gen or f"{m.mood} {m.tempo} {m.instruments} {m.description}"
        out_path = str(music_dir / f"music_{m.id}_{m.name[:20]}.mp3")
        success = generate_music(prompt, out_path, project_id=project_id, music_id=m.id)
        if success:
            db.update_music(m.id, {"file_path": out_path})
        results.append({"id": m.id, "name": m.name, "file": out_path if success else "", "success": success})
    return results


# ── 音效生成 ──────────────────────────────────────────────

def generate_sfx_audiocraft(description: str, output_path: str, duration: int = 5) -> bool:
    """使用 audiocraft AudioGen 生成音效。"""
    try:
        cmd = [
            "python", "-m", "audiocraft.generate",
            "--model", "audiogen-medium",
            "--text", description,
            "--duration", str(duration),
            "--output", output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0 and Path(output_path).exists()
    except Exception as e:
        print(f"[audiocraft-sfx] 失败: {e}")
        return False


def generate_project_sfx(project_id: int, output_dir: Path) -> list[dict]:
    """为项目所有音效生成音频文件。"""
    sfx_list = db.list_sfx(project_id)
    sfx_dir = output_dir / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for sfx in sfx_list:
        if sfx.file_path and Path(sfx.file_path).exists():
            results.append({"id": sfx.id, "name": sfx.name, "skipped": True})
            continue
        out_path = str(sfx_dir / f"sfx_{sfx.id}_{sfx.name[:20]}.mp3")
        desc = sfx.description or f"{sfx.category} {sfx.name}"
        success = generate_sfx_audiocraft(desc, out_path)
        if success:
            db.update_sfx(sfx.id, {"file_path": out_path})
        results.append({"id": sfx.id, "name": sfx.name, "file": out_path if success else "", "success": success})
    return results


# ── 完整音频管线（项目级） ─────────────────────────────────

def run_audio_pipeline(
    project_id: int,
    progress_fn=None,
) -> dict:
    """
    为整个项目跑完所有音频生成任务。
    progress_fn: callable(msg: str, pct: float)
    """
    def _progress(msg, pct=0.0):
        print(f"[AudioPipeline] {msg}")
        if progress_fn:
            progress_fn(msg, pct)

    proj = db.get_project(project_id)
    if not proj:
        return {"success": False, "error": f"项目 {project_id} 不存在"}

    out_dir = OUTPUT_DIR / "projects" / proj.name / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {"tts": [], "music": [], "sfx": []}

    # TTS
    shots = db.list_shots(project_id=project_id)
    _progress(f"生成 TTS：{len(shots)} 个 shot", 0.0)
    for i, shot in enumerate(shots):
        pct = 0.1 + 0.4 * i / max(len(shots), 1)
        _progress(f"TTS shot {shot.id} ({i+1}/{len(shots)})", pct)
        tts_results = generate_shot_tts(project_id, shot.id, out_dir)
        results["tts"].extend(tts_results)

    # 音乐
    _progress("生成音乐主题...", 0.5)
    results["music"] = generate_project_music(project_id, out_dir)
    _progress(f"音乐完成: {sum(1 for m in results['music'] if m.get('success'))}/{len(results['music'])}", 0.7)

    # 音效
    _progress("生成音效...", 0.75)
    results["sfx"] = generate_project_sfx(project_id, out_dir)
    _progress(f"音效完成: {sum(1 for s in results['sfx'] if s.get('success'))}/{len(results['sfx'])}", 0.9)

    _progress("音频管线完成", 1.0)
    return {"success": True, "project_id": project_id, **results}


# ── 工具函数 ──────────────────────────────────────────────

def _get_audio_duration(path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _register_audio(project_id: int, shot_id: int, asset_type: str, file_path: str, ref_id: int = 0):
    duration = _get_audio_duration(file_path)
    db.create_audio_asset({
        "project_id": project_id,
        "shot_id": shot_id,
        "asset_type": asset_type,
        "file_path": file_path,
        "duration_sec": duration,
        "metadata": json.dumps({"ref_id": ref_id}, ensure_ascii=False),
        "created_at": _now(),
    })


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
