"""
Artifact store v3 — Filesystem primary, SQLite index.

LAYOUT ON DISK:
    runs/
        <run_id>/
            run.json                    # Pipeline run metadata
            state.json                  # Live PipelineState (shared memory)
            trend_scout/
                input.json
                output.json
                metadata.json
                console.log             # captured stdout/stderr for this agent
            competitor_spy/
                input.json
                output.json
                metadata.json
            ...etc

WHY: You asked "where are the outputs stored?" — now they're plain files you can
cd into and cat. SQLite holds a fast index for the dashboard.
"""

import os
import json
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

from config_loader import get_path
from db.sqlite_ops import db_conn, _now, _uuid

RUNS_ROOT = Path("runs")  # Configurable via config later if needed
RUNS_ROOT.mkdir(parents=True, exist_ok=True)


# ─── DDL ──────────────────────────────────────────────────────────────────

def init_artifact_tables():
    """Create the pipeline_runs index table. Safe to run repeatedly."""
    with db_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            notes TEXT DEFAULT '',
            cluster_id TEXT,
            status TEXT DEFAULT 'running',           -- running, completed, cancelled, failed
            current_stage TEXT DEFAULT 'init',
            gate_status TEXT DEFAULT 'pending',      -- pending, approved, rejected
            total_cost_usd REAL DEFAULT 0.0,
            total_serp_calls INTEGER DEFAULT 0,
            total_llm_calls INTEGER DEFAULT 0,
            total_tokens_in INTEGER DEFAULT 0,
            total_tokens_out INTEGER DEFAULT 0,
            artifact_path TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS artifact_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            kind TEXT NOT NULL,                       -- input, output, metadata, console, custom
            file_path TEXT NOT NULL,
            byte_size INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(run_id, agent_name, kind)
        );

        CREATE INDEX IF NOT EXISTS idx_pruns_status ON pipeline_runs(status);
        CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifact_index(run_id);
        """)
        conn.commit()


# ─── PIPELINE RUNS ────────────────────────────────────────────────────────

def create_pipeline_run(topic, notes=""):
    """Create a new pipeline run. Allocates the on-disk folder."""
    run_id = f"prun-{_uuid()}"
    artifact_path = RUNS_ROOT / run_id
    artifact_path.mkdir(parents=True, exist_ok=True)

    with db_conn() as conn:
        conn.execute("""
            INSERT INTO pipeline_runs (id, topic, notes, artifact_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (run_id, topic, notes, str(artifact_path), _now(), _now()))
        conn.commit()

    # Write the initial run.json
    run_meta = {
        "id": run_id,
        "topic": topic,
        "notes": notes,
        "created_at": _now(),
        "status": "running",
    }
    (artifact_path / "run.json").write_text(json.dumps(run_meta, indent=2))

    # Initialize empty state.json (the shared memory for the swarm)
    initial_state = {
        "run_id": run_id,
        "topic": topic,
        "stage": "init",
        "agents_completed": [],
        "agents_failed": [],
        "shared": {},  # agents read/write here
    }
    (artifact_path / "state.json").write_text(json.dumps(initial_state, indent=2))

    return run_id


def get_pipeline_run(run_id):
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return dict(row) if row else None


def list_pipeline_runs(limit=50, status=None):
    query = "SELECT * FROM pipeline_runs"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_pipeline_run(run_id, **kwargs):
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [run_id]
    with db_conn() as conn:
        conn.execute(f"UPDATE pipeline_runs SET {sets} WHERE id = ?", vals)
        conn.commit()


def increment_run_counters(run_id, cost=0.0, serp_calls=0, llm_calls=0,
                           tokens_in=0, tokens_out=0):
    with db_conn() as conn:
        conn.execute("""
            UPDATE pipeline_runs SET
                total_cost_usd    = COALESCE(total_cost_usd, 0)    + ?,
                total_serp_calls  = COALESCE(total_serp_calls, 0)  + ?,
                total_llm_calls   = COALESCE(total_llm_calls, 0)   + ?,
                total_tokens_in   = COALESCE(total_tokens_in, 0)   + ?,
                total_tokens_out  = COALESCE(total_tokens_out, 0)  + ?,
                updated_at = ?
            WHERE id = ?
        """, (cost, serp_calls, llm_calls, tokens_in, tokens_out, _now(), run_id))
        conn.commit()


# ─── ARTIFACTS (the new filesystem-primary save/load) ─────────────────────

