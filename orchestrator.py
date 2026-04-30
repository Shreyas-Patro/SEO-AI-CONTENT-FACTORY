"""
Pipeline Orchestrator v6 — LangGraph-backed.

PUBLIC API (unchanged so the dashboard keeps working):
- start_pipeline_run(topic, notes)
- run_layer1(run_id) → executes the layer1 graph
- approve_gate(run_id), reject_gate(run_id)
- run_layer2(run_id), run_layer3(run_id, article_ids=None)
- rerun_agent(run_id, agent_name) — single-node rerun
- load_agent_output / load_agent_input / load_agent_metadata / load_agent_console
- edit_agent_output, edit_state_key, get_full_run_state
"""
from db.artifacts import (
    init_artifact_tables, create_pipeline_run, update_pipeline_run, get_pipeline_run,
    save_artifact, load_artifact, list_pipeline_runs, increment_run_counters,
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


def _build_initial_state(run_id):
    run = get_pipeline_run(run_id)
    return {
        "run_id": run_id,
        "topic": run["topic"],
        "cluster_id": run.get("cluster_id"),
        "current_stage": run.get("current_stage", "init"),
        "gate_status": run.get("gate_status", "pending"),
        "layer1_done": False,
        "layer2_done": False,
        "articles_queue": [],
        "current_article_id": None,
        "completed_articles": [],
        "failed_articles": [],
        "article_iteration": {},
        "article_scores": {},
        "last_fact_verifier_output": {},
        "last_brand_auditor_output": {},
        "total_cost_usd": run.get("total_cost_usd", 0) or 0,
        "total_llm_calls": run.get("total_llm_calls", 0) or 0,
        "total_serp_calls": run.get("total_serp_calls", 0) or 0,
        "errors": [],
    }


def run_layer1(run_id):
    from graph.pipeline import build_layer1_graph
    graph = build_layer1_graph()
    init_state = _build_initial_state(run_id)
    config = {"configurable": {"thread_id": f"{run_id}-l1"}}
    print(f"\n{'='*60}\n[Pipeline {run_id}] Layer 1 (LangGraph)\n{'='*60}")

    final_state = None
    for event in graph.stream(init_state, config=config):
        for node_name, node_output in event.items():
            print(f"  ✓ Node done: {node_name}")
            final_state = node_output

    update_pipeline_run(run_id, current_stage="gate_pending", gate_status="pending")
    return final_state


def run_layer2(run_id):
    from graph.pipeline import build_layer2_graph
    run = get_pipeline_run(run_id)
    if run.get("gate_status") != "approved":
        raise RuntimeError(f"Gate not approved. Status: {run.get('gate_status')}")

    graph = build_layer2_graph()
    init_state = _build_initial_state(run_id)
    config = {"configurable": {"thread_id": f"{run_id}-l2"}}
    print(f"\n{'='*60}\n[Pipeline {run_id}] Layer 2 (LangGraph)\n{'='*60}")

    final_state = None
    for event in graph.stream(init_state, config=config):
        for node_name, node_output in event.items():
            print(f"  ✓ Node done: {node_name}")
            final_state = node_output
    return final_state


def run_layer3(run_id, article_ids=None):
    from graph.pipeline import build_layer3_graph
    run = get_pipeline_run(run_id)
    if not run.get("cluster_id"):
        raise RuntimeError("No cluster_id — run Layer 2 first")

    graph = build_layer3_graph()
    init_state = _build_initial_state(run_id)
    if article_ids:
        init_state["articles_queue"] = article_ids
        init_state["current_article_id"] = article_ids[0]
    config = {
        "configurable": {"thread_id": f"{run_id}-l3"},
        "recursion_limit": 200,   # quality loop can iterate
    }

    print(f"\n{'='*60}\n[Pipeline {run_id}] Layer 3 (LangGraph + quality loop)\n{'='*60}")
    final_state = None
    for event in graph.stream(init_state, config=config):
        for node_name, node_output in event.items():
            print(f"  ✓ Node done: {node_name}")
            final_state = node_output

    update_pipeline_run(run_id, status="completed", current_stage="done")
    return final_state


# ── Single-agent reruns (kept for dashboard) ────────────────────────────
def rerun_agent(run_id, agent_name, extra_input=None):
    AGENT_MAP = {
        "competitor_spy": "agents.competitor_spy:CompetitorSpyAgent",
        "keyword_mapper": "agents.keyword_mapper:KeywordMapperAgent",
        "content_architect": "agents.content_architect:ContentArchitectAgent",
        "faq_architect": "agents.faq_architect:FAQArchitectAgent",
        "research_prompt_generator": "agents.research_prompt_generator:ResearchPromptGeneratorAgent",
        "lead_writer": "agents.lead_writer:LeadWriterAgent",
        "fact_verifier": "agents.fact_verifier:FactVerifierAgent",
        "brand_auditor": "agents.brand_auditor:BrandAuditorAgent",
        "rewriter": "agents.rewriter:RewriterAgent",
        "meta_tagger": "agents.meta_tagger:MetaTaggerAgent",
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

    agent = AgentClass(run_id, cluster_id=run.get("cluster_id"),
                       article_id=(extra_input or {}).get("article_id"))
    inp = {"topic": run["topic"]}
    if agent_name == "faq_architect":
        inp["cluster_id"] = run.get("cluster_id")
    if extra_input:
        inp.update(extra_input)
    return agent.run(inp)


# ── Accessors used by dashboard ─────────────────────────────────────────
def load_agent_output(run_id, agent_name): return load_artifact(run_id, agent_name, "output")
def load_agent_input(run_id, agent_name):  return load_artifact(run_id, agent_name, "input")
def load_agent_metadata(run_id, agent_name): return load_artifact(run_id, agent_name, "metadata")
def load_agent_console(run_id, agent_name):
    r = load_artifact(run_id, agent_name, "console")
    return r if r is not None else ""

def edit_agent_output(run_id, agent_name, new_output):
    save_artifact(run_id, agent_name, "output", new_output)

def edit_state_key(run_id, key, new_value):
    state = PipelineState.load(run_id)
    state.set(key, new_value)

def get_full_run_state(run_id):
    run = get_pipeline_run(run_id)
    if not run: return None
    state = PipelineState.load(run_id)
    snap = {
        "run": run,
        "state": {"topic": state.topic, "stage": state.stage,
                  "agents_completed": state.agents_completed,
                  "shared_keys": list(state.shared.keys())},
        "outputs": {}, "metadata": {}, "consoles": {},
    }
    for agent in [
        "trend_scout","competitor_spy","keyword_mapper",
        "content_architect","faq_architect","research_prompt_generator",
        "lead_writer","fact_verifier","brand_auditor","rewriter","meta_tagger",
    ]:
        snap["outputs"][agent] = load_artifact(run_id, agent, "output")
        snap["metadata"][agent] = load_artifact(run_id, agent, "metadata") or {}
        snap["consoles"][agent] = load_artifact(run_id, agent, "console") or ""
    return snap