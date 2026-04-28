"""
PipelineState — the shared memory contract for all agents.

DESIGN PRINCIPLES:
1. Every agent reads what it needs from PipelineState.
2. Every agent writes its output back into PipelineState.shared.
3. The orchestrator persists a snapshot of state to disk after each agent.
4. New agents added later just need to declare what keys they read/write.

This is what LangGraph would force you to build — but as plain Python you can
debug with `print()` and inspect with `cat`.

USAGE IN AN AGENT:
    state = PipelineState.load(run_id)
    keyword_data = state.get("keyword_map")  # read upstream output
    state.set("my_agent_output", {...})       # write my output
    state.save()                              # persist
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from db.artifacts import load_state, save_state, update_shared


@dataclass
class PipelineState:
    """
    Wrapper around the JSON-persisted run state. Agents pass this around.
    """
    run_id: str
    topic: str = ""
    stage: str = "init"
    cluster_id: Optional[str] = None
    agents_completed: list = field(default_factory=list)
    agents_failed: list = field(default_factory=list)
    shared: dict = field(default_factory=dict)

    # ─── Persistence ──────────────────────────────────────────────────────
    @classmethod
    def load(cls, run_id: str) -> "PipelineState":
        raw = load_state(run_id)
        return cls(
            run_id=raw.get("run_id", run_id),
            topic=raw.get("topic", ""),
            stage=raw.get("stage", "init"),
            cluster_id=raw.get("cluster_id"),
            agents_completed=raw.get("agents_completed", []),
            agents_failed=raw.get("agents_failed", []),
            shared=raw.get("shared", {}),
        )

    def save(self):
        save_state(self.run_id, asdict(self))

    # ─── Shared-data accessors ────────────────────────────────────────────
    def set(self, key: str, value: Any):
        """Write into shared memory."""
        self.shared[key] = value
        # Persist immediately so other processes (e.g. dashboard) see it
        update_shared(self.run_id, **{key: value})

    def get(self, key: str, default: Any = None) -> Any:
        return self.shared.get(key, default)

    def has(self, key: str) -> bool:
        return key in self.shared

    def require(self, key: str):
        """Raise if a required upstream key is missing — fast-fail for agents."""
        if key not in self.shared:
            raise KeyError(
                f"PipelineState missing required key '{key}'. "
                f"Available keys: {list(self.shared.keys())}"
            )
        return self.shared[key]

    # ─── Lifecycle ────────────────────────────────────────────────────────
    def mark_agent_complete(self, agent_name: str):
        if agent_name not in self.agents_completed:
            self.agents_completed.append(agent_name)
        if agent_name in self.agents_failed:
            self.agents_failed.remove(agent_name)
        self.save()

    def mark_agent_failed(self, agent_name: str, error: str = ""):
        if agent_name not in self.agents_failed:
            self.agents_failed.append({"agent": agent_name, "error": error})
        self.save()

    def set_stage(self, stage: str):
        self.stage = stage
        self.save()


# ─── KEYS REGISTRY ────────────────────────────────────────────────────────
# This is documentation as code. Every key any agent uses must be listed here
# so you (and future you) can see what the data flow looks like.

class StateKeys:
    # Layer 1
    TREND_DATA = "trend_data"                # Trend Scout output
    COMPETITOR_DATA = "competitor_data"      # Competitor Spy output
    KEYWORD_MAP = "keyword_map"              # Keyword Mapper output

    # Layer 2
    CLUSTER_PLAN = "cluster_plan"            # Content Architect output
    CLUSTER_ID = "cluster_id"
    ARTICLE_BRIEFS = "article_briefs"        # one per article (writer-ready)
    FAQ_PLAN = "faq_plan"                    # deduplicated FAQ allocation
    RESEARCH_PROMPT = "research_prompt"      # for Perplexity

    # Layer 3 (future)
    RESEARCH_DOC = "research_doc"            # ingested research output
    INGESTED_FACT_IDS = "ingested_fact_ids"
    DRAFTS = "drafts"                        # writer agent outputs

    # Cross-cutting
    OPPORTUNITY_MATRIX = "opportunity_matrix"  # for the 2-axis viz