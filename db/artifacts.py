"""
Artifact Store — the single source of truth for agent inputs and outputs.

Every agent run produces three files on disk:
    artifacts/<pipeline_run_id>/<agent_name>/input.json
    artifacts/<pipeline_run_id>/<agent_name>/output.json
    artifacts/<pipeline_run_id>/<agent_name>/metadata.json

And one row in the `artifacts` SQL table that points at them.

Why this exists:
- Streamlit session state is RAM-only. Page reload = data gone.
- LangGraph wants a checkpointable state; this IS that checkpoint.
- You can replay, edit, or audit any run without re-running expensive APIs.
- Later agents read artifacts from previous agents — no in-memory passing.
"""

import os
import json
import uuid
from datetime import datetime
from config_loader import cfg
from db.sqlite_ops import get_conn, _now, _uuid

ARTIFACTS_ROOT = cfg.get("paths", {}).get("artifacts_dir", "data/artifacts")


# ─── INIT ─────────────────────────────────────────────────────────────────

def init_artifact_tables():
    """Create the pipeline_runs and artifacts tables. Safe to re-run."""
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        status TEXT DEFAULT 'running',
        current_stage TEXT,
        cluster_id TEXT,
        gate_status TEXT DEFAULT 'pending',
        total_cost_usd REAL DEFAULT 0.0,
        total_serp_calls INTEGER DEFAULT 0,
        total_llm_calls INTEGER DEFAULT 0,
        total_tokens_in INTEGER DEFAULT 0,
        total_tokens_out INTEGER DEFAULT 0,
        started_at TEXT,
        completed_at TEXT,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        pipeline_run_id TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        artifact_kind TEXT NOT NULL,         -- 'input' | 'output' | 'metadata'
        file_path TEXT NOT NULL,
        size_bytes INTEGER DEFAULT 0,
        created_at TEXT,
        FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(id)
    );

    CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(pipeline_run_id);
    CREATE INDEX IF NOT EXISTS idx_artifacts_agent ON artifacts(agent_name);
    CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);
    """)
    conn.commit()
    conn.close()
    os.makedirs(ARTIFACTS_ROOT, exist_ok=True)


# ─── PIPELINE RUNS ────────────────────────────────────────────────────────

def create_pipeline_run(topic, notes=""):
    """Start a new pipeline run. Returns run_id."""
    run_id = f"prun-{_uuid()}"
    conn = get_conn()
    conn.execute("""
        INSERT INTO pipeline_runs (id, topic, status, current_stage, started_at, notes)
        VALUES (?, ?, 'running', 'init', ?, ?)
    """, (run_id, topic, _now(), notes))
    conn.commit()
    conn.close()

    # Create artifact directory
    os.makedirs(os.path.join(ARTIFACTS_ROOT, run_id), exist_ok=True)
    return run_id


def update_pipeline_run(run_id, **fields):
    if not fields:
        return
    conn = get_conn()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [run_id]
    conn.execute(f"UPDATE pipeline_runs SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def get_pipeline_run(run_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_pipeline_runs(limit=50, status=None):
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM pipeline_runs WHERE status = ? ORDER BY started_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def increment_run_counters(run_id, cost=0.0, serp_calls=0, llm_calls=0, tokens_in=0, tokens_out=0):
    """Atomically bump the per-run counters."""
    conn = get_conn()
    conn.execute("""
        UPDATE pipeline_runs
        SET total_cost_usd = total_cost_usd + ?,
            total_serp_calls = total_serp_calls + ?,
            total_llm_calls = total_llm_calls + ?,
            total_tokens_in = total_tokens_in + ?,
            total_tokens_out = total_tokens_out + ?
        WHERE id = ?
    """, (cost, serp_calls, llm_calls, tokens_in, tokens_out, run_id))
    conn.commit()
    conn.close()


# ─── ARTIFACTS (input/output/metadata) ────────────────────────────────────

def _artifact_path(run_id, agent_name, kind):
    """Get filesystem path for an artifact."""
    folder = os.path.join(ARTIFACTS_ROOT, run_id, agent_name)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{kind}.json")


def save_artifact(run_id, agent_name, kind, data):
    """
    Save an agent input/output/metadata to disk + register in DB.
    kind = 'input' | 'output' | 'metadata'
    """
    if kind not in ("input", "output", "metadata"):
        raise ValueError(f"Invalid artifact kind: {kind}")

    path = _artifact_path(run_id, agent_name, kind)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)

    size = os.path.getsize(path)
    artifact_id = f"art-{_uuid()}"

    conn = get_conn()
    # Upsert pattern — replace any existing same-kind artifact for this agent+run
    conn.execute("""
        DELETE FROM artifacts
        WHERE pipeline_run_id = ? AND agent_name = ? AND artifact_kind = ?
    """, (run_id, agent_name, kind))
    conn.execute("""
        INSERT INTO artifacts (id, pipeline_run_id, agent_name, artifact_kind,
                              file_path, size_bytes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (artifact_id, run_id, agent_name, kind, path, size, _now()))
    conn.commit()
    conn.close()
    return path


def load_artifact(run_id, agent_name, kind):
    """Load an artifact from disk. Returns dict, or None if not found."""
    path = _artifact_path(run_id, agent_name, kind)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_artifacts_for_run(run_id):
    """List all artifacts for a pipeline run (for the dashboard)."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM artifacts WHERE pipeline_run_id = ?
        ORDER BY created_at ASC
    """, (run_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_agent_outputs(run_id):
    """Return {agent_name: output_dict} for all completed agents in a run."""
    out = {}
    artifacts = list_artifacts_for_run(run_id)
    for a in artifacts:
        if a["artifact_kind"] == "output":
            out[a["agent_name"]] = load_artifact(run_id, a["agent_name"], "output")
    return out


def edit_artifact(run_id, agent_name, kind, new_data):
    """Overwrite an artifact (used for human-in-the-loop edits)."""
    return save_artifact(run_id, agent_name, kind, new_data)


# ─── RUN-SCOPED HELPERS ───────────────────────────────────────────────────

def get_run_summary(run_id):
    """Build a complete summary of a pipeline run for the UI."""
    run = get_pipeline_run(run_id)
    if not run:
        return None
    artifacts = list_artifacts_for_run(run_id)

    # Group by agent
    by_agent = {}
    for a in artifacts:
        agent = a["agent_name"]
        if agent not in by_agent:
            by_agent[agent] = {"input": None, "output": None, "metadata": None}
        by_agent[agent][a["artifact_kind"]] = a

    return {
        "run": run,
        "agents": by_agent,
    }


if __name__ == "__main__":
    init_artifact_tables()
    print(f"Artifact store initialized.")
    print(f"  - Tables: pipeline_runs, artifacts")
    print(f"  - Filesystem root: {ARTIFACTS_ROOT}")