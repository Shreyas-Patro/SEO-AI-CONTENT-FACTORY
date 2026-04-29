"""
Pipeline Orchestrator v5.1 — Fixed state reload before set_stage
"""

from db.artifacts import (
    init_artifact_tables,
    create_pipeline_run, update_pipeline_run, get_pipeline_run,
    save_artifact, load_artifact, list_pipeline_runs,
    increment_run_counters,
)
from db.pipeline_state import PipelineState, StateKeys
from db.sqlite_ops import init_db, get_articles_by_cluster

init_db()
init_artifact_tables()


def start_pipeline_run(topic, notes=""):
    run_id = create_pipeline_run(topic, notes=notes)
    save_artifact(run_id, "_pipeline", "input", {"topic": topic, "notes": notes})
    state = PipelineState.load(run_id)
    state.topic = topic
    state.save()
    return run_id


def approve_gate(run_id):
    update_pipeline_run(run_id, gate_status="approved", current_stage="gate_approved")

def reject_gate(run_id):
    update_pipeline_run(run_id, gate_status="rejected", status="cancelled")


def run_layer1(run_id):
    from agents.trend_scout import run_trend_scout
    from agents.competitor_spy import CompetitorSpyAgent
    from agents.keyword_mapper import KeywordMapperAgent

    run = get_pipeline_run(run_id)
    if not run:
        raise ValueError(f"Pipeline run {run_id} not found")
    topic = run["topic"]

    # Seed topic into state
    state = PipelineState.load(run_id)
    state.topic = topic
    state.save()

    print(f"\n{'='*60}\n[Pipeline {run_id}] Layer 1 for '{topic}'\n{'='*60}")

    # 1. Trend Scout (legacy function)
    update_pipeline_run(run_id, current_stage="layer1_trend_scout")
    save_artifact(run_id, "trend_scout", "input", {"topic": topic})
    trend_data = run_trend_scout(topic, cluster_id=run.get("cluster_id"))
    save_artifact(run_id, "trend_scout", "output", trend_data)
    save_artifact(run_id, "trend_scout", "metadata", {
        "agent": "trend_scout", "status": "completed",
        "validation_passed": True, "validation_problems": [],
        "serp_calls": trend_data.get("serp_calls_used", 0),
        "llm_calls": 1, "cost_usd": trend_data.get("cost_usd", 0),
    })
    increment_run_counters(
        run_id, cost=trend_data.get("cost_usd", 0),
        serp_calls=trend_data.get("serp_calls_used", 0), llm_calls=1,
    )
    # Write trend_data to state (reload fresh to avoid stale writes)
    state = PipelineState.load(run_id)
    state.set(StateKeys.TREND_DATA, trend_data)

    # 2. Competitor Spy (AgentBase — writes to state automatically)
    update_pipeline_run(run_id, current_stage="layer1_competitor_spy")
    spy = CompetitorSpyAgent(run_id, cluster_id=run.get("cluster_id"))
    spy_output = spy.run({"topic": topic})
    increment_run_counters(run_id, cost=spy.cost_usd, serp_calls=spy.serp_calls)

    # 3. Keyword Mapper (AgentBase — writes to state automatically)
    update_pipeline_run(run_id, current_stage="layer1_keyword_mapper")
    mapper = KeywordMapperAgent(run_id, cluster_id=run.get("cluster_id"))
    keyword_output = mapper.run({"topic": topic})
    increment_run_counters(run_id, cost=mapper.cost_usd, llm_calls=mapper.llm_calls)

    # CRITICAL: reload state from disk BEFORE set_stage
    # (agents wrote competitor_data + keyword_map to disk during their runs)
    state = PipelineState.load(run_id)
    update_pipeline_run(run_id, current_stage="gate_pending", gate_status="pending")
    state.set_stage("gate_pending")
    print(f"\n[Pipeline {run_id}] Layer 1 complete. Awaiting human gate.")

    return {"trend_scout": trend_data, "competitor_spy": spy_output, "keyword_mapper": keyword_output}


