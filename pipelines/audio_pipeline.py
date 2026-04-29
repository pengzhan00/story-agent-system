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

BARK_PYTHON = Path(os.path.expanduser("~/bark/venv/bin/python3"))
CHATTTS_PYTHON = Path(os.path.expanduser("~/ChatTTS/venv/bin/python3"))
CHATTTS_MODEL_PATH = Path(os.path.expanduser("~/asset/ms/AI-ModelScope/ChatTTS"))


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


def _check_bark() -> bool:
    """Bark venv 存在 且 模型权重已完整下载（无 .incomplete 文件）。"""
    if not BARK_PYTHON.exists():
        return False
    bark_cache = Path.home() / ".cache" / "huggingface" / "hub" / "models--suno--bark"
    if not bark_cache.exists():
        return False
    if list(bark_cache.glob("**/*.incomplete")):
        return False          # 有未完成的分片
    model_files = list(bark_cache.glob("**/*.bin")) + list(bark_cache.glob("**/*.pt"))
    return len(model_files) > 0


def _check_chattts() -> bool:
    return CHATTTS_PYTHON.exists() and (CHATTTS_MODEL_PATH / "asset" / "gpt").exists()


def _pick_tts_backend() -> str:
    """按优先级返回第一个可用后端。"""
    # ChatTTS 优先：原生中文，效果最好
    if _check_chattts():
        return "chattts"
    if _check_edge_tts():     # 在线服务，比 Bark 更可靠
        return "edge_tts"
    if _check_bark():
        return "bark"
    if _check_kokoro():
        return "kokoro"
    return "pyttsx3"


def _ranked_tts_backends() -> list[str]:
    """返回所有可用后端，按优先级排列，用于回退链。"""
    order = ["chattts", "edge_tts", "bark", "kokoro", "pyttsx3"]
    checks = {
        "chattts": _check_chattts,
        "edge_tts": _check_edge_tts,
        "bark": _check_bark,
        "kokoro": _check_kokoro,
        "pyttsx3": lambda: True,
    }
    return [b for b in order if checks[b]()]


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


# ── Bark TTS（~/bark/venv 独立环境）──────────────────────

def generate_tts_bark(text: str, output_path: str, voice_preset: str = "v2/zh_speaker_0") -> bool:
    """通过子进程调用 ~/bark/venv 中的 Bark 生成语音（MPS 加速）。"""
    if not BARK_PYTHON.exists():
        return False
    script = f"""
import os, sys, numpy as np
os.environ['SUNO_ENABLE_MPS'] = '1'
os.environ['SUNO_OFFLOAD_CPU'] = '0'
from bark.generation import preload_models
preload_models(text_use_gpu=True, coarse_use_gpu=True, fine_use_gpu=True, codec_use_gpu=True)
from bark import generate_audio, SAMPLE_RATE
from scipy.io.wavfile import write as wav_write
audio = generate_audio({repr(text)}, history_prompt={repr(voice_preset)})
audio_int = (audio * 32767).astype(np.int16)
wav_write({repr(str(Path(output_path).with_suffix('.wav')))}, SAMPLE_RATE, audio_int)
print('OK', len(audio) / SAMPLE_RATE)
"""
    wav_path = str(Path(output_path).with_suffix('.wav'))
    try:
        result = subprocess.run(
            [str(BARK_PYTHON), "-c", script],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"[Bark] 失败: {result.stderr[-300:]}")
            return False
        if not Path(wav_path).exists():
            return False
        # wav → mp3
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libmp3lame", "-b:a", "128k", output_path],
            capture_output=True, timeout=30,
        )
        Path(wav_path).unlink(missing_ok=True)
        return Path(output_path).exists()
    except Exception as e:
        print(f"[Bark] 异常: {e}")
        return False


# ── ChatTTS（~/ChatTTS/venv 独立环境，原生中文）────────────

