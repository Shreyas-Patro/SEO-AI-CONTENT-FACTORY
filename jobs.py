"""
Job manager — runs pipeline functions in a separate process so the web
server stays responsive. Tees stdout to runs/<run_id>/_live.log.
"""
import os
import sys
import traceback
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, Future
from threading import Lock
from typing import Callable

ROOT = Path(__file__).parent


def _bootstrap_and_run(fn_module: str, fn_name: str, log_path: str, *args):
    """Runs in a child process. Tees stdout/stderr to the log file."""
    import importlib

    class Tee:
        def __init__(self, real, fp):
            self.real = real
            self.fp = fp
        def write(self, s):
            try:
                self.real.write(s)
            except Exception:
                pass
            try:
                with open(self.fp, "a", encoding="utf-8") as f:
                    f.write(s)
            except Exception:
                pass
            return len(s) if s else 0
        def flush(self):
            try:
                self.real.flush()
            except Exception:
                pass

    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    Path(log_path).write_text("", encoding="utf-8")

    sys.stdout = Tee(sys.__stdout__, log_path)
    sys.stderr = Tee(sys.__stderr__, log_path)

    try:
        mod = importlib.import_module(fn_module)
        fn = getattr(mod, fn_name)
        print(f"[bg] starting {fn_module}.{fn_name} args={args}")
        result = fn(*args)
        print(f"[bg] completed {fn_module}.{fn_name}")
        return {"ok": True, "result": str(result)[:500]}
    except Exception as e:
        tb = traceback.format_exc()
        print(f"\n[CRASH] {type(e).__name__}: {e}\n{tb}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "tb": tb}


class JobManager:
    """Wraps a ProcessPoolExecutor so each job runs in its own process."""
    def __init__(self, max_workers: int = 4):
        self._executor = ProcessPoolExecutor(max_workers=max_workers)
        self._futures: dict[str, Future] = {}
        self._errors: dict[str, dict] = {}
        self._lock = Lock()

    def _log_path_for(self, job_id: str) -> str:
        # job_id format: "<run_id>:l1" or "<run_id>:rerun:<agent>"
        run_id = job_id.split(":")[0]
        return str(ROOT / "runs" / run_id / "_live.log")

    def submit(self, job_id: str, fn: Callable, *args):
        with self._lock:
            existing = self._futures.get(job_id)
            if existing and not existing.done():
                return False  # already running
            log_path = self._log_path_for(job_id)
            fut = self._executor.submit(
                _bootstrap_and_run,
                fn.__module__, fn.__name__, log_path, *args,
            )

            def _on_done(f: Future):
                try:
                    res = f.result()
                except Exception as e:
                    self._errors[job_id] = {"error": str(e), "tb": traceback.format_exc()}
                    return
                if not res.get("ok"):
                    self._errors[job_id] = {
                        "error": res.get("error", "unknown"),
                        "tb": res.get("tb", ""),
                    }

            fut.add_done_callback(_on_done)
            self._futures[job_id] = fut
            return True

    def is_active(self, job_id: str) -> bool:
        fut = self._futures.get(job_id)
        return fut is not None and not fut.done()

    def get_error(self, job_id: str):
        return self._errors.get(job_id)

    def clear_error(self, job_id: str):
        self._errors.pop(job_id, None)


job_manager = JobManager(max_workers=4)