"""
Live agent activity tracker.

Agents call `set_status(run_id, message)` from anywhere — even deep inside
a SERP loop. The message gets written to runs/<run_id>/status.json with a
timestamp. The dashboard polls this file and displays it.

WHY a file and not session state? Because Streamlit's session state is
locked while a long-running orchestrator call blocks the UI thread.
A file on disk + dashboard auto-refresh works around this.

USAGE in agents:
    from db.activity import set_status, push_activity_log

    set_status(run_id, "Searching magicbricks.com", agent="competitor_spy", sub_task="3/4")
    push_activity_log(run_id, "Found 5 articles for HSR Layout")
"""

import json
import time
from pathlib import Path
from datetime import datetime
from typing import Optional


def _status_file(run_id: str) -> Path:
    from db.artifacts import get_pipeline_run
    run = get_pipeline_run(run_id)
    if not run or not run.get("artifact_path"):
        p = Path("runs") / run_id
        p.mkdir(parents=True, exist_ok=True)
        return p / "status.json"
    return Path(run["artifact_path"]) / "status.json"


def _activity_log_file(run_id: str) -> Path:
    from db.artifacts import get_pipeline_run
    run = get_pipeline_run(run_id)
    if not run or not run.get("artifact_path"):
        p = Path("runs") / run_id
        p.mkdir(parents=True, exist_ok=True)
        return p / "activity.log"
    return Path(run["artifact_path"]) / "activity.log"


def set_status(run_id: str, message: str, agent: Optional[str] = None,
               progress: Optional[float] = None, sub_task: Optional[str] = None):
    if not run_id:
        return
    payload = {
        "agent": agent,
        "message": message,
        "sub_task": sub_task,
        "progress": progress,
        "timestamp": datetime.utcnow().isoformat(),
        "epoch": time.time(),
    }
    try:
        f = _status_file(run_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass

    push_activity_log(run_id, f"[{agent or '?'}] {message}" + (f" ({sub_task})" if sub_task else ""))


def push_activity_log(run_id: str, line: str):
    if not run_id:
        return
    try:
        f = _activity_log_file(run_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%H:%M:%S")
        with f.open("a", encoding="utf-8") as fp:
            fp.write(f"[{ts}] {line}\n")
    except Exception:
        pass


def get_status(run_id: str) -> dict:
    f = _status_file(run_id)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_activity_log(run_id: str, max_lines: int = 200) -> list:
    f = _activity_log_file(run_id)
    if not f.exists():
        return []
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


def clear_status(run_id: str):
    f = _status_file(run_id)
    if f.exists():
        try:
            f.unlink()
        except Exception:
            pass


def reset_activity_log(run_id: str):
    f = _activity_log_file(run_id)
    if f.exists():
        try:
            f.unlink()
        except Exception:
            pass


def is_run_active(run_id: str, timeout_seconds: int = 60) -> bool:
    """A run is 'active' if its status was updated in the last N seconds."""
    status = get_status(run_id)
    if not status:
        return False
    age = time.time() - status.get("epoch", 0)
    return age < timeout_seconds