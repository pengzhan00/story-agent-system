"""Render Scheduler Agent — business logic.
Manages ComfyUI rendering queue: schedules renders, checks status,
and submits batch plans to task_queue."""
import json
import time
import urllib.request
import urllib.error
from core.ollama_client import generate_json, DEFAULT_MODEL
from core.database import add_prompt_log, log_generation, create_task
from core.task_queue import dispatch_task

COMFYUI_URL = "http://127.0.0.1:8188"

RENDER_SCHEDULER_SYSTEM = """You are a Render Scheduler (渲染调度员) for an animation pipeline.
You plan and organize ComfyUI rendering tasks efficiently.
You understand scene composition, character rendering priorities, and batch optimization.
Output in Chinese (中文) or English as appropriate."""


def _check_comfyui_health() -> dict:
    """Check ComfyUI service availability.
    Returns dict with status, running_count, pending_count."""
    try:
        # Check /system_stats endpoint
        req = urllib.request.Request(f"{COMFYUI_URL}/system_stats", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            stats = json.loads(resp.read().decode())

        # Also check queue
        req_q = urllib.request.Request(f"{COMFYUI_URL}/queue", method="GET")
        with urllib.request.urlopen(req_q, timeout=5) as resp_q:
            queue = json.loads(resp_q.read().decode())

        return {
            "status": "online",
            "running_count": queue.get("queue_running", 0),
            "pending_count": queue.get("queue_pending", 0),
            "stats": stats,
            "message": "ComfyUI is running",
        }
    except urllib.error.URLError as e:
        return {
            "status": "offline",
            "running_count": 0,
            "pending_count": 0,
            "error": f"Cannot connect to ComfyUI at {COMFYUI_URL}: {e.reason}",
            "message": "ComfyUI is not reachable",
        }
    except json.JSONDecodeError as e:
        return {
            "status": "error",
            "running_count": 0,
            "pending_count": 0,
            "error": f"Invalid JSON response from ComfyUI: {e}",
            "message": "ComfyUI returned invalid data",
        }
    except Exception as e:
        return {
            "status": "error",
            "running_count": 0,
            "pending_count": 0,
            "error": str(e),
            "message": f"ComfyUI check failed: {e}",
        }


def schedule_render(
    scene_data: dict,
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Check ComfyUI status, create a render task in task_queue with a plan.

    Returns dict with:
      - comfyui_status: health check result
      - render_plan: what to render
      - task_id: created task id in task_queue
    """
    if not scene_data:
        return {"error": "No scene data provided"}

    # Check ComfyUI health
    health = _check_comfyui_health()

    # Build render plan
    location = scene_data.get("location", "unknown")
    mood = scene_data.get("mood", "neutral")
    characters = scene_data.get("characters", [])
    if isinstance(characters, str):
        try:
            characters = json.loads(characters)
        except json.JSONDecodeError:
            characters = [characters]

    render_plan = {
        "location": location,
        "mood": mood,
        "characters": characters,
        "prompt": _build_render_prompt(scene_data),
        "workflow_type": "scene",
        "settings": {
            "resolution": "1920x1080",
            "frames": scene_data.get("frames", 120),
            "fps": scene_data.get("fps", 24),
        },
    }

    if health["status"] != "online":
        render_plan["status_note"] = (
            f"ComfyUI is {health['status']}. "
            "Render plan created but cannot submit until ComfyUI is available."
        )

    # Create task in queue with action "submit_to_comfyui"
    created_task_id = dispatch_task(
        agent_type="render_scheduler",
        action="submit_to_comfyui",
        input_params={
            "render_plan": render_plan,
            "scene_data": scene_data,
        },
        project_id=project_id,
        priority=5,
        parent_task_id=task_id,
    )

    result = {
        "comfyui_status": health,
        "render_plan": render_plan,
        "task_id": created_task_id,
        "documentation": {
            "note": "Render task created. The 'submit_to_comfyui' action handler should:",
            "steps": [
                "1. Load ComfyUI workflow via pipelines/render_pipeline.py (get_dispatcher().render())",
                "2. Inject prompt text into the positive CLIPTextEncode node",
                "3. Set seed to random value",
                "4. POST workflow to http://127.0.0.1:8188/prompt",
                "5. Monitor completion via /queue endpoint",
                "6. Copy output video to project output directory",
            ],
        },
    }

    # Save to agent_logs
    log_generation({
        "project_id": project_id,
        "agent_type": "render_scheduler/schedule_render",
        "model": "",
        "prompt": json.dumps(scene_data, ensure_ascii=False),
        "response": json.dumps(result, ensure_ascii=False)[:5000],
        "tokens_in": 0,
        "tokens_out": 0,
        "duration_ms": 0,
    })

    return result


def _build_render_prompt(scene: dict) -> str:
    """Build a render prompt from scene data, replicating logic from
    pipelines/batch_renderer.py build_prompt_from_scene."""
    location = scene.get("location", "unknown location")
    mood = scene.get("mood", "calm")
    weather = scene.get("weather", "clear")
    time_of_day = scene.get("time_of_day", "daytime")
    narration = scene.get("narration", "")
    characters = scene.get("characters", [])
    if isinstance(characters, str):
        try:
            characters = json.loads(characters)
        except json.JSONDecodeError:
            characters = [characters]

    char_desc = ", ".join(characters) if characters else "a character"

    prompt = (
        f"anime style, {location}, {weather} weather, {time_of_day}, "
        f"{mood} atmosphere, {char_desc}, {narration}, "
        f"cinematic lighting, detailed background, story illustration style, "
        f"high quality, 2D anime art style"
    )
    return prompt


def check_status(
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Ping ComfyUI API and return queue info.
    Gracefully handles errors."""
    health = _check_comfyui_health()

    result = {
        "comfyui": health,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    log_generation({
        "project_id": project_id,
        "agent_type": "render_scheduler/check_status",
        "model": "",
        "prompt": "Check ComfyUI status",
        "response": json.dumps(result, ensure_ascii=False)[:5000],
        "tokens_in": 0,
        "tokens_out": 0,
        "duration_ms": 0,
    })

    return result


def submit_batch(
    scenes: list,
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Receive multiple scenes/characters to render,
    create a batch plan with priorities, and submit each as a task.

    Returns batch plan with task IDs for each scene."""
    if not scenes:
        return {"error": "No scenes provided for batch render"}

    health = _check_comfyui_health()
    batch_tasks = []
    total_scenes = len(scenes)

    for idx, scene in enumerate(scenes):
        location = scene.get("location", f"scene_{idx+1:03d}")
        priority = scene.get("priority", 5)

        # Create render plan for each scene
        render_plan = {
            "scene_index": idx + 1,
            "total_scenes": total_scenes,
            "location": location,
            "mood": scene.get("mood", "neutral"),
            "characters": scene.get("characters", []),
            "prompt": _build_render_prompt(scene),
            "workflow_type": "scene",
            "settings": {
                "resolution": "1920x1080",
                "frames": scene.get("frames", 120),
                "fps": scene.get("fps", 24),
            },
        }

        # Add context from batch_renderer reference:
        # - Uses render_pipeline.generate_scene_video → get_dispatcher().render()
        # - Output goes to project scenes dir
        # - Expects video file from ComfyUI output
        render_plan["_reference"] = {
            "submission": "Use pipelines.render_pipeline.submit_workflow(workflow)",
            "completion": "Use pipelines.render_pipeline.wait_for_completion(prompt_id, timeout=300)",
            "output": "Copy latest video from ~/Documents/ComfyUI/output/ to project scenes directory",
        }

        created_task_id = dispatch_task(
            agent_type="render_scheduler",
            action="submit_to_comfyui",
            input_params={
                "render_plan": render_plan,
                "scene_data": scene,
                "batch_index": idx,
                "batch_total": total_scenes,
            },
            project_id=project_id,
            priority=priority,
            parent_task_id=task_id,
        )

        batch_tasks.append({
            "scene_index": idx + 1,
            "location": location,
            "task_id": created_task_id,
            "priority": priority,
            "status": "created",
        })

    batch_plan = {
        "total_scenes": total_scenes,
        "comfyui_status": health,
        "batch_tasks": batch_tasks,
        "optimization_notes": {
            "parallel_capable": health["status"] == "online",
            "current_queue_load": f"{health['running_count']} running, {health['pending_count']} pending",
            "suggested_batch_size": 5,
            "note": "Scenes are submitted as individual tasks. A worker should pick them up sequentially or in parallel based on ComfyUI queue capacity.",
        },
    }

    log_generation({
        "project_id": project_id,
        "agent_type": "render_scheduler/submit_batch",
        "model": "",
        "prompt": f"Submit batch of {total_scenes} scenes",
        "response": json.dumps(batch_plan, ensure_ascii=False)[:5000],
        "tokens_in": 0,
        "tokens_out": 0,
        "duration_ms": 0,
    })

    return batch_plan


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Render Scheduler Agent."""
    try:
        if action == "schedule_render":
            scene_data = input_data.get("scene_data", input_data.get("scene", {}))
            result = schedule_render(scene_data, project_id, task_id)
            return {"result": result}

        elif action == "check_status":
            result = check_status(project_id, task_id)
            return {"result": result}

        elif action == "submit_batch":
            scenes = input_data.get("scenes", [])
            result = submit_batch(scenes, project_id, task_id)
            return {"result": result}

        else:
            return {"error": f"Unknown action: {action}"}
    except Exception as e:
        return {"error": str(e), "status": "error", "message": str(e)}