def run_layer2(run_id):
    from agents.content_architect import ContentArchitectAgent
    from agents.faq_architect import FAQArchitectAgent
    from agents.research_prompt_generator import ResearchPromptGeneratorAgent

    run = get_pipeline_run(run_id)
    if not run:
        raise ValueError(f"Pipeline run {run_id} not found")
    if run.get("gate_status") != "approved":
        raise RuntimeError(f"Gate not approved. Status: {run.get('gate_status')}")

    topic = run["topic"]
    print(f"\n{'='*60}\n[Pipeline {run_id}] Layer 2 for '{topic}'\n{'='*60}")

    # 4. Content Architect
    update_pipeline_run(run_id, current_stage="layer2_content_architect")
    architect = ContentArchitectAgent(run_id, cluster_id=run.get("cluster_id"))
    arch_result = architect.run({"topic": topic})
    cluster_id = arch_result.get("cluster_id")
    update_pipeline_run(run_id, cluster_id=cluster_id)
    increment_run_counters(run_id, cost=architect.cost_usd, llm_calls=architect.llm_calls)

    # Store cluster_id in state
    state = PipelineState.load(run_id)
    state.set(StateKeys.CLUSTER_ID, cluster_id)

    # 5. FAQ Architect
    update_pipeline_run(run_id, current_stage="layer2_faq_architect")
    faq = FAQArchitectAgent(run_id, cluster_id=cluster_id)
    faq_result = faq.run({"topic": topic, "cluster_id": cluster_id})
    increment_run_counters(run_id, cost=faq.cost_usd, llm_calls=faq.llm_calls)

    # 6. Research Prompt Generator
    update_pipeline_run(run_id, current_stage="layer2_research_prompt_gen")
    rpg = ResearchPromptGeneratorAgent(run_id, cluster_id=cluster_id)
    rpg_result = rpg.run({"topic": topic})
    increment_run_counters(run_id, cost=rpg.cost_usd, llm_calls=rpg.llm_calls)

    # CRITICAL: reload before set_stage
    state = PipelineState.load(run_id)
    update_pipeline_run(run_id, current_stage="layer2_done")
    state.set_stage("layer2_done")
    print(f"\n[Pipeline {run_id}] Layer 2 complete.")

    return {"content_architect": arch_result, "faq_architect": faq_result, "research_prompt_generator": rpg_result}


def run_layer3(run_id, article_ids=None):
    from agents.lead_writer import LeadWriterAgent
    from agents.fact_verifier import FactVerifierAgent
    from agents.brand_auditor import BrandAuditorAgent
    from agents.meta_tagger import MetaTaggerAgent

    run = get_pipeline_run(run_id)
    if not run:
        raise ValueError(f"Pipeline run {run_id} not found")

    cluster_id = run.get("cluster_id")
    if not cluster_id:
        raise RuntimeError("No cluster_id — run Layer 2 first")

    topic = run["topic"]
    all_articles = get_articles_by_cluster(cluster_id)
    if article_ids:
        articles = [a for a in all_articles if a["id"] in article_ids]
    else:
        articles = all_articles

    print(f"\n{'='*60}\n[Pipeline {run_id}] Layer 3 — {len(articles)} articles\n{'='*60}")

    results = []
    for i, art in enumerate(articles):
        art_id = art["id"]
        print(f"\n--- [{i+1}/{len(articles)}] {art['title']} ---")
        update_pipeline_run(run_id, current_stage=f"layer3_writing_{i+1}")

        try:
            writer = LeadWriterAgent(run_id, cluster_id=cluster_id, article_id=art_id)
            w = writer.run({"article_id": art_id, "topic": topic})
            increment_run_counters(run_id, cost=writer.cost_usd, llm_calls=writer.llm_calls)

            verifier = FactVerifierAgent(run_id, cluster_id=cluster_id, article_id=art_id)
            v = verifier.run({"article_id": art_id})
            increment_run_counters(run_id, cost=verifier.cost_usd, llm_calls=verifier.llm_calls)

            auditor = BrandAuditorAgent(run_id, cluster_id=cluster_id, article_id=art_id)
            a = auditor.run({"article_id": art_id})
            increment_run_counters(run_id, cost=auditor.cost_usd, llm_calls=auditor.llm_calls)

            tagger = MetaTaggerAgent(run_id, cluster_id=cluster_id, article_id=art_id)
            t = tagger.run({"article_id": art_id})
            increment_run_counters(run_id, cost=tagger.cost_usd, llm_calls=tagger.llm_calls)

            results.append({"article_id": art_id, "title": art["title"], "status": "completed",
                           "word_count": w.get("word_count", 0)})
        except Exception as e:
            print(f"  ❌ Failed: {e}")
            results.append({"article_id": art_id, "title": art["title"], "status": "failed", "error": str(e)})

    save_artifact(run_id, "layer3_summary", "output", {
        "articles_processed": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    })

    update_pipeline_run(run_id, status="completed", current_stage="done")
    state = PipelineState.load(run_id)
    state.set_stage("done")

    return results


