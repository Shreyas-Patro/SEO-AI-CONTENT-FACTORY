"""
graph/nodes.py — node functions for the LangGraph.

Each node:
  • Receives the LangGraph state
  • Loads on-disk PipelineState
  • Runs an agent
  • Updates both states
  • Returns the LangGraph state delta
"""
from typing import Dict, Any
from db.pipeline_state import PipelineState, StateKeys
from db.artifacts import get_pipeline_run, increment_run_counters, save_artifact, update_pipeline_run
from db.sqlite_ops import get_articles_by_cluster, get_article


# ─── LAYER 1 ────────────────────────────────────────────────────────────
def node_trend_scout(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.trend_scout import run_trend_scout
    run_id = graph_state["run_id"]
    topic = graph_state["topic"]

    update_pipeline_run(run_id, current_stage="layer1_trend_scout")
    save_artifact(run_id, "trend_scout", "input", {"topic": topic})

    result = run_trend_scout(topic, cluster_id=graph_state.get("cluster_id"))

    save_artifact(run_id, "trend_scout", "output", result)
    save_artifact(run_id, "trend_scout", "metadata", {
        "agent": "trend_scout", "status": "completed",
        "validation_passed": True, "validation_problems": [],
        "serp_calls": result.get("serp_calls_used", 0),
        "llm_calls": 1, "cost_usd": result.get("cost_usd", 0),
    })
    increment_run_counters(run_id, cost=result.get("cost_usd", 0),
                           serp_calls=result.get("serp_calls_used", 0), llm_calls=1)

    state = PipelineState.load(run_id)
    state.set(StateKeys.TREND_DATA, result)

    return {
        "current_stage": "layer1_competitor_spy",
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + result.get("cost_usd", 0),
        "total_serp_calls": graph_state.get("total_serp_calls", 0) + result.get("serp_calls_used", 0),
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + 1,
    }


def node_competitor_spy(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.competitor_spy import CompetitorSpyAgent
    run_id = graph_state["run_id"]
    topic = graph_state["topic"]

    update_pipeline_run(run_id, current_stage="layer1_competitor_spy")
    agent = CompetitorSpyAgent(run_id, cluster_id=graph_state.get("cluster_id"))
    agent.run({"topic": topic})
    increment_run_counters(run_id, cost=agent.cost_usd, serp_calls=agent.serp_calls, llm_calls=agent.llm_calls)

    return {
        "current_stage": "layer1_keyword_mapper",
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_serp_calls": graph_state.get("total_serp_calls", 0) + agent.serp_calls,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
    }


def node_keyword_mapper(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.keyword_mapper import KeywordMapperAgent
    run_id = graph_state["run_id"]

    update_pipeline_run(run_id, current_stage="layer1_keyword_mapper")
    agent = KeywordMapperAgent(run_id, cluster_id=graph_state.get("cluster_id"))
    agent.run({"topic": graph_state["topic"]})
    increment_run_counters(run_id, cost=agent.cost_usd, llm_calls=agent.llm_calls)

    update_pipeline_run(run_id, current_stage="gate_pending", gate_status="pending")
    return {
        "current_stage": "gate_pending",
        "layer1_done": True,
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
    }


# ─── LAYER 2 ────────────────────────────────────────────────────────────
def node_content_architect(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.content_architect import ContentArchitectAgent
    run_id = graph_state["run_id"]

    update_pipeline_run(run_id, current_stage="layer2_content_architect")
    agent = ContentArchitectAgent(run_id, cluster_id=graph_state.get("cluster_id"))
    result = agent.run({"topic": graph_state["topic"]})
    cluster_id = result.get("cluster_id")
    update_pipeline_run(run_id, cluster_id=cluster_id)

    state = PipelineState.load(run_id)
    state.set(StateKeys.CLUSTER_ID, cluster_id)
    increment_run_counters(run_id, cost=agent.cost_usd, llm_calls=agent.llm_calls)

    return {
        "cluster_id": cluster_id,
        "current_stage": "layer2_faq_architect",
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
    }


def node_faq_architect(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.faq_architect import FAQArchitectAgent
    run_id = graph_state["run_id"]
    cluster_id = graph_state["cluster_id"]

    update_pipeline_run(run_id, current_stage="layer2_faq_architect")
    agent = FAQArchitectAgent(run_id, cluster_id=cluster_id)
    agent.run({"topic": graph_state["topic"], "cluster_id": cluster_id})
    increment_run_counters(run_id, cost=agent.cost_usd, llm_calls=agent.llm_calls)

    return {
        "current_stage": "layer2_research_prompt",
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
    }


def node_research_prompt(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.research_prompt_generator import ResearchPromptGeneratorAgent
    run_id = graph_state["run_id"]

    update_pipeline_run(run_id, current_stage="layer2_research_prompt")
    agent = ResearchPromptGeneratorAgent(run_id, cluster_id=graph_state.get("cluster_id"))
    agent.run({"topic": graph_state["topic"]})
    increment_run_counters(run_id, cost=agent.cost_usd, llm_calls=agent.llm_calls)

    update_pipeline_run(run_id, current_stage="layer2_done")
    return {
        "current_stage": "layer2_done",
        "layer2_done": True,
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
    }


# ─── LAYER 3 (per-article) ──────────────────────────────────────────────
def node_layer3_init(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    """Builds the article queue."""
    run_id = graph_state["run_id"]
    cluster_id = graph_state.get("cluster_id")
    if not cluster_id:
        return {"errors": [{"node": "layer3_init", "error": "no cluster_id"}]}

    articles = get_articles_by_cluster(cluster_id)
    queue = [a["id"] for a in articles if a["status"] in ("planned", "draft")]
    print(f"[layer3_init] {len(queue)} articles to process")
    return {
        "articles_queue": queue,
        "current_article_id": queue[0] if queue else None,
        "current_stage": "layer3_writing",
        "article_iteration": {},
        "article_scores": {},
    }


def node_lead_writer(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.lead_writer import LeadWriterAgent
    run_id = graph_state["run_id"]
    cluster_id = graph_state.get("cluster_id")
    article_id = graph_state["current_article_id"]
    iteration = graph_state.get("article_iteration", {}).get(article_id, 0)

    print(f"[lead_writer] article={article_id} iter={iteration}")

    agent = LeadWriterAgent(run_id, cluster_id=cluster_id, article_id=article_id)
    agent_input = {
        "article_id": article_id,
        "topic": graph_state["topic"],
    }
    # If we're on iteration > 0, this is a retry — pass in feedback
    if iteration > 0:
        # In rewriter pattern, the rewriter handles fixes — writer doesn't see feedback
        pass

    agent.run(agent_input)
    increment_run_counters(run_id, cost=agent.cost_usd, llm_calls=agent.llm_calls)

    return {
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
        "article_iteration": {article_id: iteration + 1},
    }


def node_fact_verifier(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.fact_verifier import FactVerifierAgent
    run_id = graph_state["run_id"]
    cluster_id = graph_state.get("cluster_id")
    article_id = graph_state["current_article_id"]

    agent = FactVerifierAgent(run_id, cluster_id=cluster_id, article_id=article_id)
    output = agent.run({"article_id": article_id})
    increment_run_counters(run_id, cost=agent.cost_usd, llm_calls=agent.llm_calls)

    score = output.get("fact_check_score", 0.7)
    scores = graph_state.get("article_scores", {}).get(article_id, {})
    scores["fact"] = score
    return {
        "last_fact_verifier_output": output,
        "article_scores": {article_id: scores},
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
    }


def node_brand_auditor(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.brand_auditor import BrandAuditorAgent
    run_id = graph_state["run_id"]
    cluster_id = graph_state.get("cluster_id")
    article_id = graph_state["current_article_id"]

    agent = BrandAuditorAgent(run_id, cluster_id=cluster_id, article_id=article_id)
    output = agent.run({"article_id": article_id})
    increment_run_counters(run_id, cost=agent.cost_usd, llm_calls=agent.llm_calls)

    brand_score = output.get("brand_score", 7.0)
    readability = output.get("readability", {}).get("flesch_kincaid", 60)
    scores = graph_state.get("article_scores", {}).get(article_id, {})
    scores["brand"] = brand_score
    scores["readability"] = readability
    return {
        "last_brand_auditor_output": output,
        "article_scores": {article_id: scores},
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
    }


def node_rewriter(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.rewriter import RewriterAgent
    run_id = graph_state["run_id"]
    cluster_id = graph_state.get("cluster_id")
    article_id = graph_state["current_article_id"]

    fact_output = graph_state.get("last_fact_verifier_output", {}) or {}
    brand_output = graph_state.get("last_brand_auditor_output", {}) or {}

    fact_issues = [
        c for c in fact_output.get("verification", {}).get("claims", [])
        if c.get("status") in ("flagged", "unverifiable")
    ]
    brand_issues = brand_output.get("flagged_passages", []) or []
    readability = brand_output.get("readability", {}).get("flesch_kincaid", 60)

    agent = RewriterAgent(run_id, cluster_id=cluster_id, article_id=article_id)
    agent.run({
        "article_id": article_id,
        "fact_issues": fact_issues,
        "brand_issues": brand_issues,
        "readability_score": readability,
    })
    increment_run_counters(run_id, cost=agent.cost_usd, llm_calls=agent.llm_calls)

    iteration = graph_state.get("article_iteration", {}).get(article_id, 1)
    return {
        "article_iteration": {article_id: iteration + 1},
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
    }


def node_meta_tagger(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.meta_tagger import MetaTaggerAgent
    run_id = graph_state["run_id"]
    cluster_id = graph_state.get("cluster_id")
    article_id = graph_state["current_article_id"]

    agent = MetaTaggerAgent(run_id, cluster_id=cluster_id, article_id=article_id)
    agent.run({"article_id": article_id})
    increment_run_counters(run_id, cost=agent.cost_usd, llm_calls=agent.llm_calls)

    return {
        "completed_articles": [article_id],
        "total_cost_usd": graph_state.get("total_cost_usd", 0) + agent.cost_usd,
        "total_llm_calls": graph_state.get("total_llm_calls", 0) + agent.llm_calls,
    }


def node_advance_queue(graph_state: Dict[str, Any]) -> Dict[str, Any]:
    """Pops the current article and moves to the next one."""
    queue = graph_state.get("articles_queue", []) or []
    completed = graph_state.get("completed_articles", []) or []
    remaining = [a for a in queue if a not in completed]
    if remaining and graph_state.get("current_article_id") == remaining[0]:
        remaining = remaining[1:]
    next_id = remaining[0] if remaining else None
    return {
        "current_article_id": next_id,
        "articles_queue": remaining,
    }