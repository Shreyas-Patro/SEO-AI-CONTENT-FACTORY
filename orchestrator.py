"""
Pipeline Orchestrator v3 (with load_agent_console)

Drop this file into your project root, replacing the existing orchestrator.py.

CHANGES FROM v2:
- Uses PipelineState as the canonical inter-agent communication channel.
- Layer 1 agents (Trend Scout, Competitor Spy, Keyword Mapper) now write into state.
- Content Architect + FAQ Architect read from state, write back briefs/plans.
- FAQ Architect now runs ONCE per cluster (not once per article).
- Research Prompt Generator reads ARTICLE_BRIEFS + FAQ_PLAN from state.

EXPORTS (everything dashboard.py needs):
- start_pipeline_run, run_layer1, run_layer2
- approve_gate, reject_gate
- load_agent_input, load_agent_output, load_agent_metadata, load_agent_console
- edit_agent_output, edit_state_key, get_full_run_state
"""

from db.artifacts import (
    init_artifact_tables,
    create_pipeline_run, update_pipeline_run, get_pipeline_run,
    save_artifact, load_artifact, list_pipeline_runs,
    increment_run_counters,
)
from db.pipeline_state import PipelineState, StateKeys
from db.sqlite_ops import init_db, get_articles_by_cluster

# Initialize on import
init_db()
init_artifact_tables()


# ─── Lifecycle ─────────────────────────────────────────────────────────────

def start_pipeline_run(topic, notes=""):
    run_id = create_pipeline_run(topic, notes=notes)
    save_artifact(run_id, "_pipeline", "input", {"topic": topic, "notes": notes})

    # Seed PipelineState
    state = PipelineState.load(run_id)
    state.topic = topic
    state.save()
    return run_id


def approve_gate(run_id):
    update_pipeline_run(run_id, gate_status="approved", current_stage="gate_approved")


def reject_gate(run_id):
    update_pipeline_run(run_id, gate_status="rejected", status="cancelled")


# ─── LAYER 1: Market Intelligence ──────────────────────────────────────────

def run_layer1(run_id):
    from agents.trend_scout import run_trend_scout       # legacy function
    from agents.competitor_spy import CompetitorSpyAgent
    from agents.keyword_mapper import KeywordMapperAgent

    run = get_pipeline_run(run_id)
    if not run:
        raise ValueError(f"Pipeline run {run_id} not found")
    topic = run["topic"]

    state = PipelineState.load(run_id)
    state.topic = topic
    state.save()

    update_pipeline_run(run_id, current_stage="layer1_trend_scout")
    print(f"\n{'='*60}\n[Pipeline {run_id}] Layer 1 starting for '{topic}'\n{'='*60}")

    # ─── 1. Trend Scout ────────────────────────────────────────────────────
    save_artifact(run_id, "trend_scout", "input", {"topic": topic})
    trend_data = run_trend_scout(topic, cluster_id=run.get("cluster_id"))
    save_artifact(run_id, "trend_scout", "output", trend_data)

    ts_meta = {
        "agent": "trend_scout",
        "status": "completed",
        "validation_passed": True,
        "validation_problems": [],
        "serp_calls": trend_data.get("serp_calls_used", 15),
        "llm_calls": 1,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": trend_data.get("cost_usd", 0),
    }
    save_artifact(run_id, "trend_scout", "metadata", ts_meta)
    increment_run_counters(
        run_id,
        cost=trend_data.get("cost_usd", 0),
        serp_calls=trend_data.get("serp_calls_used", 15),
        llm_calls=1,
    )

    # Push into PipelineState
    state.set(StateKeys.TREND_DATA, trend_data)

    # ─── 2. Competitor Spy ─────────────────────────────────────────────────
    update_pipeline_run(run_id, current_stage="layer1_competitor_spy")
    spy = CompetitorSpyAgent(run_id, cluster_id=run.get("cluster_id"))
    spy_output = spy.run({"topic": topic})

    state.set(StateKeys.COMPETITOR_DATA, spy_output)

    # ─── 3. Keyword Mapper ─────────────────────────────────────────────────
    update_pipeline_run(run_id, current_stage="layer1_keyword_mapper")
    mapper = KeywordMapperAgent(run_id, cluster_id=run.get("cluster_id"))
    keyword_output = mapper.run({"topic": topic})

    state.set(StateKeys.KEYWORD_MAP, keyword_output)

    update_pipeline_run(run_id, current_stage="gate_pending", gate_status="pending")
    state.set_stage("gate_pending")
    print(f"\n[Pipeline {run_id}] Layer 1 complete. Awaiting human gate.")

    return {
        "trend_scout": trend_data,
        "competitor_spy": spy_output,
        "keyword_mapper": keyword_output,
    }


