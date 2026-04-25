#!/usr/bin/env python3
"""Writer (Screenwriter) Agent — CLI entry point"""
import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import init_db
from agents.writer.core import *


def handler_factory(action: str):
    """Create a task handler compatible with TaskWorker.run_forever."""
    def handler(task: dict) -> tuple:
        input_data = task.get("input_params", {})
        project_id = task.get("project_id", 0)
        task_id = task.get("id", 0)
        try:
            result = run_action(action, input_data, project_id, task_id)
            return result, ""
        except Exception as e:
            return {"error": str(e)}, str(e)
    return handler


def main():
    parser = argparse.ArgumentParser(description="Writer (Screenwriter) Agent")
    parser.add_argument("--action", required=True, help="Action to perform (generate_storyline, expand_scene)")
    parser.add_argument("--input", default='{}', help="JSON input params")
    parser.add_argument("--project-id", type=int, default=0)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--task-mode", action="store_true", help="Run as task queue worker")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Poll interval in task mode")
    args = parser.parse_args()

    init_db()

    if args.task_mode:
        from core.task_queue import TaskWorker
        worker = TaskWorker("screenwriter")
        worker.run_forever(handler_factory(args.action), poll_interval=args.poll_interval)
    else:
        input_data = json.loads(args.input)
        result = run_action(args.action, input_data, args.project_id, args.task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
