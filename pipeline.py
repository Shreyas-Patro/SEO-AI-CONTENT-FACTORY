"""
Pipeline Orchestrator

Single entry point that runs Layer 1 → Gate → Layer 2 → Research Prompt Gen.

Each agent reads its input from the artifact store of the previous agent's
output, and writes its own output back. This is the LangGraph-compatible
pattern — when you migrate to LangGraph later, this orchestrator becomes
the StateGraph and each agent becomes a node.

Usage from the dashboard:

    from pipeline import (
        start_pipeline_run, run_layer1, run_layer2,
        load_agent_output, edit_agent_output
    )

    run_id = start_pipeline_run("Hosa Road")
    run_layer1(run_id)            # trend → competitor → keyword
    # human gate happens in dashboard
    run_layer2(run_id)            # content_arch → faq_arch → research_prompt
"""

import json
from db.artifacts import (
    init_artifact_tables, create_pipeline_run, update_pipeline_run,
    save_artifact, load_artifact, get_pipeline_run
)
from db.sqlite_ops import init_db


# Initialize on import — safe to run repeatedly
init_db()
init_artifact_tables()


# ─── PUBLIC API ──────────────────────────────────────────────────────────

def start_pipeline_run(topic, notes=""):
    """Create a new pipeline run. Returns run_id."""
    run_id = create_pipeline_run(topic, notes=notes)
    save_artifact(run_id, "_pipeline", "input", {"topic": topic, "notes": notes})
    return run_id


def run_layer1(run_id):
    """
    Run Layer 1: Trend Scout → Competitor Spy → Keyword Mapper.
    Reads topic from the pipeline run and writes each agent's output to artifacts.
    """
    from agents.trend_scout import run_trend_scout
    from agents.competitor_spy import CompetitorSpyAgent
    from agents.keyword_mapper import KeywordMapperAgent

    run = get_pipeline_run(run_id)
    if not run:
        raise ValueError(f"Pipeline run {run_id} not found")
    topic = run["topic"]

    update_pipeline_run(run_id, current_stage="layer1_trend_scout")

    # ─── 1. Trend Scout ──────────────────────────────────
    # NOTE: trend_scout.py is your existing agent — wrap it minimally.
    # Save its full output as artifact.
    print(f"\n{'='*60}\n[Pipeline {run_id}] Layer 1 starting for '{topic}'\n{'='*60}")

    save_artifact(run_id, "trend_scout", "input", {"topic": topic})
    trend_data = run_trend_scout(topic, cluster_id=run.get("cluster_id"))
    save_artifact(run_id, "trend_scout", "output", trend_data)
    save_artifact(run_id, "trend_scout", "metadata", {
        "agent": "trend_scout",
        "cost_usd": trend_data.get("cost_usd", 0),
        "serp_calls_used": trend_data.get("serp_calls_used", 15),
    })
    update_pipeline_run(run_id, current_stage="layer1_competitor_spy")

    # ─── 2. Competitor Spy ───────────────────────────────
    spy = CompetitorSpyAgent(run_id, cluster_id=run.get("cluster_id"))
    spy_output = spy.run({"topic": topic})
    update_pipeline_run(run_id, current_stage="layer1_keyword_mapper")

    # ─── 3. Keyword Mapper ───────────────────────────────
    mapper = KeywordMapperAgent(run_id, cluster_id=run.get("cluster_id"))
    # Reformat for keyword mapper expected shape
    competitor_data_for_mapper = {
        "analysis": {
            "competitor_coverage": spy_output["competitor_coverage"],
            "coverage_gaps": spy_output["coverage_gaps"],
            "our_advantages": spy_output["our_advantages"],
        },
        "raw_results": spy_output["raw_results"],
    }
    keyword_output = mapper.run({
        "topic": topic,
        "trend_data": trend_data,
        "competitor_data": competitor_data_for_mapper,
    })

    update_pipeline_run(run_id, current_stage="gate_pending", gate_status="pending")
    print(f"\n[Pipeline {run_id}] Layer 1 complete. Awaiting human gate.")

    return {
        "trend_scout": trend_data,
        "competitor_spy": spy_output,
        "keyword_mapper": keyword_output,
    }


def approve_gate(run_id):
    """Mark the human gate as approved so Layer 2 can run."""
    update_pipeline_run(run_id, gate_status="approved", current_stage="gate_approved")


def reject_gate(run_id):
    update_pipeline_run(run_id, gate_status="rejected", status="cancelled")