def _agent_dir(run_id, agent_name) -> Path:
    """Ensure the per-agent folder exists and return it."""
    run = get_pipeline_run(run_id)
    if not run or not run.get("artifact_path"):
        raise ValueError(f"Pipeline run {run_id} not found or has no artifact_path")
    p = Path(run["artifact_path"]) / agent_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_artifact(run_id, agent_name, kind, data):
    """
    Save an artifact to disk AND index it in SQLite.
    kind: 'input' | 'output' | 'metadata' | 'console' | <custom>
    """
    folder = _agent_dir(run_id, agent_name)

    if kind == "console":
        # Console logs are plain text, not JSON
        ext = "log"
        file_path = folder / f"{kind}.{ext}"
        if isinstance(data, list):
            data = "\n".join(str(line) for line in data)
        file_path.write_text(str(data), encoding="utf-8")
    else:
        ext = "json"
        file_path = folder / f"{kind}.{ext}"
        file_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    byte_size = file_path.stat().st_size

    with db_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO artifact_index
                (run_id, agent_name, kind, file_path, byte_size, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (run_id, agent_name, kind, str(file_path), byte_size, _now()))
        conn.commit()

    return str(file_path)


def load_artifact(run_id, agent_name, kind):
    """
    Load an artifact. Returns dict for json kinds, str for console, None if missing.
    Reads from disk directly — single source of truth.
    """
    run = get_pipeline_run(run_id)
    if not run or not run.get("artifact_path"):
        return None

    folder = Path(run["artifact_path"]) / agent_name
    if kind == "console":
        f = folder / "console.log"
        if not f.exists():
            return None
        return f.read_text(encoding="utf-8")

    f = folder / f"{kind}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️  Failed to parse artifact {f}: {e}")
        return None


def list_artifacts(run_id):
    """List all artifacts for a run, grouped by agent."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM artifact_index WHERE run_id = ? ORDER BY agent_name, kind",
            (run_id,)
        ).fetchall()

    by_agent = {}
    for r in rows:
        d = dict(r)
        by_agent.setdefault(d["agent_name"], []).append(d)
    return by_agent


def get_artifact_path(run_id, agent_name, kind):
    """Return the absolute file path for direct access (e.g. download buttons)."""
    folder = _agent_dir(run_id, agent_name)
    if kind == "console":
        return str(folder / "console.log")
    return str(folder / f"{kind}.json")


# ─── PIPELINE STATE (shared memory for the swarm) ─────────────────────────

def load_state(run_id) -> dict:
    """Load the live PipelineState. Returns empty skeleton if missing."""
    run = get_pipeline_run(run_id)
    if not run:
        return {}
    state_file = Path(run["artifact_path"]) / "state.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {"run_id": run_id, "shared": {}, "agents_completed": [], "agents_failed": []}


def save_state(run_id, state: dict):
    run = get_pipeline_run(run_id)
    if not run:
        return
    state_file = Path(run["artifact_path"]) / "state.json"
    state_file.write_text(json.dumps(state, indent=2, default=str))


def update_state(run_id, **patch):
    """Merge `patch` into the run's state and persist."""
    state = load_state(run_id)
    for k, v in patch.items():
        state[k] = v
    save_state(run_id, state)


def update_shared(run_id, **patch):
    """Update the `shared` dict inside state — for inter-agent data."""
    state = load_state(run_id)
    state.setdefault("shared", {})
    for k, v in patch.items():
        state["shared"][k] = v
    save_state(run_id, state)


# ─── HOUSEKEEPING ─────────────────────────────────────────────────────────

def delete_pipeline_run(run_id, delete_files=True):
    """Hard-delete a run and (optionally) its files. Use for cleanup."""
    run = get_pipeline_run(run_id)
    if not run:
        return False

    if delete_files and run.get("artifact_path"):
        p = Path(run["artifact_path"])
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    with db_conn() as conn:
        conn.execute("DELETE FROM artifact_index WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM pipeline_runs WHERE id = ?", (run_id,))
        conn.commit()
    return True


def get_run_size_mb(run_id):
    """Sum of all artifact bytes for a run."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(byte_size), 0) FROM artifact_index WHERE run_id = ?",
            (run_id,)
        ).fetchone()
    return round((row[0] or 0) / 1024 / 1024, 3)


if __name__ == "__main__":
    init_artifact_tables()
    print(f"Artifact tables initialized. Runs root: {RUNS_ROOT.absolute()}")