# ChatTTS 使用固定种子保持音色稳定；不同 seed 对应不同音色
# seed 2  → 男声；seed 42 → 女声（实测，勿反）
_CHATTTS_VOICE_SEEDS = {
    "男": 2,
    "男性": 2,
    "男孩": 2222,
    "少年": 2222,
    "老人": 6666,
    "长者": 6666,
    "反派": 888,
    "女": 42,
    "女性": 42,
    "女孩": 5555,
    "少女": 5555,
    "温柔": 4444,
    "旁白": 9999,
    "解说": 9999,
    "narrator": 9999,
    "default": 42,
}


def generate_tts_chattts(text: str, output_path: str, voice_seed: int = 42) -> bool:
    """通过子进程调用 ~/chattts/venv 中的 ChatTTS 生成中文语音（CPU，MPS 不可用）。"""
    if not CHATTTS_PYTHON.exists():
        return False
    wav_path = str(Path(output_path).with_suffix('.wav'))
    script = f"""
import os, sys
import numpy as np
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
import torch
import ChatTTS
from scipy.io.wavfile import write as wav_write

chat = ChatTTS.Chat()
chat.load(source='custom', custom_path={repr(str(CHATTTS_MODEL_PATH))}, compile=False)

torch.manual_seed({voice_seed})
rand_spk = chat.sample_random_speaker()
params_infer = ChatTTS.Chat.InferCodeParams(spk_emb=rand_spk, temperature=0.0003, top_P=0.7, top_K=20)
params_refine = ChatTTS.Chat.RefineTextParams(prompt='[oral_2][laugh_0][break_6]')
wavs = chat.infer([{repr(text)}], params_infer_code=params_infer, params_refine_text=params_refine, split_text=False)
if not wavs:
    sys.exit(1)
wav = np.array(wavs[0], dtype=np.float32)
wav_int = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16)
wav_write({repr(wav_path)}, 24000, wav_int)
print('OK', len(wav) / 24000)
"""
    try:
        result = subprocess.run(
            [str(CHATTTS_PYTHON), "-c", script],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            print(f"[ChatTTS] 失败: {result.stderr[-500:]}")
            return False
        if not Path(wav_path).exists():
            return False
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libmp3lame", "-b:a", "128k", output_path],
            capture_output=True, timeout=30,
        )
        Path(wav_path).unlink(missing_ok=True)
        return Path(output_path).exists()
    except Exception as e:
        print(f"[ChatTTS] 异常: {e}")
        return False


def get_voice_seed_chattts(character_name: str, project_id: int) -> int:
    chars = db.list_characters(project_id)
    char = next((c for c in chars if c.name == character_name), None)
    if char:
        profile = (char.voice_profile or "").lower()
        for key, seed in _CHATTTS_VOICE_SEEDS.items():
            if key in profile:
                return seed
        if char.gender in ("男", "男性"):
            return _CHATTTS_VOICE_SEEDS["男"]
        if char.gender in ("女", "女性"):
            return _CHATTTS_VOICE_SEEDS["女"]
    return _CHATTTS_VOICE_SEEDS["default"]


_BARK_VOICE_MAP = {
    # 男性角色
    "男": "v2/zh_speaker_2",
    "男性": "v2/zh_speaker_2",
    "男孩": "v2/zh_speaker_3",
    "少年": "v2/zh_speaker_3",
    "老男": "v2/zh_speaker_8",
    "老人": "v2/zh_speaker_8",
    "长者": "v2/zh_speaker_8",
    "反派": "v2/zh_speaker_6",
    "武士": "v2/zh_speaker_4",
    # 女性角色
    "女": "v2/zh_speaker_0",
    "女性": "v2/zh_speaker_0",
    "女孩": "v2/zh_speaker_1",
    "少女": "v2/zh_speaker_5",
    "温柔": "v2/zh_speaker_7",
    # 旁白/解说
    "旁白": "v2/zh_speaker_9",
    "解说": "v2/zh_speaker_4",
    "narrator": "v2/zh_speaker_9",
    "default": "v2/zh_speaker_0",
}


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