def rerun_agent(run_id, agent_name, extra_input=None):
    AGENT_MAP = {
        "competitor_spy": "agents.competitor_spy:CompetitorSpyAgent",
        "keyword_mapper": "agents.keyword_mapper:KeywordMapperAgent",
        "content_architect": "agents.content_architect:ContentArchitectAgent",
        "faq_architect": "agents.faq_architect:FAQArchitectAgent",
        "research_prompt_generator": "agents.research_prompt_generator:ResearchPromptGeneratorAgent",
    }
    run = get_pipeline_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    if agent_name == "trend_scout":
        from agents.trend_scout import run_trend_scout
        state = PipelineState.load(run_id)
        result = run_trend_scout(run["topic"], cluster_id=run.get("cluster_id"))
        save_artifact(run_id, "trend_scout", "output", result)
        state.set(StateKeys.TREND_DATA, result)
        return result

    if agent_name not in AGENT_MAP:
        raise ValueError(f"Unknown agent: {agent_name}")

    module_path, class_name = AGENT_MAP[agent_name].split(":")
    import importlib
    mod = importlib.import_module(module_path)
    AgentClass = getattr(mod, class_name)

    agent = AgentClass(run_id, cluster_id=run.get("cluster_id"))
    inp = {"topic": run["topic"]}
    if agent_name == "faq_architect":
        inp["cluster_id"] = run.get("cluster_id")
    if extra_input:
        inp.update(extra_input)
    return agent.run(inp)


# Accessors for dashboard
def load_agent_output(run_id, agent_name):
    return load_artifact(run_id, agent_name, "output")

def load_agent_input(run_id, agent_name):
    return load_artifact(run_id, agent_name, "input")

def load_agent_metadata(run_id, agent_name):
    return load_artifact(run_id, agent_name, "metadata")

def load_agent_console(run_id, agent_name):
    result = load_artifact(run_id, agent_name, "console")
    return result if result is not None else ""

def edit_agent_output(run_id, agent_name, new_output):
    save_artifact(run_id, agent_name, "output", new_output)

def edit_state_key(run_id, key, new_value):
    state = PipelineState.load(run_id)
    state.set(key, new_value)

def get_full_run_state(run_id):
    run = get_pipeline_run(run_id)
    if not run:
        return None
    state = PipelineState.load(run_id)
    snapshot = {
        "run": run,
        "state": {"topic": state.topic, "stage": state.stage,
                  "agents_completed": state.agents_completed,
                  "shared_keys": list(state.shared.keys())},
        "outputs": {}, "metadata": {}, "consoles": {},
    }
    for agent in ["trend_scout","competitor_spy","keyword_mapper",
                  "content_architect","faq_architect","research_prompt_generator","layer3_summary"]:
        snapshot["outputs"][agent] = load_artifact(run_id, agent, "output")
        snapshot["metadata"][agent] = load_artifact(run_id, agent, "metadata") or {}
        snapshot["consoles"][agent] = load_artifact(run_id, agent, "console") or ""
    return snapshot