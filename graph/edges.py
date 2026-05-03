"""
graph/edges.py — conditional edges (the supervisor's decisions).

These functions return the NAME of the next node to run.
"""
from typing import Dict, Any
from config_loader import cfg


def supervisor_after_audit(graph_state: Dict[str, Any]) -> str:
    """
    Gap-aware quality gate.

    Rewrite ONLY when:
      - Score is meaningfully below threshold (>= REWRITE_GAP), OR
      - Critical failure (fact_score very low, or many flagged passages)

    Circuit breaker: if last iteration didn't improve score by >= MIN_PROGRESS,
    don't try again — accept and move on.
    """
    article_id = graph_state.get("current_article_id")
    if not article_id:
        return "advance_queue"

    iter_count = graph_state.get("article_iteration", {}).get(article_id, 1)
    max_iter = cfg.get("quality", {}).get("max_quality_loop_iterations", 2)

    fact_score = (graph_state.get("last_fact_verifier_output", {}) or {}).get(
        "fact_check_score", 1.0
    )
    brand_out = graph_state.get("last_brand_auditor_output", {}) or {}
    brand_score = brand_out.get("brand_score", 10)
    readability = (brand_out.get("readability") or {}).get("flesch_kincaid", 100)
    brand_flags = len(brand_out.get("flagged_passages", []) or [])

    min_fact = cfg.get("quality", {}).get("min_fact_check_confidence", 0.85)
    min_brand = cfg.get("quality", {}).get("min_brand_tone_score", 7.0)
    min_read = cfg.get("quality", {}).get("min_readability_score", 55)

    # ── Gap-aware thresholds ──────────────────────────────────────────
    # Only rewrite if the gap is meaningful — saves money on borderline cases.
    REWRITE_GAP_BRAND = 0.5      # rewrite only if >0.5 below threshold
    REWRITE_GAP_FACT  = 0.05     # rewrite only if >0.05 below threshold
    REWRITE_GAP_READ  = 8        # rewrite only if >8 points below
    HARD_FLAG_LIMIT   = 5        # >5 flagged passages → always rewrite

    fact_failing  = fact_score   < (min_fact  - REWRITE_GAP_FACT)
    brand_failing = brand_score  < (min_brand - REWRITE_GAP_BRAND)
    read_failing  = readability  < (min_read  - REWRITE_GAP_READ)
    too_many_flags = brand_flags > HARD_FLAG_LIMIT

    needs_fix = fact_failing or brand_failing or read_failing or too_many_flags

    # ── Circuit breaker: did last rewrite actually help? ───────────────
    if iter_count > 1:
        prev = graph_state.get("article_scores", {}).get(article_id, {}).get(
            f"prev_iter_{iter_count - 1}", {}
        )
        if prev:
            brand_delta = brand_score - prev.get("brand", 0)
            fact_delta  = fact_score  - prev.get("fact",  0)
            # If neither score moved meaningfully, stop trying
            if brand_delta < 0.3 and fact_delta < 0.03:
                print(
                    f"[supervisor] {article_id} iter={iter_count} → CIRCUIT BREAKER "
                    f"(no progress: brand+={brand_delta:.2f}, fact+={fact_delta:.3f})"
                )
                return "meta_tagger"

    # Snapshot current scores so the next iteration can detect progress
    scores = graph_state.get("article_scores", {}).get(article_id, {})
    scores[f"prev_iter_{iter_count}"] = {
        "brand": brand_score, "fact": fact_score, "read": readability
    }

    if needs_fix and iter_count < max_iter:
        reason = []
        if fact_failing:  reason.append(f"fact={fact_score:.2f}<{min_fact}")
        if brand_failing: reason.append(f"brand={brand_score:.1f}<{min_brand}")
        if read_failing:  reason.append(f"read={readability:.0f}<{min_read}")
        if too_many_flags: reason.append(f"flags={brand_flags}>{HARD_FLAG_LIMIT}")
        print(f"[supervisor] {article_id} iter={iter_count} → REWRITE ({', '.join(reason)})")
        return "rewriter"

    if needs_fix:
        print(f"[supervisor] {article_id} iter={iter_count} → MAX ITERATIONS, accepting")
    else:
        print(
            f"[supervisor] {article_id} → PASS "
            f"(brand={brand_score:.1f}, fact={fact_score:.2f}, read={readability:.0f}, flags={brand_flags})"
        )
    return "meta_tagger"

def supervisor_after_advance(graph_state: Dict[str, Any]) -> str:
    """If the queue still has items, loop. Otherwise end."""
    if graph_state.get("current_article_id"):
        return "lead_writer"
    return "END"


def gate_check(graph_state: Dict[str, Any]) -> str:
    """After Layer 1, halt for human approval. Resume only if approved."""
    if graph_state.get("gate_status") == "approved":
        return "content_architect"
    return "WAIT"