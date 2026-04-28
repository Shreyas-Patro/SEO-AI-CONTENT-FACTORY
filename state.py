"""
PipelineState — the typed object that flows between agents.

Why this exists:
- Replaces ad-hoc dicts being passed around
- Every agent reads what it needs from state, writes its output back
- Pydantic validates at every step — catches contract violations early
- Serializable to/from JSON for the artifact store
- When you migrate to LangGraph later, this becomes the LangGraph state type
  unchanged. No rewrite needed.

Usage:
    state = PipelineState(run_id="run-abc", topic="HSR Layout")
    state.set_agent_output("trend_scout", {...})
    out = state.get_agent_output("trend_scout")
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class AgentMetadata(BaseModel):
    """Per-agent execution metadata. One per agent per run."""
    agent: str
    status: str = "pending"      # pending | running | completed | failed
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    serp_calls: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    validation_passed: bool = True
    validation_problems: List[str] = Field(default_factory=list)
    retry_count: int = 0
    edited_by_user: bool = False
    error: Optional[str] = None


class PipelineState(BaseModel):
    """
    The single object that flows between agents in a pipeline run.

    It does NOT hold the raw outputs (those live on disk in artifacts/).
    It holds:
      - run-level identifiers
      - per-agent metadata (status, cost, etc.)
      - lightweight signals other agents need (e.g. cluster_id after
        content_architect creates one)

    Heavy outputs (cluster plans, FAQ slots, research prompts) are loaded
    on demand from the artifact store using state.run_id + agent name.
    """
    run_id: str
    topic: str
    status: str = "running"          # running | gate_pending | completed | cancelled | failed
    current_stage: str = "init"
    gate_status: str = "pending"     # pending | approved | rejected
    cluster_id: Optional[str] = None
    notes: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    # Per-agent metadata, keyed by agent name
    agent_metadata: Dict[str, AgentMetadata] = Field(default_factory=dict)

    # Aggregate counters
    total_cost_usd: float = 0.0
    total_serp_calls: int = 0
    total_llm_calls: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0

    def update_metadata(self, agent: str, **fields) -> None:
        """Update an agent's metadata, creating the entry if absent."""
        if agent not in self.agent_metadata:
            self.agent_metadata[agent] = AgentMetadata(agent=agent)
        meta = self.agent_metadata[agent]
        for k, v in fields.items():
            if hasattr(meta, k):
                setattr(meta, k, v)
        self.updated_at = datetime.utcnow().isoformat()

    def increment_totals(self, *, cost: float = 0.0, serp: int = 0, llm: int = 0,
                         tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.total_cost_usd = round(self.total_cost_usd + cost, 6)
        self.total_serp_calls += serp
        self.total_llm_calls += llm
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.updated_at = datetime.utcnow().isoformat()

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "PipelineState":
        return cls.model_validate_json(raw)