"""
Pipeline Orchestrator v2

Changes from v1:
- Uses ContentArchitectAgent (AgentBase, with validation + retry)
- Uses FAQArchitectAgent
- Saves a proper metadata.json for trend_scout (was missing — caused dashboard to crash)
- Cleaner error reporting
"""

import json
from db.artifacts import (
    init_artifact_tables, create_pipeline_run, update_pipeline_run,
    save_artifact, load_artifact, get_pipeline_run,
    increment_run_counters
)
from db.sqlite_ops import init_db


# Initialize on import
init_db()
init_artifact_tables()


def start_pipeline_run(topic, notes=""):
    run_id = create_pipeline_run(topic, notes=notes)
    save_artifact(run_id, "_pipeline", "input", {"topic": topic, "notes": notes})
    return run_id


def run_layer1(run_id):
    from agents.trend_scout import run_trend_scout
    from agents.competitor_spy import CompetitorSpyAgent
    from agents.keyword_mapper import KeywordMapperAgent

    run = get_pipeline_run(run_id)
    if not run:
        raise ValueError(f"Pipeline run {run_id} not found")
    topic = run["topic"]

    update_pipeline_run(run_id, current_stage="layer1_trend_scout")
    print(f"\n{'='*60}\n[Pipeline {run_id}] Layer 1 starting for '{topic}'\n{'='*60}")

    # ─── 1. Trend Scout (legacy wrapper) ─────────────────────────
    save_artifact(run_id, "trend_scout", "input", {"topic": topic})
    trend_data = run_trend_scout(topic, cluster_id=run.get("cluster_id"))
    save_artifact(run_id, "trend_scout", "output", trend_data)

    # NEW: build a proper metadata.json so the dashboard doesn't crash
    ts_cost = trend_data.get("cost_usd", 0)
    ts_serp = trend_data.get("serp_calls_used", 15)
    ts_meta = {
        "agent": "trend_scout",
        "status": "completed",
        "validation_passed": True,
        "validation_problems": [],
        "serp_calls": ts_serp,
        "llm_calls": 1,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": ts_cost,
    }
    save_artifact(run_id, "trend_scout", "metadata", ts_meta)
    increment_run_counters(run_id, cost=ts_cost, serp_calls=ts_serp, llm_calls=1)

    update_pipeline_run(run_id, current_stage="layer1_competitor_spy")

    # ─── 2. Competitor Spy ───────────────────────────────────────
    spy = CompetitorSpyAgent(run_id, cluster_id=run.get("cluster_id"))
    spy_output = spy.run({"topic": topic})
    update_pipeline_run(run_id, current_stage="layer1_keyword_mapper")

    # ─── 3. Keyword Mapper ───────────────────────────────────────
    mapper = KeywordMapperAgent(run_id, cluster_id=run.get("cluster_id"))
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
    update_pipeline_run(run_id, gate_status="approved", current_stage="gate_approved")


def reject_gate(run_id):
    update_pipeline_run(run_id, gate_status="rejected", status="cancelled")


def run_layer2(run_id):
    from agents.content_architect import ContentArchitectAgent
    from agents.faq_architect import FAQArchitectAgent
    from agents.research_prompt_generator import ResearchPromptGeneratorAgent
    from db.sqlite_ops import get_articles_by_cluster

    run = get_pipeline_run(run_id)
    if run["gate_status"] != "approved":
        raise RuntimeError(f"Gate not approved. Current status: {run['gate_status']}")

    topic = run["topic"]
    keyword_output = load_artifact(run_id, "keyword_mapper", "output")
    if not keyword_output:
        raise RuntimeError("Layer 1 keyword_mapper output missing — run Layer 1 first")

    # ─── 4. Content Architect ────────────────────────────────────
    update_pipeline_run(run_id, current_stage="layer2_content_architect")

    architect = ContentArchitectAgent(run_id, cluster_id=run.get("cluster_id"))
    arch_result = architect.run({
        "topic": topic,
        "keyword_data": {"keyword_map": keyword_output},
    })

    cluster_id = arch_result.get("cluster_id")
    update_pipeline_run(run_id, cluster_id=cluster_id, current_stage="layer2_faq_architect")

    # ─── 5. FAQ Architect (per article) ──────────────────────────
    db_articles = get_articles_by_cluster(cluster_id) if cluster_id else []
    print(f"\n[Pipeline] Running FAQ Architect on {len(db_articles)} articles")

    faqs_by_article = {}
    faq_results = []

    # We treat each article as a sub-call but accumulate metadata under faq_architect
    aggregate_meta = {
        "agent": "faq_architect",
        "status": "completed",
        "serp_calls": 0,
        "llm_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "validation_problems": [],
        "validation_passed": True,
    }

    for art in db_articles:
        try:
            faq_agent = FAQArchitectAgent(run_id, cluster_id=cluster_id, article_id=art["id"])
            # Run agent BUT don't double-save — we want per-article tracking aggregated
            faq_out = faq_agent.run({
                "article_id": art["id"],
                "keyword_data": {"keyword_map": keyword_output},
            })
            faq_results.append({"article_id": art["id"], "faqs": faq_out.get("faqs", [])})
            faqs_by_article[art["id"]] = faq_out.get("faqs", [])

            aggregate_meta["llm_calls"] += faq_agent.llm_calls
            aggregate_meta["tokens_in"] += faq_agent.tokens_in
            aggregate_meta["tokens_out"] += faq_agent.tokens_out
            aggregate_meta["cost_usd"] += faq_agent.cost_usd

        except Exception as e:
            print(f"  ⚠️  FAQ Architect failed for {art['id']}: {e}")
            faqs_by_article[art["id"]] = []
            aggregate_meta["validation_passed"] = False
            aggregate_meta["validation_problems"].append(f"{art['id']}: {e}")

    # Save aggregated FAQ output (overrides per-article saves with cluster-level view)
    save_artifact(run_id, "faq_architect", "input", {
        "articles": [{"id": a["id"], "title": a["title"]} for a in db_articles],
    })
    save_artifact(run_id, "faq_architect", "output", {
        "faqs_by_article": faqs_by_article,
        "total_articles": len(db_articles),
        "total_faqs": sum(len(v) for v in faqs_by_article.values()),
        "results": faq_results,
    })
    aggregate_meta["cost_usd"] = round(aggregate_meta["cost_usd"], 6)
    save_artifact(run_id, "faq_architect", "metadata", aggregate_meta)

    # ─── 6. Research Prompt Generator ────────────────────────────
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


# ─── Helpers ──────────────────────────────────────────────────────────
def load_agent_output(run_id, agent_name):
    return load_artifact(run_id, agent_name, "output")

def load_agent_input(run_id, agent_name):
    return load_artifact(run_id, agent_name, "input")

def load_agent_metadata(run_id, agent_name):
    return load_artifact(run_id, agent_name, "metadata")

def edit_agent_output(run_id, agent_name, new_output):
    save_artifact(run_id, agent_name, "output", new_output)


def get_full_run_state(run_id):
    """Build a dashboard-ready snapshot. Always returns dicts (never None) for metadata."""
    run = get_pipeline_run(run_id)
    if not run:
        return None
    state = {"run": run, "outputs": {}, "metadata": {}}
    for agent in ["trend_scout", "competitor_spy", "keyword_mapper",
                  "content_architect", "faq_architect", "research_prompt_generator"]:
        state["outputs"][agent] = load_artifact(run_id, agent, "output")
        # KEY FIX: always return a dict, never None — prevents the AttributeError
        state["metadata"][agent] = load_artifact(run_id, agent, "metadata") or {}
    return state