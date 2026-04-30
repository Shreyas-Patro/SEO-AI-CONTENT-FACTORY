"""
graph/pipeline.py — builds the LangGraph.
"""
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from graph.state import PipelineGraphState
from graph.nodes import (
    node_trend_scout, node_competitor_spy, node_keyword_mapper,
    node_content_architect, node_faq_architect, node_research_prompt,
    node_layer3_init, node_lead_writer, node_fact_verifier,
    node_brand_auditor, node_rewriter, node_meta_tagger, node_advance_queue,
)
from graph.edges import supervisor_after_audit, supervisor_after_advance


def build_layer1_graph():
    """Layer 1 only — runs to gate_pending and stops."""
    g = StateGraph(PipelineGraphState)
    g.add_node("trend_scout", node_trend_scout)
    g.add_node("competitor_spy", node_competitor_spy)
    g.add_node("keyword_mapper", node_keyword_mapper)
    g.set_entry_point("trend_scout")
    g.add_edge("trend_scout", "competitor_spy")
    g.add_edge("competitor_spy", "keyword_mapper")
    g.add_edge("keyword_mapper", END)
    return g.compile(checkpointer=MemorySaver())


def build_layer2_graph():
    """Layer 2 only — runs after gate is approved."""
    g = StateGraph(PipelineGraphState)
    g.add_node("content_architect", node_content_architect)
    g.add_node("faq_architect", node_faq_architect)
    g.add_node("research_prompt", node_research_prompt)
    g.set_entry_point("content_architect")
    g.add_edge("content_architect", "faq_architect")
    g.add_edge("faq_architect", "research_prompt")
    g.add_edge("research_prompt", END)
    return g.compile(checkpointer=MemorySaver())


def build_layer3_graph():
    """
    Layer 3 with quality loop.

    init → writer → fact_verifier → brand_auditor → SUPERVISOR
                                                    ├─ rewriter → fact_verifier (loop)
                                                    └─ meta_tagger → advance_queue
                                                                       ├─ lead_writer (next article)
                                                                       └─ END
    """
    g = StateGraph(PipelineGraphState)
    g.add_node("layer3_init", node_layer3_init)
    g.add_node("lead_writer", node_lead_writer)
    g.add_node("fact_verifier", node_fact_verifier)
    g.add_node("brand_auditor", node_brand_auditor)
    g.add_node("rewriter", node_rewriter)
    g.add_node("meta_tagger", node_meta_tagger)
    g.add_node("advance_queue", node_advance_queue)

    g.set_entry_point("layer3_init")
    g.add_edge("layer3_init", "lead_writer")
    g.add_edge("lead_writer", "fact_verifier")
    g.add_edge("fact_verifier", "brand_auditor")
    g.add_conditional_edges(
        "brand_auditor",
        supervisor_after_audit,
        {"rewriter": "rewriter", "meta_tagger": "meta_tagger"},
    )
    g.add_edge("rewriter", "fact_verifier")  # loop back through audit chain
    g.add_edge("meta_tagger", "advance_queue")
    g.add_conditional_edges(
        "advance_queue",
        supervisor_after_advance,
        {"lead_writer": "lead_writer", "END": END},
    )
    return g.compile(checkpointer=MemorySaver())