def get_voice_preset_bark(character_name: str, project_id: int) -> str:
    chars = db.list_characters(project_id)
    char = next((c for c in chars if c.name == character_name), None)
    if char:
        profile = (char.voice_profile or "").lower()
        for key, preset in _BARK_VOICE_MAP.items():
            if key in profile:
                return preset
        if char.gender in ("男", "男性"):
            return _BARK_VOICE_MAP["男"]
        if char.gender in ("女", "女性"):
            return _BARK_VOICE_MAP["女"]
    return _BARK_VOICE_MAP["default"]


def _try_one_backend(
    backend: str,
    text: str,
    output_path: str,
    voice: str,
    voice_preset: str,
    voice_seed: int,
) -> bool:
    """尝试单个后端，失败返回 False（不抛异常）。"""
    try:
        if backend == "chattts":
            seed = voice_seed or _CHATTTS_VOICE_SEEDS.get(voice, _CHATTTS_VOICE_SEEDS["default"])
            return generate_tts_chattts(text, output_path, seed)
        elif backend == "bark":
            preset = voice_preset or _BARK_VOICE_MAP.get(voice, _BARK_VOICE_MAP["default"])
            return generate_tts_bark(text, output_path, preset)
        elif backend == "edge_tts":
            v = voice if voice.startswith("zh-CN") else _VOICE_MAP.get(voice, _VOICE_MAP["default"])
            return generate_tts_edge(text, output_path, v)
        elif backend == "kokoro":
            return generate_tts_kokoro(text, output_path)
        else:
            return generate_tts_pyttsx3(text, output_path)
    except Exception as e:
        print(f"[TTS] {backend} 失败: {e}，尝试下一个后端…")
        return False


