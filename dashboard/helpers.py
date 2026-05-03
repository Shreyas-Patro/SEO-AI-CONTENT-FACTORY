"""
Helpers for the dashboard.
"""
import json
import threading
import contextlib
import sys
import time
from pathlib import Path


class _Tee:
    def __init__(self, log_file, original):
        self.log_file = log_file
        self.original = original

    def write(self, s):
        try:
            self.original.write(s)
        except Exception:
            pass
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(s)
                f.flush()
        except Exception:
            pass
        return len(s) if s else 0

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass


@contextlib.contextmanager
def tee_to_file(log_file):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    Path(log_file).write_text("", encoding="utf-8")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(log_file, old_out)
    sys.stderr = _Tee(log_file, old_err)
    try:
        yield
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def run_in_bg(fn, run_id, log_file, err_holder, *args):
    """Run a function in a daemon thread, tee'ing output to a log file."""
    def _target():
        try:
            with tee_to_file(log_file):
                fn(run_id, *args)
        except Exception as e:
            err_holder["error"] = f"{type(e).__name__}: {e}"
            try:
                import traceback
                with open(log_file, "a") as f:
                    f.write(f"\n[CRASH] {type(e).__name__}: {e}\n{traceback.format_exc()}")
            except Exception:
                pass
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t


def is_alive(t):
    return t is not None and t.is_alive()


# ─── Agent status reading ───────────────────────────────────────────

ALL_AGENTS = [
    # Layer 1
    ("trend_scout",                "📡 Trend Scout",      1),
    ("competitor_spy",             "🕵️ Comp Spy",        1),
    ("keyword_mapper",             "🗺️ KW Mapper",       1),
    # Layer 2
    ("content_architect",          "🏗️ Architect",       2),
    ("faq_architect",              "❓ FAQs",            2),
    ("research_prompt_generator",  "🔬 Research",        2),
    # Layer 3
    ("lead_writer",                "✍️ Writer",          3),
    ("fact_verifier",              "🔍 Fact Verify",     3),
    ("brand_auditor",              "🎨 Brand Audit",     3),
    ("rewriter",                   "🔄 Rewriter",        3),
    ("meta_tagger",                "🏷️ Meta Tagger",     3),
]


def get_agent_status(run_id, agent_name):
    base = Path("runs") / run_id / agent_name
    if not base.exists():
        return "pending"
    out = base / "output.json"
    meta_f = base / "metadata.json"
    if out.exists():
        if meta_f.exists():
            try:
                d = json.loads(meta_f.read_text(encoding="utf-8"))
                if d.get("status") == "failed":
                    return "failed"
            except Exception:
                pass
        return "done"
    if (base / "input.json").exists():
        return "active"
    return "pending"


def has_active_agent(run_id):
    """Return True if any agent for this run is currently 'active'."""
    for agent_name, _, _ in ALL_AGENTS:
        if get_agent_status(run_id, agent_name) == "active":
            return True
    return False