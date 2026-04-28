"""
Background pipeline runner.

The main problem: Streamlit's `with st.spinner()` blocks the UI for the
duration of run_layer1(). Agents print things, but the user sees nothing
until the call returns.

Fix: run the orchestrator in a background thread, intercept stdout, and
write every line to the activity log file. The dashboard polls that file
and shows live progress with auto-refresh.

USAGE (in dashboard.py):
    from background_runner import start_layer_in_background, get_run_thread_status

    if st.button("Run Layer 1"):
        start_layer_in_background(view_run_id, "layer1")
        st.rerun()

    # In the UI body:
    thread_status = get_run_thread_status(view_run_id)
    if thread_status == "running":
        st_autorefresh(interval=2000)  # auto-rerun every 2s
"""

import threading
import sys
import io
import time
from typing import Dict
from db.activity import set_status, push_activity_log, clear_status, reset_activity_log


# ─── Thread registry ──────────────────────────────────────────────────────
# Maps run_id -> {"thread": Thread, "status": str, "error": Optional[str]}
_threads: Dict[str, dict] = {}
_lock = threading.Lock()


def get_run_thread_status(run_id: str) -> str:
    """Returns: 'idle' | 'running' | 'completed' | 'failed'."""
    with _lock:
        info = _threads.get(run_id)
    if not info:
        return "idle"
    if info["thread"].is_alive():
        return "running"
    return info.get("status", "idle")


def get_run_thread_error(run_id: str) -> str:
    with _lock:
        info = _threads.get(run_id) or {}
    return info.get("error", "")


# ─── Stdout interceptor ───────────────────────────────────────────────────
class _ActivityLogWriter(io.StringIO):
    """A stream that forwards every line written to the activity log."""

    def __init__(self, run_id: str, original_stream):
        super().__init__()
        self.run_id = run_id
        self.original = original_stream
        self._buffer = ""

    def write(self, s):
        # Always write to original so terminal sees it too
        try:
            self.original.write(s)
        except Exception:
            pass

        # Accumulate until we have a full line, then push
        self._buffer += s
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip()
            if line:
                push_activity_log(self.run_id, line)
                # Heuristic: detect the agent name from "[Agent Name]" prefix
                agent = None
                if line.startswith("["):
                    end = line.find("]")
                    if 0 < end < 40:
                        agent = line[1:end].lower().replace(" ", "_")
                set_status(self.run_id, line[:120], agent=agent)
        return len(s)

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass


# ─── Background thread entry points ───────────────────────────────────────
def _run_in_thread(run_id: str, layer: str):
    """The function that actually runs in the background thread."""
    # Replace stdout/stderr for this thread so prints go to activity log
    orig_out, orig_err = sys.stdout, sys.stderr
    sys.stdout = _ActivityLogWriter(run_id, orig_out)
    sys.stderr = _ActivityLogWriter(run_id, orig_err)

    try:
        set_status(run_id, f"Starting {layer}...", agent="orchestrator")

        # Lazy import so we don't pay the cost at module load
        from orchestrator import run_layer1, run_layer2

        if layer == "layer1":
            run_layer1(run_id)
        elif layer == "layer2":
            run_layer2(run_id)
        else:
            raise ValueError(f"Unknown layer: {layer}")

        set_status(run_id, f"{layer} complete", agent="orchestrator")
        with _lock:
            if run_id in _threads:
                _threads[run_id]["status"] = "completed"

    except Exception as e:
        import traceback
        err_text = f"{type(e).__name__}: {e}"
        push_activity_log(run_id, f"FAILED: {err_text}")
        push_activity_log(run_id, traceback.format_exc())
        set_status(run_id, f"FAILED: {err_text}", agent="orchestrator")
        with _lock:
            if run_id in _threads:
                _threads[run_id]["status"] = "failed"
                _threads[run_id]["error"] = err_text
    finally:
        sys.stdout = orig_out
        sys.stderr = orig_err


def start_layer_in_background(run_id: str, layer: str) -> bool:
    """
    Kick off run_layer1 or run_layer2 in a daemon thread.
    Returns True if started, False if already running.
    """
    with _lock:
        existing = _threads.get(run_id)
        if existing and existing["thread"].is_alive():
            return False  # already running

        # Reset activity log for fresh run
        reset_activity_log(run_id)
        clear_status(run_id)

        t = threading.Thread(
            target=_run_in_thread,
            args=(run_id, layer),
            daemon=True,
            name=f"pipeline-{run_id[-8:]}-{layer}",
        )
        _threads[run_id] = {"thread": t, "status": "running", "error": ""}
        t.start()
    return True


def cleanup_old_threads():
    """Remove finished threads from the registry."""
    with _lock:
        for run_id in list(_threads.keys()):
            if not _threads[run_id]["thread"].is_alive():
                # Keep status info but drop the thread reference after 1 hour
                pass  # We deliberately keep them so the dashboard can see final status