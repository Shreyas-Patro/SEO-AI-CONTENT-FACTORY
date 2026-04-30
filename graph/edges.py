"""
graph/edges.py — conditional edges (the supervisor's decisions).

These functions return the NAME of the next node to run.
"""
from typing import Dict, Any
from config_loader import cfg


def supervisor_after_audit(graph_state: Dict[str, Any]) -> str:
    """
    The core quality loop decision.

    Inputs: graph_state.last_fact_verifier_output, last_brand_auditor_output
    Output: "rewriter" if fixes needed AND iteration cap not reached,
            else "meta_tagger".
    """
    article_id = graph_state.get("current_article_id")
    if not article_id:
        return "advance_queue"

    iter_count = graph_state.get("article_iteration", {}).get(article_id, 1)
    max_iter = cfg.get("quality", {}).get("max_quality_loop_iterations", 2)

    fact_score = (graph_state.get("last_fact_verifier_output", {}) or {}).get("fact_check_score", 1.0)
    brand_out = graph_state.get("last_brand_auditor_output", {}) or {}
    brand_score = brand_out.get("brand_score", 10)
    readability = (brand_out.get("readability") or {}).get("flesch_kincaid", 100)
    brand_flags = len(brand_out.get("flagged_passages", []) or [])

    min_fact = cfg.get("quality", {}).get("min_fact_check_confidence", 0.85)
    min_brand = cfg.get("quality", {}).get("min_brand_tone_score", 7.0)
    min_read = cfg.get("quality", {}).get("min_readability_score", 55)

    needs_fix = (
        fact_score < min_fact
        or brand_score < min_brand
        or readability < min_read
        or brand_flags > 3
    )

    if needs_fix and iter_count < max_iter:
        print(f"[supervisor] {article_id} iter={iter_count} → REWRITE "
              f"(fact={fact_score:.2f}, brand={brand_score:.1f}, read={readability:.0f}, flags={brand_flags})")
        return "rewriter"

    if needs_fix:
        print(f"[supervisor] {article_id} iter={iter_count} → MAX REACHED, accepting")
    else:
        print(f"[supervisor] {article_id} → PASS")
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