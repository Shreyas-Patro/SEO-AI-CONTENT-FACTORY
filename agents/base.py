"""
agents/base.py — AgentBase v4.2

CHANGE FROM v4.1:
    Auto-merge state values into agent_input BEFORE the agent's own
    validation runs. This means agents that declare
    READS_STATE = [TREND_DATA, COMPETITOR_DATA] will receive those values
    inside their input dict automatically — no orchestrator-level data
    plumbing needed.

DESIGN CONTRACT:
    Each agent declares:
        READS_STATE       = [StateKey, ...]   # auto-injected into input
        WRITES_STATE      = [StateKey, ...]   # output goes into state
        OUTPUT_REQUIRED   = ["key", ...]      # output validation
        OUTPUT_NON_EMPTY  = ["key", ...]      # output validation

The framework (this base class) handles:
    1. Loading PipelineState
    2. Auto-injecting READS_STATE values into agent_input
    3. Persisting input.json
    4. Calling agent's _execute(state, agent_input)
    5. Validating output against OUTPUT_REQUIRED / OUTPUT_NON_EMPTY
    6. Retrying once on validation failure
    7. Persisting output.json + metadata.json + console
    8. Pushing WRITES_STATE values back into PipelineState
    9. Updating SQL run record

Drop into: agents/base.py
"""

import io
import sys
import time
import inspect
import contextlib
from datetime import datetime

from db.artifacts import save_artifact
from db.pipeline_state import PipelineState

# SQL helpers are best-effort — wrap imports
try:
    from db.sqlite_ops import start_agent_run, complete_agent_run
except Exception:
    def start_agent_run(*args, **kwargs):
        return None
    def complete_agent_run(*args, **kwargs):
        return None


# ─── Exceptions ────────────────────────────────────────────────────────────
class ValidationError(Exception):
    """Raised when input/output validation fails after retries."""
    pass


# ─── Console capture ───────────────────────────────────────────────────────
class _ConsoleCapture(io.StringIO):
    """Capture stdout while ALSO mirroring it to the original stream so the
    dashboard's live log polling still sees output in real time."""
    def __init__(self, original):
        super().__init__()
        self.original = original

    def write(self, s):
        try:
            self.original.write(s)
            self.original.flush()
        except Exception:
            pass
        return super().write(s)

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass


@contextlib.contextmanager
def _capture_console():
    cap_out = _ConsoleCapture(sys.stdout)
    cap_err = _ConsoleCapture(sys.stderr)
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = cap_out
    sys.stderr = cap_err
    try:
        yield cap_out, cap_err
    finally:
        sys.stdout = old_out
        sys.stderr = old_err


