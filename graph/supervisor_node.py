"""
graph/supervisor_node.py — the brain of the quality loop.

Runs after brand_auditor. Decides one of three outcomes per article:
  1. PASS — go to meta_tagger
  2. REWRITE — produce a structured rewrite brief, go to rewriter
  3. ESCALATE — flag for human review (write to article history), go to meta_tagger

This node DOES NOT call an LLM. It's pure rule-based reasoning over the
fact_verifier and brand_auditor outputs. Cheap, deterministic, fast.
"""
from typing import Dict, Any
from config_loader import cfg
from db.sqlite_ops import update_article, add_article_history


def node_supervisor(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    article_id = graph_state.get("current_article_id")
    if not article_id:
        return {"supervisor_decision": "advance"}

    iter_count = graph_state.get("article_iteration", {}).get(article_id, 1)
    max_iter = cfg.get("quality", {}).get("max_quality_loop_iterations", 2)

    fact_out  = graph_state.get("last_fact_verifier_output", {}) or {}
    brand_out = graph_state.get("last_brand_auditor_output", {}) or {}

    fact_score   = fact_out.get("fact_check_score", 1.0)
    brand_score  = brand_out.get("brand_score", 10)
    readability  = (brand_out.get("readability") or {}).get("flesch_kincaid", 100)
    flagged_pass = brand_out.get("flagged_passages", []) or []
    flagged_clm  = [
        c for c in fact_out.get("verification", {}).get("claims", [])
        if c.get("status") in ("flagged", "unverifiable")
    ]

    min_fact  = cfg.get("quality", {}).get("min_fact_check_confidence", 0.85)
    min_brand = cfg.get("quality", {}).get("min_brand_tone_score", 7.0)
    min_read  = cfg.get("quality", {}).get("min_readability_score", 55)

    # Gaps (positive = failing, negative = passing)
    fact_gap   = min_fact  - fact_score
    brand_gap  = min_brand - brand_score
    read_gap   = min_read  - readability

    # ── Decision logic ────────────────────────────────────────────────
    REWRITE_GAP_BRAND = 0.5
    REWRITE_GAP_FACT  = 0.05
    REWRITE_GAP_READ  = 8
    HARD_FLAG_LIMIT   = 5

    needs_fix = (
        fact_gap   > REWRITE_GAP_FACT   or
        brand_gap  > REWRITE_GAP_BRAND  or
        read_gap   > REWRITE_GAP_READ   or
        len(flagged_pass) > HARD_FLAG_LIMIT
    )

    # Circuit breaker — has the last iteration actually helped?
    scores = graph_state.get("article_scores", {}).get(article_id, {})
    if iter_count > 1:
        prev = scores.get(f"prev_iter_{iter_count - 1}", {})
        if prev:
            if (brand_score - prev.get("brand", 0) < 0.3 and
                fact_score  - prev.get("fact",  0) < 0.03):
                add_article_history(
                    article_id, "supervisor",
                    f"CIRCUIT BREAKER iter={iter_count} no-progress",
                    ""
                )
                return {
                    "supervisor_decision": "pass",
                    "supervisor_reason": "no-progress circuit breaker",
                    "rewrite_brief": None,
                }

    # Snapshot for next iteration
    scores[f"prev_iter_{iter_count}"] = {
        "brand": brand_score, "fact": fact_score, "read": readability,
    }

    # ── Routing ───────────────────────────────────────────────────────
    if not needs_fix:
        return {
            "supervisor_decision": "pass",
            "supervisor_reason": "all thresholds met",
            "rewrite_brief": None,
            "article_scores": {article_id: scores},
        }

    if iter_count >= max_iter:
        # Hit the iteration cap — accept and flag for human review
        msg = f"MAX ITER. fact={fact_score:.2f} brand={brand_score:.1f} read={readability:.0f}"
        add_article_history(article_id, "supervisor", msg, "")
        update_article(article_id, status="needs_human_review")
        return {
            "supervisor_decision": "pass",
            "supervisor_reason": "max iterations reached, escalated",
            "rewrite_brief": None,
            "article_scores": {article_id: scores},
        }

    # Build a structured rewrite brief — what the rewriter actually needs
    brief = {
        "article_id": article_id,
        "iteration": iter_count + 1,
        "priorities": [],   # ordered list of what to fix first
        "fact_issues": flagged_clm,
        "brand_issues": flagged_pass[:10],  # cap to avoid bloated rewriter prompt
        "readability_target": min_read,
        "current_readability": readability,
        "scores": {
            "fact": fact_score,
            "brand": brand_score,
            "readability": readability,
        },
    }
    if fact_gap > REWRITE_GAP_FACT:
        brief["priorities"].append({
            "dimension": "fact",
            "severity": "high" if fact_gap > 0.15 else "medium",
            "instruction": (
                f"Fix or remove {len(flagged_clm)} factually flagged claim(s). "
                "Do NOT invent new statistics; either cite a verifiable source "
                "or rephrase to remove the unverifiable specific number."
            ),
        })
    if brand_gap > REWRITE_GAP_BRAND:
        brief["priorities"].append({
            "dimension": "brand",
            "severity": "high" if brand_gap > 1.5 else "medium",
            "instruction": (
                f"Address the {len(flagged_pass)} flagged passages by applying their "
                "suggested rewrites verbatim or close to. Tighten any paragraph "
                "lacking a number/date/source."
            ),
        })
    if read_gap > REWRITE_GAP_READ:
        brief["priorities"].append({
            "dimension": "readability",
            "severity": "medium",
            "instruction": (
                f"Lower average sentence length and remove jargon. "
                f"Current Flesch={readability:.0f}, target ≥{min_read}."
            ),
        })

    add_article_history(
        article_id, "supervisor",
        f"REWRITE iter={iter_count+1} priorities={[p['dimension'] for p in brief['priorities']]}",
        ""
    )
    return {
        "supervisor_decision": "rewrite",
        "supervisor_reason": ", ".join(p["dimension"] for p in brief["priorities"]),
        "rewrite_brief": brief,
        "article_scores": {article_id: scores},
    }