def generate_tts(
    text: str,
    output_path: str,
    voice: str = "",
    backend: str = "",
    voice_preset: str = "",
    voice_seed: int = 0,
) -> bool:
    """
    生成 TTS 音频，内置回退链：
      chattts → edge_tts → bark → kokoro → pyttsx3
    指定 backend 时以该后端优先，失败后仍自动回退。
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 构建有序后端列表
    ranked = _ranked_tts_backends()
    if backend and backend in ranked:
        ranked = [backend] + [b for b in ranked if b != backend]
    elif backend:
        ranked = [backend] + ranked  # 强制指定的放首位，即使 check 失败也试一次

    for b in ranked:
        if _try_one_backend(b, text, output_path, voice, voice_preset, voice_seed):
            return True

    print("[TTS] 所有后端均失败")
    return False


# ── Shot TTS 批量生成 ──────────────────────────────────────

def generate_shot_tts(
    project_id: int,
    shot_id: int,
    output_dir: Path,
    backend: str = "",
) -> list[dict]:
    """
    为单个 shot 的所有对白生成 TTS 音频。
    生成前先查 asset_registry，已完成则直接返回已有记录。
    返回音频文件列表。
    """
    shot = db.get_shot(shot_id)
    if not shot:
        return []

    # ── 复用检查：TTS 已全部完成，直接返回 ──────────────────
    from core.asset_registry import is_shot_tts_complete, get_shot_tts
    if is_shot_tts_complete(project_id, shot_id):
        existing = get_shot_tts(project_id, shot_id)
        if existing:
            print(f"[AudioPipeline] ♻️  shot {shot_id} TTS 已存在，复用 {len(existing)} 条")
            return existing

    try:
        dialogue_list = json.loads(shot.dialogue) if shot.dialogue else []
    except Exception:
        dialogue_list = []

    results = []
    shot_audio_dir = output_dir / f"shot_{shot_id:04d}" / "tts"
    shot_audio_dir.mkdir(parents=True, exist_ok=True)

    # 已有部分行 — 按 line_idx 跳过已完成的
    existing_indices = {a["line_idx"] for a in get_shot_tts(project_id, shot_id)}

    for idx, line in enumerate(dialogue_list):
        if not isinstance(line, dict):
            continue
        text = line.get("line", "").strip()
        character = line.get("character", "旁白")
        if not text:
            continue

        out_path = str(shot_audio_dir / f"line_{idx:03d}_{character}.mp3")

        # 行级复用：该行已生成且文件存在
        if idx in existing_indices and Path(out_path).exists():
            duration = _get_audio_duration(out_path)
            results.append({"line_idx": idx, "character": character,
                             "text": text, "file": out_path, "duration": duration})
            continue

        backend_used = backend or _pick_tts_backend()
        if backend_used == "chattts":
            seed = get_voice_seed_chattts(character, project_id)
            success = generate_tts(text, out_path, backend="chattts", voice_seed=seed)
        elif backend_used == "bark":
            voice_preset = get_voice_preset_bark(character, project_id)
            success = generate_tts(text, out_path, backend="bark", voice_preset=voice_preset)
        else:
            voice = get_voice_for_character(character, project_id)
            success = generate_tts(text, out_path, voice=voice, backend=backend_used)
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

# 情绪 → 和弦频率(Hz) + 混响参数 + AM 调制速率
_MOOD_PROFILES: dict[str, dict] = {
    "epic":    {"freqs": [65.4, 98.0, 130.8, 196.0], "amps": [0.35, 0.25, 0.2, 0.1], "echo": "1000|1500", "am": 1.5},
    "热血":    {"freqs": [65.4, 98.0, 130.8, 196.0], "amps": [0.35, 0.25, 0.2, 0.1], "echo": "1000|1500", "am": 2.0},
    "mysterious": {"freqs": [55.0, 69.3, 82.4, 110.0], "amps": [0.3, 0.2, 0.2, 0.15], "echo": "2000|3000", "am": 0.4},
    "神秘":    {"freqs": [55.0, 69.3, 82.4, 110.0], "amps": [0.3, 0.2, 0.2, 0.15], "echo": "2000|3000", "am": 0.4},
    "warm":    {"freqs": [65.4, 82.4, 98.0, 123.5], "amps": [0.3, 0.25, 0.2, 0.15], "echo": "800|1200",  "am": 0.7},
    "温馨":    {"freqs": [65.4, 82.4, 98.0, 123.5], "amps": [0.3, 0.25, 0.2, 0.15], "echo": "800|1200",  "am": 0.7},
    "dark":    {"freqs": [41.2, 49.0, 65.4, 82.4],  "amps": [0.35, 0.3, 0.2, 0.1],  "echo": "1500|2500", "am": 0.25},
    "黑暗":    {"freqs": [41.2, 49.0, 65.4, 82.4],  "amps": [0.35, 0.3, 0.2, 0.1],  "echo": "1500|2500", "am": 0.25},
    "romantic": {"freqs": [65.4, 82.4, 110.0, 130.8], "amps": [0.28, 0.22, 0.18, 0.12], "echo": "600|1000", "am": 0.6},
    "浪漫":    {"freqs": [65.4, 82.4, 110.0, 130.8], "amps": [0.28, 0.22, 0.18, 0.12], "echo": "600|1000", "am": 0.6},
    "suspense": {"freqs": [65.4, 69.3, 92.5, 110.0], "amps": [0.3, 0.2, 0.25, 0.15], "echo": "1200|2000", "am": 0.9},
    "悬疑":    {"freqs": [65.4, 69.3, 92.5, 110.0], "amps": [0.3, 0.2, 0.25, 0.15], "echo": "1200|2000", "am": 0.9},
}
_DEFAULT_MOOD_PROFILE = {"freqs": [65.4, 82.4, 98.0, 130.8], "amps": [0.3, 0.22, 0.18, 0.1], "echo": "1000|1800", "am": 0.8}


def generate_music_ffmpeg(
    prompt: str,
    output_path: str,
    duration: int = 30,
    mood: str = "",
) -> bool:
    """用 ffmpeg 多声部正弦合成生成氛围背景音乐（无需本地 ML 模型）。"""
    TWO_PI = 6.28318530
    # 匹配情绪 profile
    mood_key = (mood or prompt or "").lower()
    profile = _DEFAULT_MOOD_PROFILE
    for key, p in _MOOD_PROFILES.items():
        if key in mood_key:
            profile = p
            break

    freqs = profile["freqs"]
    amps  = profile["amps"]
    echo_delays = profile["echo"]
    fade = min(3.0, duration * 0.1)

    # 简单和弦：多个正弦波叠加，避免复杂嵌套表达式
    def _expr(detune: float = 1.0) -> str:
        terms = [f"{a}*sin({TWO_PI}*{f*detune:.3f}*t)" for f, a in zip(freqs, amps)]
        return "+".join(terms)

    aevalsrc = f"{_expr()}|{_expr(1.002)}"  # 轻微失谐立体声

    af_chain = (
        f"afade=t=in:d={fade},"
        f"afade=t=out:st={max(0, duration-fade):.1f}:d={fade},"
        f"aecho=0.8:0.9:{echo_delays}:0.3|0.25,"
        "equalizer=f=200:t=o:w=200:g=4,"
        "equalizer=f=3000:t=o:w=500:g=-3,"
        "volume=0.85"
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"aevalsrc={aevalsrc}:s=44100:d={duration}",
                "-af", af_chain,
                "-c:a", "libmp3lame", "-b:a", "128k",
                output_path,
            ],
            capture_output=True, timeout=60,
        )
        return result.returncode == 0 and Path(output_path).exists()
    except Exception as e:
        print(f"[ffmpeg music] 失败: {e}")
        return False

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


# 中文情绪 → MusicGen 英文描述（效果更好）
_MOOD_TO_MUSICGEN: dict[str, str] = {
    "热血": "epic orchestral battle music, intense drums, brass fanfare, heroic melody, high energy",
    "史诗": "cinematic epic score, full orchestra, soaring strings, powerful brass, dramatic",
    "神秘": "mysterious ambient music, ethereal pads, distant flute, dark atmosphere, slow tempo",
    "温馨": "warm heartfelt piano melody, gentle strings, soft acoustic guitar, emotional, tender",
    "黑暗": "dark ominous music, low drones, dissonant strings, eerie atmosphere, tense",
    "浪漫": "romantic piano and violin, soft strings, waltz feel, emotional, beautiful melody",
    "悬疑": "suspenseful film music, staccato strings, building tension, mysterious, cinematic",
    "轻松": "light playful music, acoustic guitar, xylophone, cheerful, gentle rhythm",
    "治愈": "healing ambient music, soft piano, nature sounds, peaceful, meditative",
    "搞笑": "playful comedic music, pizzicato strings, bouncy rhythm, light-hearted, fun",
    "恐怖": "horror ambient music, deep drones, unsettling atmosphere, creepy, dark",
    "epic":   "epic orchestral music, heroic brass, powerful drums, cinematic, triumphant",
    "warm":   "warm acoustic music, gentle piano, soft strings, cozy, emotional",
    "dark":   "dark cinematic music, minor key, low bass, ominous atmosphere",
}
_DEFAULT_MUSICGEN_PROMPT = "atmospheric background music, orchestral, cinematic, moderate tempo"


def _check_audiocraft() -> bool:
    """检查 bark venv 中是否有可用的 audiocraft。"""
    if not BARK_PYTHON.exists():
        return False
    r = subprocess.run(
        [str(BARK_PYTHON), "-c", "from audiocraft.models import MusicGen"],
        capture_output=True, timeout=15,
    )
    return r.returncode == 0


def generate_music_audiocraft(prompt: str, output_path: str, duration: int = 30, mood: str = "") -> bool:
    """通过 bark venv 子进程调用 MusicGen 生成音乐。"""
    if not BARK_PYTHON.exists():
        return False

    # 构建英文 prompt（MusicGen 对英文效果更好）
    mood_key = (mood or "").strip()
    en_prompt = _MOOD_TO_MUSICGEN.get(mood_key)
    if not en_prompt:
        # 尝试在 prompt 里匹配情绪关键词
        for key, val in _MOOD_TO_MUSICGEN.items():
            if key in (prompt or "").lower():
                en_prompt = val
                break
    en_prompt = en_prompt or prompt or _DEFAULT_MUSICGEN_PROMPT

    wav_path = str(Path(output_path).with_suffix(".wav"))
    script = f"""
