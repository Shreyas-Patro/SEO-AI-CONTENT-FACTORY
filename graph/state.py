"""
graph/state.py — LangGraph state, extends your PipelineState.

LangGraph wants a TypedDict. We bridge: every node loads/saves the
on-disk PipelineState, and only puts a SUMMARY in the LangGraph state
so it stays small and fast to checkpoint.
"""
from typing import TypedDict, Optional, List, Dict, Any
from typing_extensions import Annotated
import operator


def _merge_dict(left: dict, right: dict) -> dict:
    """Reducer for dict fields — right wins on conflict."""
    if not isinstance(left, dict):
        left = {}
    if not isinstance(right, dict):
        right = {}
    return {**left, **right}


def _extend_list(left: list, right: list) -> list:
    if not isinstance(left, list): left = []
    if not isinstance(right, list): right = []
    return left + right


class PipelineGraphState(TypedDict, total=False):
    # Run identity
    run_id: str
    topic: str
    cluster_id: Optional[str]

    # Stage tracking
    current_stage: str
    layer1_done: bool
    layer2_done: bool
    gate_status: str  # "pending" | "approved" | "rejected"

    # Article queue for layer 3
    articles_queue: List[str]          # article_ids to process
    current_article_id: Optional[str]
    completed_articles: Annotated[List[str], _extend_list]
    failed_articles: Annotated[List[Dict[str, str]], _extend_list]

    # Quality loop tracking PER ARTICLE
    article_iteration: Annotated[Dict[str, int], _merge_dict]   # {article_id: iteration_count}
    article_scores: Annotated[Dict[str, Dict[str, float]], _merge_dict]
    # e.g. {"art-abc": {"fact": 0.9, "brand": 7.5, "readability": 62}}

    # Latest agent decisions
    last_fact_verifier_output: Dict[str, Any]
    last_brand_auditor_output: Dict[str, Any]

    # Cost tracking
    total_cost_usd: float
    total_llm_calls: int
    total_serp_calls: int

    # Errors
    errors: Annotated[List[Dict[str, str]], _extend_list]