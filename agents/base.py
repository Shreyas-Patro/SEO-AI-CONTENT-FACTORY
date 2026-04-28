"""
agents/base.py — AgentBase v5.0 (FIXED)

FIXES FROM v4.2:
    - Added _track_llm(result) convenience method that agents actually call
    - Added _retry_suffix() for cache namespace differentiation
    - validate_output is now _validate_output (consistent naming)
    - Better error messages
"""

import io
import sys
import time
import inspect
import contextlib
from datetime import datetime

from db.artifacts import save_artifact
from db.pipeline_state import PipelineState

try:
    from db.sqlite_ops import start_agent_run, complete_agent_run
except Exception:
    def start_agent_run(*args, **kwargs):
        return None
    def complete_agent_run(*args, **kwargs):
        return None


class ValidationError(Exception):
    pass


class _ConsoleCapture(io.StringIO):
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

        self._retry_attempt = 0
        self._validation_problems = []
        self._retry_problems = []

    def __getattr__(self, name):
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

    # ─── Cost tracking ─────────────────────────────────────────────────
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

    def _track_llm(self, result):
        """Convenience: extract cost info from call_llm_json wrapper dict."""
        if not isinstance(result, dict):
            return
        self.track_llm(
            tokens_in=result.get("tokens_in", 0) or 0,
            tokens_out=result.get("tokens_out", 0) or 0,
            cost=result.get("cost_usd", 0) or 0,
            cache_hit=bool(result.get("cached")),
        )

    def _retry_suffix(self):
        """Return a string to append to cache keys on retry, so we don't
        get the same broken cached response."""
        if self._retry_attempt > 1:
            return f":retry{self._retry_attempt}"
        return ""

    # ─── Validation ────────────────────────────────────────────────────
    def _validate_and_merge_input(self, state, agent_input):
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
                f"Available: {available}"
            )
        return merged

    def _validate_output(self, output):
        """Override this in subclasses for custom validation.
        Must return a LIST of problem strings. Empty list = valid."""
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

    # ─── Execution ─────────────────────────────────────────────────────
    def _call_execute(self, state, agent_input):
        sig = inspect.signature(self._execute)
        n_params = len(sig.parameters)
        if n_params >= 2:
            return self._execute(state, agent_input)
        return self._execute(agent_input)

    def _execute(self, state, agent_input):
        raise NotImplementedError(
            f"{self.NAME}._execute() must be implemented by subclass"
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
                "agent": self.NAME, "status": "failed",
                "validation_passed": False, "validation_problems": [str(e)],
                "duration_seconds": round(time.time() - t_start, 2),
                "completed_at": datetime.now().isoformat(),
            })
            state.mark_agent_failed(self.NAME, error=str(e))
            raise

        # 2. Persist input
        save_artifact(self.run_id, self.NAME, "input", merged_input)

        # 3. SQL record
        sql_run_id = None
        try:
            sql_run_id = start_agent_run(
                self.NAME, cluster_id=self.cluster_id,
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
                self._retry_attempt = attempt
                self._validation_problems = last_problems
                self._retry_problems = last_problems

                if attempt > 1:
                    print(f"   [{self.NAME}] retry {attempt}/{self.MAX_VALIDATION_RETRIES} "
                          f"— problems: {last_problems}")
                try:
                    output = self._call_execute(state, merged_input)
                except Exception as e:
                    print(f"   [{self.NAME}] EXECUTION ERROR: {type(e).__name__}: {e}")
                    # Save what we have before re-raising
                    save_artifact(self.run_id, self.NAME, "console",
                                  cap_out.getvalue() + cap_err.getvalue())
                    raise

                last_problems = self._validate_output(output)
                if last_problems:
                    problems_history.append(last_problems)
                else:
                    break

            console_text = cap_out.getvalue() + cap_err.getvalue()

        validation_passed = (len(last_problems) == 0)

        # 5. Push WRITES_STATE into PipelineState
        if validation_passed and self.WRITES_STATE and isinstance(output, dict):
            for key in self.WRITES_STATE:
                if key in output:
                    state.set(key, output[key])
            if (len(self.WRITES_STATE) == 1
                    and self.WRITES_STATE[0] not in output):
                state.set(self.WRITES_STATE[0], output)

        # 6. Persist everything
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
                    sql_run_id, status=meta["status"],
                    cost_usd=self.cost_usd, tokens_in=self.tokens_in,
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