# ─── AgentBase ─────────────────────────────────────────────────────────────
class AgentBase:
    NAME = "base"

    READS_STATE: list = []
    WRITES_STATE: list = []
    OUTPUT_REQUIRED: list = []
    OUTPUT_NON_EMPTY: list = []

    MAX_VALIDATION_RETRIES = 2

    def __init__(self, pipeline_run_id, cluster_id=None, article_id=None):
        self.run_id = pipeline_run_id
        self.cluster_id = cluster_id
        self.article_id = article_id

        self.serp_calls = 0
        self.serp_cache_hits = 0
        self.llm_calls = 0
        self.llm_cache_hits = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0

        # Set during the run() lifecycle so agents can inspect their attempt
        # number (e.g. for prompt tweaking on retry). Default to 0 so agents
        # that reference it before run() starts don't crash.
        self._retry_attempt = 0
        self._validation_problems = []  # last-validation problems, for prompt feedback
        self._retry_problems = []       # alias used by some agents

    def __getattr__(self, name):
        """
        Forgiving fallback for a SHORT whitelist of private state attrs that
        older agent code may reference. We deliberately do NOT use a broad
        pattern match — that would mask method-name typos and create cryptic
        'int object is not callable' errors when an agent calls
        self.something_count() and we silently return 0.

        If you hit AttributeError on a NEW private attr name, add it here.
        """
        # Only handle attributes starting with _ AND in our explicit allowlist.
        SAFE_DEFAULTS = {
            "_retry_attempt": 0,
            "_retry_problems": [],
            "_retry_suffix": "",
            "_validation_problems": [],
            "_problems_history": [],
            "_last_output": None,
            "_last_error": None,
            "_attempt_count": 0,
            "_retries": 0,
        }
        if name in SAFE_DEFAULTS:
            return SAFE_DEFAULTS[name]
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    def track_serp(self, cache_hit=False):
        if cache_hit:
            self.serp_cache_hits += 1
        else:
            self.serp_calls += 1

    def track_llm(self, tokens_in=0, tokens_out=0, cost=0.0, cache_hit=False):
        if cache_hit:
            self.llm_cache_hits += 1
        else:
            self.llm_calls += 1
            self.tokens_in += tokens_in
            self.tokens_out += tokens_out
            self.cost_usd += cost

    def _validate_and_merge_input(self, state, agent_input):
        """
        Core of v4.2: merge READS_STATE values into agent_input automatically.

        For each key in READS_STATE:
          - If state.shared has it, copy into agent_input under the same key.
          - If neither state nor agent_input has it (and isn't already truthy),
            raise ValidationError.

        Result: agent's _execute() can read trend_data from EITHER
        agent_input["trend_data"] OR state.get(StateKeys.TREND_DATA) — they
        will be in sync.
        """
        merged = dict(agent_input) if agent_input else {}
        missing = []
        for key in self.READS_STATE:
            if key in merged and merged[key]:
                continue
            if state.has(key):
                merged[key] = state.get(key)
                continue
            missing.append(key)

        if missing:
            available = list(state.shared.keys())
            raise ValidationError(
                f"{self.NAME}: missing required state keys {missing}. "
                f"Upstream agents may have failed. "
                f"Available state keys: {available}"
            )
        return merged

    def _validate_output(self, output):
        problems = []
        if not isinstance(output, dict):
            return [f"output is not a dict (got {type(output).__name__})"]
        for key in self.OUTPUT_REQUIRED:
            if key not in output:
                problems.append(f"missing required output key: {key!r}")
        for key in self.OUTPUT_NON_EMPTY:
            val = output.get(key)
            if val is None or val == "" or val == [] or val == {}:
                problems.append(f"output key {key!r} is empty")
        return problems

    def _call_execute(self, state, agent_input):
        """Subclasses may define _execute with one of two signatures:
           - (self, agent_input)              # pre-v4 style
           - (self, state, agent_input)       # v4+ style
        """
        sig = inspect.signature(self._execute)
        n_params = len(sig.parameters)
        if n_params >= 2:
            return self._execute(state, agent_input)
        return self._execute(agent_input)

    def _execute(self, state, agent_input):
        raise NotImplementedError(
            f"{self.NAME}._execute(state, agent_input) must be implemented by subclass"
        )

    def run(self, agent_input: dict) -> dict:
        print(f"\n┌─ [{self.NAME}] starting (run={self.run_id})")
        t_start = time.time()

        state = PipelineState.load(self.run_id)

        # 1. Validate + merge READS_STATE into input
        try:
            merged_input = self._validate_and_merge_input(state, agent_input)
        except ValidationError as e:
            print(f"└─ [{self.NAME}] INPUT VALIDATION FAILED: {e}")
            save_artifact(self.run_id, self.NAME, "input", agent_input or {})
            save_artifact(self.run_id, self.NAME, "metadata", {
                "agent": self.NAME,
                "status": "failed",
                "validation_passed": False,
                "validation_problems": [str(e)],
                "reads_state": self.READS_STATE,
                "writes_state": self.WRITES_STATE,
                "duration_seconds": round(time.time() - t_start, 2),
                "completed_at": datetime.now().isoformat(),
            })
            state.mark_agent_failed(self.NAME, error=str(e))
            raise

        # 2. Persist input
        save_artifact(self.run_id, self.NAME, "input", merged_input)

        # 3. Start SQL record (best-effort)
        sql_run_id = None
        try:
            sql_run_id = start_agent_run(
                self.NAME,
                cluster_id=self.cluster_id,
                article_id=self.article_id,
                input_summary=str(list(merged_input.keys()))[:500],
            )
        except Exception:
            pass

        # 4. Execute with retries
        last_problems = []
        problems_history = []
        output = None
        attempt = 0

        with _capture_console() as (cap_out, cap_err):
            while attempt < self.MAX_VALIDATION_RETRIES:
                attempt += 1
                # Expose attempt + last problems to subclasses so they can
                # adjust prompts on retry
                self._retry_attempt = attempt
                self._validation_problems = last_problems
                self._retry_problems = last_problems
                if attempt > 1:
                    print(f"   [{self.NAME}] retry {attempt}/{self.MAX_VALIDATION_RETRIES} "
                          f"due to validation problems: {last_problems}")
                try:
                    output = self._call_execute(state, merged_input)
                except Exception as e:
                    print(f"   [{self.NAME}] EXECUTION ERROR: {type(e).__name__}: {e}")
                    raise
                last_problems = self._validate_output(output)
                if last_problems:
                    problems_history.append(last_problems)
                else:
                    break
            console_text = cap_out.getvalue() + cap_err.getvalue()

        validation_passed = (len(last_problems) == 0)

        # 5. Push WRITES_STATE values into state
        if validation_passed and self.WRITES_STATE and isinstance(output, dict):
            for key in self.WRITES_STATE:
                if key in output:
                    state.set(key, output[key])
            # If there's a single WRITES_STATE key and output doesn't nest it,
            # push the whole output under that key.
            if (
                len(self.WRITES_STATE) == 1
                and self.WRITES_STATE[0] not in output
            ):
                state.set(self.WRITES_STATE[0], output)

        # 6. Persist
        save_artifact(self.run_id, self.NAME, "output", output or {})
        save_artifact(self.run_id, self.NAME, "console", console_text)

        meta = {
            "agent": self.NAME,
            "status": "completed" if validation_passed else "failed",
            "validation_passed": validation_passed,
            "validation_problems": last_problems,
            "problems_history": problems_history,
            "reads_state": self.READS_STATE,
            "writes_state": self.WRITES_STATE,
            "serp_calls": self.serp_calls,
            "serp_cache_hits": self.serp_cache_hits,
            "llm_calls": self.llm_calls,
            "llm_cache_hits": self.llm_cache_hits,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 6),
            "duration_seconds": round(time.time() - t_start, 2),
            "completed_at": datetime.now().isoformat(),
            "attempts": attempt,
        }
        save_artifact(self.run_id, self.NAME, "metadata", meta)

        if sql_run_id:
            try:
                complete_agent_run(
                    sql_run_id,
                    status=meta["status"],
                    cost_usd=self.cost_usd,
                    tokens_in=self.tokens_in,
                    tokens_out=self.tokens_out,
                )
            except Exception:
                pass

        if validation_passed:
            state.mark_agent_complete(self.NAME)
            print(f"└─ [{self.NAME}] complete in {meta['duration_seconds']}s "
                  f"(${self.cost_usd:.4f}, {self.serp_calls} SERP, {self.llm_calls} LLM)")
        else:
            state.mark_agent_failed(self.NAME, error=str(last_problems))
            print(f"└─ [{self.NAME}] FAILED validation: {last_problems}")
            raise ValidationError(
                f"{self.NAME} output validation failed: {last_problems}"
            )

        return output