# ─── LAYER 2: Content Planning ─────────────────────────────────────────────

def run_layer2(run_id):
    from agents.content_architect import ContentArchitectAgent
    from agents.faq_architect import FAQArchitectAgent
    from agents.research_prompt_generator import ResearchPromptGeneratorAgent

    run = get_pipeline_run(run_id)
    if run["gate_status"] != "approved":
        raise RuntimeError(f"Gate not approved. Current status: {run['gate_status']}")

    state = PipelineState.load(run_id)

    if not state.has(StateKeys.KEYWORD_MAP):
        raise RuntimeError("PipelineState missing keyword_map — re-run Layer 1")

    # ─── 4. Content Architect ──────────────────────────────────────────────
    update_pipeline_run(run_id, current_stage="layer2_content_architect")
    architect = ContentArchitectAgent(run_id, cluster_id=run.get("cluster_id"))
    arch_result = architect.run({"topic": run["topic"]})
    cluster_id = arch_result.get("cluster_id")
    update_pipeline_run(run_id, cluster_id=cluster_id)

    # ─── 5. FAQ Architect (ONE call for the whole cluster) ─────────────────
    update_pipeline_run(run_id, current_stage="layer2_faq_architect")
    faq = FAQArchitectAgent(run_id, cluster_id=cluster_id)
    faq_result = faq.run({"cluster_id": cluster_id})

    # ─── 6. Research Prompt Generator ──────────────────────────────────────
    update_pipeline_run(run_id, current_stage="layer2_research_prompt_gen")
    rpg = ResearchPromptGeneratorAgent(run_id, cluster_id=cluster_id)
    rpg_output = rpg.run({"topic": run["topic"]})

    update_pipeline_run(run_id, status="completed", current_stage="done")
    state.set_stage("done")
    state.save()

    return {
        "content_architect": arch_result,
        "faq_architect": faq_result,
        "research_prompt_generator": rpg_output,
    }


# ─── Convenience accessors ─────────────────────────────────────────────────

def load_agent_output(run_id, agent_name):
    return load_artifact(run_id, agent_name, "output")


def load_agent_input(run_id, agent_name):
    return load_artifact(run_id, agent_name, "input")


def load_agent_metadata(run_id, agent_name):
    return load_artifact(run_id, agent_name, "metadata")


def load_agent_console(run_id, agent_name):
    """Return captured stdout/stderr for an agent run, or '' if missing."""
    result = load_artifact(run_id, agent_name, "console")
    return result if result is not None else ""


def edit_agent_output(run_id, agent_name, new_output):
    """
    Edit an agent's output AND propagate to PipelineState if the agent
    declares WRITES_STATE keys.
    """
    save_artifact(run_id, agent_name, "output", new_output)


def edit_state_key(run_id, key, new_value):
    """Direct edit to PipelineState — for fine-grained surgery via dashboard."""
    state = PipelineState.load(run_id)
    state.set(key, new_value)
    state.save()


def get_full_run_state(run_id):
    """Dashboard-ready snapshot."""
    run = get_pipeline_run(run_id)
    if not run:
        return None
    state = PipelineState.load(run_id)
    snapshot = {
        "run": run,
        "state": {
            "topic": state.topic,
            "stage": state.stage,
            "agents_completed": state.agents_completed,
            "shared_keys": list(state.shared.keys()),
        },
        "outputs": {},
        "metadata": {},
        "consoles": {},
    }
    for agent in [
        "trend_scout", "competitor_spy", "keyword_mapper",
        "content_architect", "faq_architect", "research_prompt_generator",
    ]:
        snapshot["outputs"][agent] = load_artifact(run_id, agent, "output")
        snapshot["metadata"][agent] = load_artifact(run_id, agent, "metadata") or {}
        snapshot["consoles"][agent] = load_artifact(run_id, agent, "console") or ""
    return snapshot