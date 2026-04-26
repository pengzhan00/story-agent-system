"""
Pipeline State — 管线 Checkpoint 与续跑

核心思路：状态从 DB 推导，无需外部状态文件。
  - render 完成  ← shot.status == "rendered" AND 视频文件存在
  - tts 完成    ← audio_assets 记录数 >= 对白行数
  - compose 完成 ← composed 文件存在磁盘
  - episode 完成 ← episode 文件存在磁盘

pipeline_runs 表记录每次跑的摘要，用于 UI 展示历史和错误。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional
import sys

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.database as db
from core.asset_registry import (
    project_snapshot,
    get_shot_video,
    get_shot_tts,
    get_project_bgm,
    get_shot_loras,
    is_shot_composed,
    get_composed_shot,
)

OUTPUT_DIR = PROJECT_ROOT / "output"

# ── 阶段常量 ──────────────────────────────────────────────

STAGE_RENDER    = "render"
STAGE_TTS       = "tts"
STAGE_COMPOSITE = "composite"
STAGE_EPISODE   = "episode"
ALL_STAGES = [STAGE_RENDER, STAGE_TTS, STAGE_COMPOSITE, STAGE_EPISODE]


# ── pipeline_runs 表操作 ──────────────────────────────────

def start_run(project_id: int, stage: str, total: int) -> int:
    """创建一条 pipeline_run 记录，返回 run_id。"""
    return db._insert("pipeline_runs", {
        "project_id": project_id,
        "stage": stage,
        "status": "running",
        "total": total,
        "done_count": 0,
        "error": "",
        "started_at": _now(),
        "updated_at": _now(),
    })


def update_run(run_id: int, done_count: int, status: str = "running", error: str = ""):
    db._execute(
        "UPDATE pipeline_runs SET done_count=?, status=?, error=?, updated_at=? WHERE id=?",
        (done_count, status, error, _now(), run_id)
    )


def finish_run(run_id: int, done_count: int, error: str = ""):
    status = "failed" if error else "done"
    update_run(run_id, done_count, status=status, error=error)


def list_runs(project_id: int, limit: int = 20) -> list[dict]:
    rows = db._fetchall(
        "SELECT * FROM pipeline_runs WHERE project_id=? ORDER BY id DESC LIMIT ?",
        (project_id, limit)
    )
    return [dict(r) for r in rows]


# ── 续跑决策 ──────────────────────────────────────────────

def get_pending_shots(project_id: int, stage: str, project_name: str = "") -> list:
    """
    返回指定阶段还没完成的 shot 列表（按 act/scene/shot 顺序排列）。
    stage: "render" | "tts" | "composite"
    """
    from pipelines.output_manager import sort_shots_by_order
    shots = db.list_shots(project_id=project_id)
    shots = sort_shots_by_order(shots)

    if stage == STAGE_RENDER:
        from core.asset_registry import is_shot_rendered, is_shot_video_on_disk
        return [s for s in shots
                if not (is_shot_rendered(s.id) and is_shot_video_on_disk(project_id, s.id, project_name))]

    elif stage == STAGE_TTS:
        from core.asset_registry import is_shot_tts_complete
        return [s for s in shots if not is_shot_tts_complete(project_id, s.id)]

    elif stage == STAGE_COMPOSITE:
        if not project_name:
            proj = db.get_project(project_id)
            project_name = proj.name if proj else ""
        return [s for s in shots
                if s.status in ("rendered", "approved")
                and not is_shot_composed(project_name, s.id)]

    return []


def describe_state(project_id: int, project_name: str = "") -> str:
    """生成可读的管线状态摘要（用于 UI 显示）。"""
    snap = project_snapshot(project_id, project_name)
    t = snap["total_shots"]
    lines = [
        f"📊 **管线状态** — 项目 #{project_id} `{snap['project_name']}`",
        f"- 总 Shot 数: {t}",
        f"- 🎬 已渲染: {snap['rendered']}/{t}",
        f"- 🔊 TTS 完成: {snap['tts_done']}/{t}",
        f"- 🎞️ 已合成: {snap['composed']}/{t}",
        f"- 🎵 BGM: {'✅' if snap['bgm_ready'] else '❌ 未生成'}",
    ]
    pending_render = t - snap["rendered"]
    pending_tts    = t - snap["tts_done"]
    pending_comp   = snap["rendered"] - snap["composed"]
    if pending_render > 0:
        lines.append(f"\n⏳ 待续: **渲染** {pending_render} 个 shot")
    elif pending_tts > 0:
        lines.append(f"\n⏳ 待续: **TTS** {pending_tts} 个 shot")
    elif pending_comp > 0:
        lines.append(f"\n⏳ 待续: **合成** {pending_comp} 个 shot")
    else:
        lines.append("\n✅ 所有阶段已完成，可导出剧集")
    return "\n".join(lines)


# ── 完整续跑管线 ──────────────────────────────────────────

def resume_pipeline(
    project_id: int,
    stages: list[str] = None,
    max_retries: int = 2,
    progress_fn=None,
) -> Generator[tuple[str, float], None, dict]:
    """
    从当前状态自动续跑所有未完成阶段。
    跳过已完成的 shot，只处理未完成的。

    用法（在 Gradio 生成器中 yield）：
        for msg, pct in resume_pipeline(project_id):
            yield msg, pct

    返回最终 result dict（生成器 return 值）。
    """
    if stages is None:
        stages = ALL_STAGES

    def _prog(msg: str, pct: float = 0.0):
        if progress_fn:
            progress_fn(msg, pct)
        return msg, pct

    proj = db.get_project(project_id)
    if not proj:
        yield _prog(f"❌ 项目 {project_id} 不存在", 0.0)
        return {"success": False, "error": "project not found"}

    project_name = proj.name
    result = {"project_id": project_id, "project_name": project_name, "stages": {}}

    # ── Stage 1: 渲染 ─────────────────────────────────────
    if STAGE_RENDER in stages:
        pending = get_pending_shots(project_id, STAGE_RENDER, project_name)
        yield _prog(f"🎬 渲染阶段：{len(pending)} 个 shot 待渲染", 0.02)

        if pending:
            run_id = start_run(project_id, STAGE_RENDER, len(pending))
            done = 0
            errors = []

            from pipelines.batch_renderer import BatchRenderer
            renderer = BatchRenderer(project_name, project_id=project_id)

            if not renderer.check_comfyui():
                finish_run(run_id, done, error="ComfyUI 不可达")
                yield _prog("⚠️  ComfyUI 离线，跳过渲染阶段", 0.05)
            else:
                for i, shot in enumerate(pending):
                    pct = 0.05 + 0.30 * i / max(len(pending), 1)
                    label = f"A{shot.act_number}S{shot.scene_number}#{shot.shot_number}"
                    yield _prog(f"🎬 渲染 [{i+1}/{len(pending)}] {label}", pct)

                    # 构建 render_payload → scene dict
                    try:
                        rp = json.loads(shot.render_payload) if shot.render_payload else {}
                    except Exception:
                        rp = {}
                    scene_dict = {
                        "location": shot.location,
                        "mood": shot.mood,
                        "time_of_day": shot.time_of_day,
                        "weather": shot.weather,
                        "narration": shot.narration,
                        "shot_id": shot.id,
                        "episode_number": 1,
                        **rp,
                    }
                    scene_id = f"shot_{shot.id:04d}"

                    # LoRA 从 asset_registry 解析
                    loras = get_shot_loras(project_id, shot)
                    scene_dict["_lora_refs"] = loras

                    video = renderer.render_scene_with_retry(
                        scene_dict, scene_id=scene_id,
                        timeout=1800, max_retries=max_retries
                    )
                    if video:
                        done += 1
                        update_run(run_id, done)
                    else:
                        errors.append(shot.id)

                finish_run(run_id, done, error=f"失败 shot: {errors}" if errors else "")
                result["stages"][STAGE_RENDER] = {"done": done, "failed": len(errors)}
                yield _prog(f"🎬 渲染完成 {done}/{len(pending)}，失败 {len(errors)}", 0.35)
        else:
            yield _prog("🎬 渲染：全部已完成，跳过", 0.35)
            result["stages"][STAGE_RENDER] = {"done": 0, "skipped": True}

    # ── Stage 2: TTS ──────────────────────────────────────
    if STAGE_TTS in stages:
        pending = get_pending_shots(project_id, STAGE_TTS, project_name)
        yield _prog(f"🔊 TTS 阶段：{len(pending)} 个 shot 待生成", 0.36)

        if pending:
            run_id = start_run(project_id, STAGE_TTS, len(pending))
            done = 0
            out_dir = OUTPUT_DIR / "projects" / project_name / "audio"

            from pipelines.audio_pipeline import generate_shot_tts, generate_project_music, generate_project_sfx
            for i, shot in enumerate(pending):
                pct = 0.36 + 0.18 * i / max(len(pending), 1)
                label = f"A{shot.act_number}S{shot.scene_number}#{shot.shot_number}"
                yield _prog(f"🔊 TTS [{i+1}/{len(pending)}] {label}", pct)
                tts = generate_shot_tts(project_id, shot.id, out_dir)
                if tts:
                    done += 1
                    update_run(run_id, done)

            finish_run(run_id, done)
            result["stages"][STAGE_TTS] = {"done": done}

            # 补生成音乐/音效（复用已有）
            yield _prog("🎵 生成/复用项目音乐...", 0.55)
            generate_project_music(project_id, out_dir)
            generate_project_sfx(project_id, out_dir)
            yield _prog("🎵 音乐/音效处理完成", 0.58)
        else:
            yield _prog("🔊 TTS：全部已完成，跳过", 0.58)
            result["stages"][STAGE_TTS] = {"done": 0, "skipped": True}

    # ── Stage 3: 合成 ─────────────────────────────────────
    if STAGE_COMPOSITE in stages:
        pending = get_pending_shots(project_id, STAGE_COMPOSITE, project_name)
        yield _prog(f"🎞️  合成阶段：{len(pending)} 个 shot 待合成", 0.59)

        if pending:
            run_id = start_run(project_id, STAGE_COMPOSITE, len(pending))
            done = 0
            composed_dir = OUTPUT_DIR / "projects" / project_name / "composed"
            composed_dir.mkdir(parents=True, exist_ok=True)
            bgm_path = get_project_bgm(project_id)

            from pipelines.compositor import compose_shot
            for i, shot in enumerate(pending):
                pct = 0.59 + 0.28 * i / max(len(pending), 1)
                label = f"A{shot.act_number}S{shot.scene_number}#{shot.shot_number}"
                yield _prog(f"🎞️  合成 [{i+1}/{len(pending)}] {label}", pct)

                video_path = get_shot_video(project_id, shot.id, project_name)
                if not video_path:
                    continue
                tts_files = get_shot_tts(project_id, shot.id)
                out_path = str(composed_dir / f"shot_{shot.id:04d}_composed.mp4")

                result_path = compose_shot(
                    shot_id=shot.id,
                    video_path=video_path,
                    tts_files=tts_files,
                    music_path=bgm_path,
                    sfx_paths=[],
                    output_path=out_path,
                    burn_subs=True,
                    project_id=project_id,
                )
                if result_path:
                    done += 1
                    update_run(run_id, done)

            finish_run(run_id, done)
            result["stages"][STAGE_COMPOSITE] = {"done": done}
            yield _prog(f"🎞️  合成完成 {done}/{len(pending)}", 0.87)
        else:
            yield _prog("🎞️  合成：全部已完成，跳过", 0.87)
            result["stages"][STAGE_COMPOSITE] = {"done": 0, "skipped": True}

    # ── Stage 4: 剧集合并 ─────────────────────────────────
    if STAGE_EPISODE in stages:
        yield _prog("🎬 合并剧集...", 0.88)
        from pipelines.output_manager import merge_project, sort_shots_by_order

        shots_ordered = sort_shots_by_order(db.list_shots(project_id=project_id))
        composed_videos = []
        for shot in shots_ordered:
            cp = get_composed_shot(project_name, shot.id)
            if cp:
                composed_videos.append(cp)
            else:
                # fallback: 使用原始渲染视频
                vp = get_shot_video(project_id, shot.id, project_name)
                if vp:
                    composed_videos.append(vp)

        if composed_videos:
            ep_dir = OUTPUT_DIR / "projects" / project_name / "episodes"
            ep_dir.mkdir(parents=True, exist_ok=True)
            ep_path = str(ep_dir / "ep001_final.mp4")

            from pipelines.compositor import compose_episode
            final = compose_episode(
                project_name=project_name,
                episode=1,
                shot_videos=composed_videos,
                output_path=ep_path,
                crossfade_duration=0.5,
            )
            result["episode_file"] = final
            yield _prog(f"✅ 剧集完成: {Path(final).name if final else '失败'}", 1.0)
        else:
            yield _prog("⚠️  没有可用视频，无法合并剧集", 1.0)

    result["success"] = True
    return result


# ── 辅助 ──────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