import torch, sys
try:
    from audiocraft.models import MusicGen
    from audiocraft.data.audio import audio_write
except ImportError:
    sys.exit(1)

device = "cpu"  # MPS causes OOM on musicgen-small in subprocess; CPU is stable
model = MusicGen.get_pretrained("facebook/musicgen-small", device=device)
model.set_generation_params(duration={duration})
wav = model.generate([{repr(en_prompt)}])
import torchaudio
torchaudio.save({repr(wav_path)}, wav[0].cpu(), model.sample_rate)
"""
    try:
        result = subprocess.run(
            [str(BARK_PYTHON), "-c", script],
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:
            print(f"[MusicGen] 失败: {result.stderr[-300:]}")
            return False
        if not Path(wav_path).exists():
            return False
        # wav → mp3
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libmp3lame", "-b:a", "192k", output_path],
            capture_output=True, timeout=30,
        )
        Path(wav_path).unlink(missing_ok=True)
        return Path(output_path).exists()
    except Exception as e:
        print(f"[MusicGen] 异常: {e}")
        return False


def _audio_valid(path: str, min_bytes: int = 2048) -> bool:
    """Return True only if file exists and has meaningful content."""
    p = Path(path)
    return p.exists() and p.stat().st_size >= min_bytes


def generate_music(
    prompt: str,
    output_path: str,
    duration: int = 30,
    project_id: int = 0,
    music_id: int = 0,
    mood: str = "",
) -> bool:
    """统一音乐生成接口，按优先级尝试各后端。每个后端都验证输出文件非空。"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if generate_music_heartmula(prompt, output_path, duration) and _audio_valid(output_path):
        _register_audio(project_id, 0, "music", output_path, music_id)
        return True

    if generate_music_audiocraft(prompt, output_path, duration, mood=mood) and _audio_valid(output_path):
        print(f"[AudioPipeline] MusicGen 生成 BGM（mood={mood or ''}）")
        _register_audio(project_id, 0, "music", output_path, music_id)
        return True

    if generate_music_ffmpeg(prompt, output_path, duration, mood=mood) and _audio_valid(output_path):
        print(f"[AudioPipeline] ffmpeg 合成 BGM（mood={mood or '默认'}）")
        _register_audio(project_id, 0, "music", output_path, music_id)
        return True

    Path(output_path).unlink(missing_ok=True)
    print(f"[AudioPipeline] 音乐生成失败（所有后端均无有效输出）: {prompt[:60]}")
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
        success = generate_music(prompt, out_path, duration=20, project_id=project_id, music_id=m.id, mood=m.mood or "")
        if success:
            db.update_music(m.id, {"file_path": out_path})
        results.append({"id": m.id, "name": m.name, "file": out_path if success else "", "success": success})
    return results


# ── 音效生成 ──────────────────────────────────────────────

def generate_sfx_audiocraft(description: str, output_path: str, duration: int = 5) -> bool:
    """使用 audiocraft AudioGen 生成音效。"""
    try:
        cmd = [
            "python3", "-m", "audiocraft.generate",
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
    from core.asset_registry import is_shot_tts_complete
    shots = db.list_shots(project_id=project_id)
    _progress(f"生成 TTS：{len(shots)} 个 shot", 0.0)
    for i, shot in enumerate(shots):
        pct = 0.1 + 0.4 * i / max(len(shots), 1)
        if is_shot_tts_complete(project_id, shot.id):
            _progress(f"  ♻️  shot {shot.id} TTS 已完成，跳过", pct)
            continue
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
    if not project_id:
        return
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