def run_layer2(run_id):
    """
    Run Layer 2: Content Architect → FAQ Architect → Research Prompt Generator.
    Reads previous agents' output from artifact store.
    """
    from agents.content_architect import run_content_architect
    from agents.faq_architect import run_faq_architect
    from agents.research_prompt_generator import ResearchPromptGeneratorAgent
    from db.sqlite_ops import get_articles_by_cluster

    run = get_pipeline_run(run_id)
    if run["gate_status"] != "approved":
        raise RuntimeError(f"Gate not approved. Current status: {run['gate_status']}")

    topic = run["topic"]
    keyword_output = load_artifact(run_id, "keyword_mapper", "output")
    if not keyword_output:
        raise RuntimeError("Layer 1 keyword_mapper output missing — run Layer 1 first")

    # ─── 4. Content Architect ────────────────────────────
    update_pipeline_run(run_id, current_stage="layer2_content_architect")
    save_artifact(run_id, "content_architect", "input", {
        "topic": topic,
        "keyword_data": {"keyword_map": keyword_output},
    })

    arch_result = run_content_architect(
        topic,
        {"keyword_map": keyword_output},
        cluster_id=run.get("cluster_id"),
    )
    save_artifact(run_id, "content_architect", "output", arch_result)
    save_artifact(run_id, "content_architect", "metadata", {
        "agent": "content_architect",
        "articles_created": arch_result.get("articles_created", 0),
        "cost_usd": arch_result.get("cost_usd", 0),
    })

    cluster_id = arch_result.get("cluster_id")
    update_pipeline_run(run_id, cluster_id=cluster_id, current_stage="layer2_faq_architect")

    # ─── 5. FAQ Architect ────────────────────────────────
    db_articles = get_articles_by_cluster(cluster_id) if cluster_id else []
    faqs_by_article = {}
    faq_results = []

    for art in db_articles:
        try:
            faq_res = run_faq_architect(
                art["id"],
                {"keyword_map": keyword_output},
                cluster_id=cluster_id,
            )
            faq_results.append(faq_res)
            faqs_by_article[art["id"]] = faq_res.get("faqs", [])
        except Exception as e:
            print(f"  ⚠️  FAQ Architect failed for {art['id']}: {e}")
            faqs_by_article[art["id"]] = []

    save_artifact(run_id, "faq_architect", "input", {
        "articles": [{"id": a["id"], "title": a["title"]} for a in db_articles],
    })
    save_artifact(run_id, "faq_architect", "output", {
        "faqs_by_article": faqs_by_article,
        "total_articles": len(db_articles),
        "total_faqs": sum(len(v) for v in faqs_by_article.values()),
        "results": faq_results,
    })

    # ─── 6. Research Prompt Generator (NEW) ──────────────
    update_pipeline_run(run_id, current_stage="layer2_research_prompt_gen")
    rpg = ResearchPromptGeneratorAgent(run_id, cluster_id=cluster_id)
    rpg_output = rpg.run({
        "topic": topic,
        "cluster_plan": arch_result.get("cluster_plan", {}),
        "faqs_by_article": faqs_by_article,
    })

    update_pipeline_run(run_id, status="completed", current_stage="done")

    return {
        "content_architect": arch_result,
        "faq_architect": {"faqs_by_article": faqs_by_article, "results": faq_results},
        "research_prompt_generator": rpg_output,
    }


# ─── HELPERS for the dashboard ───────────────────────────────────────────

def load_agent_output(run_id, agent_name):
    """UI helper: read any agent's output."""
    return load_artifact(run_id, agent_name, "output")


def load_agent_input(run_id, agent_name):
    return load_artifact(run_id, agent_name, "input")


def load_agent_metadata(run_id, agent_name):
    return load_artifact(run_id, agent_name, "metadata")


def edit_agent_output(run_id, agent_name, new_output):
    """UI helper for Q11: human edits an agent's output before next agent runs."""
    save_artifact(run_id, agent_name, "output", new_output)


def get_full_run_state(run_id):
    """Build a dashboard-ready snapshot of everything in this run."""
    run = get_pipeline_run(run_id)
    if not run:
        return None
    state = {"run": run, "outputs": {}, "metadata": {}}
    for agent in ["trend_scout", "competitor_spy", "keyword_mapper",
                  "content_architect", "faq_architect", "research_prompt_generator"]:
        state["outputs"][agent] = load_artifact(run_id, agent, "output")
        state["metadata"][agent] = load_artifact(run_id, agent, "metadata")
    return state