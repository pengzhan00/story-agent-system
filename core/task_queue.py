"""
Task Queue Coordinator — central brain for inter-agent communication.
Each Agent claims tasks from the queue, executes, and writes results back.

Usage (inside an Agent's main.py):
    from core.task_queue import TaskWorker
    worker = TaskWorker("director")
    worker.run_forever(poll_interval=2.0)  # poll loop
    # or one-shot:
    task = worker.claim()
    if task:
        result = my_handler(task)
        worker.complete(task["id"], result)
"""

import json
import time
import sys
import traceback
from typing import Optional
from datetime import datetime, timezone
from core.database import create_task, claim_next_task, complete_task, get_task, list_tasks
from core.database import add_agent_log, list_agent_logs


class TaskWorker:
    """Base worker that claims + completes tasks from the queue for one agent type."""

    def __init__(self, agent_type: str):
        self.agent_type = agent_type
        self.running = False

    def claim(self) -> Optional[dict]:
        """Claim the next pending task for this agent type."""
        return claim_next_task(self.agent_type)

    def complete(self, task_id: int, output: dict, error: str = ""):
        """Mark a task complete/failed in the queue."""
        complete_task(task_id, output, error)

    def log(self, task_id: int, action: str, level: str, message: str):
        """Write to agent_logs for observability."""
        add_agent_log(task_id, self.agent_type, action, level, message)

    def run_forever(self, handler, poll_interval: float = 2.0):
        """
        Run an infinite polling loop.
        `handler` receives (task: dict) and returns (output: dict, error: str).
        """
        self.running = True
        print(f"[task_queue] {self.agent_type} worker started, poll={poll_interval}s", file=sys.stderr)
        while self.running:
            try:
                task = self.claim()
                if task:
                    self.log(task["id"], "claimed", "info", f"Claimed task #{task['id']}: {task['action']}")
                    try:
                        output, error = handler(task)
                        if error:
                            self.complete(task["id"], output or {}, error)
                            self.log(task["id"], "failed", "error", error)
                        else:
                            self.complete(task["id"], output or {})
                            self.log(task["id"], "completed", "info", "Task completed successfully")
                    except Exception as e:
                        tb = traceback.format_exc()
                        self.complete(task["id"], {}, str(e))
                        self.log(task["id"], "exception", "error", tb)
                else:
                    time.sleep(poll_interval)
            except KeyboardInterrupt:
                self.running = False
                print(f"[task_queue] {self.agent_type} worker stopped", file=sys.stderr)
                break
            except Exception as e:
                print(f"[task_queue] Worker error: {e}", file=sys.stderr)
                time.sleep(poll_interval * 2)


def dispatch_task(agent_type: str, action: str, input_params: dict,
                  project_id: int = 0, priority: int = 5, parent_task_id: int = 0) -> int:
    """Create a task in the queue for an agent to pick up."""
    return create_task({
        "agent_type": agent_type,
        "action": action,
        "input_params": input_params,
        "project_id": project_id,
        "priority": priority,
        "parent_task_id": parent_task_id,
    })


def wait_for_task(task_id: int, poll_interval: float = 1.0, timeout: float = 300) -> Optional[dict]:
    """Block until a specific task completes or times out."""
    elapsed = 0.0
    while elapsed < timeout:
        task = get_task(task_id)
        if task and task["status"] in ("completed", "failed"):
            return task
        time.sleep(poll_interval)
        elapsed += poll_interval
    return None


def get_pipeline_status(project_id: int) -> list:
    """Get all tasks for a project, grouped for the UI to display."""
    return list_tasks(project_id=project_id, limit=100)
