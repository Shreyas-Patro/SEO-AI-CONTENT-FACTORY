"""
Base Agent v3 — fixes the retry-doesn't-actually-re-call-LLM bug.

KEY FIX (round 3):
- Previously: retries called _execute() again, but _execute() used the same
  cache_namespace, so the cache returned the same broken result. Retry logic
  was running 3x but always returning the same parsed output.
- Fix: AgentBase now passes a `_retry_attempt` integer into _execute, and
  subclasses must include this in their cache_namespace.
- Also: subclasses can read self._retry_problems to see what failed last time
  and adjust the prompt accordingly.
"""

import time
from db.artifacts import (
    save_artifact, load_artifact, increment_run_counters,
    update_pipeline_run
)
from db.sqlite_ops import (
    start_agent_run, complete_agent_run, fail_agent_run, _now
)


class ValidationError(Exception):
    pass


class AgentBase:
    NAME = "base"
    INPUT_REQUIRED = []
    OUTPUT_REQUIRED = []
    OUTPUT_NON_EMPTY = []
    MAX_VALIDATION_RETRIES = 2

    def __init__(self, pipeline_run_id, cluster_id=None, article_id=None):
        self.run_id = pipeline_run_id
        self.cluster_id = cluster_id
        self.article_id = article_id
        self.serp_calls = 0
        self.llm_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        # Set by validate-retry loop, readable by _execute() subclass impls
        self._retry_attempt = 0
        self._retry_problems = []

    # ─── Validation ──────────────────────────────────────────────────────
    def validate_input(self, payload):
        if not isinstance(payload, dict):
            raise ValidationError(f"{self.NAME}: input must be a dict, got {type(payload)}")
        missing = [k for k in self.INPUT_REQUIRED if k not in payload]
        if missing:
            raise ValidationError(f"{self.NAME}: input missing required fields: {missing}")
        return payload

    def validate_output(self, output):
        problems = []
        if not isinstance(output, dict):
            return False, [f"output is not a dict (got {type(output).__name__})"]

        for key in self.OUTPUT_REQUIRED:
            if key not in output:
                problems.append(f"missing required field: {key}")

        for key in self.OUTPUT_NON_EMPTY:
            v = output.get(key)
            if v is None or v == "" or v == [] or v == {}:
                problems.append(f"field '{key}' is empty")

        return (len(problems) == 0), problems

    def _execute(self, validated_input):
        raise NotImplementedError("Subclasses must implement _execute")

    def _output_summary(self, output):
        if not isinstance(output, dict):
            return "non-dict output"
        keys = list(output.keys())[:5]
        return f"keys: {keys}"

    # ─── Helpers for subclasses ──────────────────────────────────────────
    def _track_llm(self, llm_result):
        self.llm_calls += 1
        self.tokens_in += llm_result.get("tokens_in", 0)
        self.tokens_out += llm_result.get("tokens_out", 0)
        self.cost_usd += llm_result.get("cost_usd", 0.0)

    def _track_serp(self, count=1):
        self.serp_calls += count

    def _retry_suffix(self):
        """
        Subclasses MUST include this in their cache_namespace string so
        retries don't return cached broken results.
        """
        return f"::retry{self._retry_attempt}" if self._retry_attempt > 0 else ""

    # ─── Main entry point ────────────────────────────────────────────────
    def run(self, agent_input):
        # 1. Persist input immediately
        save_artifact(self.run_id, self.NAME, "input", agent_input)

        # 2. Validate input
        validated = self.validate_input(agent_input)

        sql_run_id = start_agent_run(
            self.NAME,
            cluster_id=self.cluster_id,
            article_id=self.article_id,
            input_summary=f"Topic: {validated.get('topic', validated.get('article_title', 'n/a'))}"
        )

        update_pipeline_run(self.run_id, current_stage=self.NAME)

        output = None
        problems_history = []
        last_problems = []

        # 3. Execute with retry — each retry bumps _retry_attempt so cache is busted
        max_attempts = self.MAX_VALIDATION_RETRIES + 1
        for attempt in range(1, max_attempts + 1):
            self._retry_attempt = attempt - 1
            self._retry_problems = last_problems

            try:
                t0 = time.time()
                output = self._execute(validated)
                elapsed = time.time() - t0

                if not isinstance(output, dict):
                    output = {"error": "agent returned non-dict", "raw": str(output)[:500]}

                is_valid, problems = self.validate_output(output)
                if is_valid:
                    print(f"  ✅ {self.NAME} output valid (attempt {attempt}, {elapsed:.1f}s)")
                    break

                problems_history.append({"attempt": attempt, "problems": problems})
                last_problems = problems

                if attempt < max_attempts:
                    print(f"  ⚠️  {self.NAME} validation failed: {problems}. Re-running with cache-busted prompt ({attempt}/{self.MAX_VALIDATION_RETRIES})...")
                    # Don't return early; loop will re-call _execute with bumped _retry_attempt
                    continue
                else:
                    print(f"  ❌ {self.NAME} validation FAILED after {self.MAX_VALIDATION_RETRIES} retries. Continuing with partial output.")

            except Exception as e:
                err_msg = f"{self.NAME} execution failed: {type(e).__name__}: {e}"
                print(f"  ❌ {err_msg}")
                fail_agent_run(sql_run_id, error_log=err_msg)
                save_artifact(self.run_id, self.NAME, "output", {
                    "error": err_msg,
                    "agent": self.NAME,
                })
                save_artifact(self.run_id, self.NAME, "metadata", {
                    "status": "failed",
                    "error": err_msg,
                    "attempts": attempt,
                    "problems_history": problems_history,
                    "cost_usd": round(self.cost_usd, 6),
                    "llm_calls": self.llm_calls,
                    "serp_calls": self.serp_calls,
                })
                raise

        # 4. Save output + metadata
        save_artifact(self.run_id, self.NAME, "output", output)

        metadata = {
            "agent": self.NAME,
            "status": "completed",
            "validation_passed": (len(last_problems) == 0),
            "validation_problems": last_problems,
            "problems_history": problems_history,
            "serp_calls": self.serp_calls,
            "llm_calls": self.llm_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 6),
            "completed_at": _now(),
            "retry_count": self._retry_attempt,
        }
        save_artifact(self.run_id, self.NAME, "metadata", metadata)

        complete_agent_run(
            sql_run_id,
            output_summary=self._output_summary(output),
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost_usd=self.cost_usd,
        )
        increment_run_counters(
            self.run_id,
            cost=self.cost_usd,
            serp_calls=self.serp_calls,
            llm_calls=self.llm_calls,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
        )

        return output