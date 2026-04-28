"""
quick_fix.py — fixes two things that are blocking your pipeline:

1) Backfills NULL `current_stage` values in pipeline_runs to 'init'
   (so the dashboard's Run Layer 1 button appears on existing runs).

2) Verifies the orchestrator exports load_agent_console.

Run from project root:
    python quick_fix.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("data/canvas.db")

def main():
    if not DB_PATH.exists():
        print(f"❌ {DB_PATH} not found. Are you in the project root?")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── 1. Backfill NULL stages ────────────────────────────────────────────
    rows = cur.execute(
        "SELECT id, topic, status, current_stage FROM pipeline_runs"
    ).fetchall()

    null_stage_runs = [r for r in rows if r["current_stage"] is None]
    if null_stage_runs:
        print(f"\nFound {len(null_stage_runs)} run(s) with NULL stage:")
        for r in null_stage_runs:
            print(f"  • {r['id'][-12:]}  topic={r['topic']!r}  status={r['status']}")

        cur.execute(
            "UPDATE pipeline_runs SET current_stage = 'init' "
            "WHERE current_stage IS NULL AND status = 'running'"
        )
        conn.commit()
        print(f"✅ Backfilled stage='init' on {cur.rowcount} running run(s).")
    else:
        print("✅ No NULL stages found — schema is healthy.")

    # ── 2. Show what's currently in the DB so you can verify ───────────────
    print("\nCurrent runs in DB (most recent 5):")
    rows = cur.execute(
        "SELECT id, topic, status, current_stage, gate_status "
        "FROM pipeline_runs ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    for r in rows:
        print(
            f"  {r['id'][-12:]}  "
            f"topic={r['topic']!r:20s}  "
            f"status={r['status']:10s}  "
            f"stage={r['current_stage']!s:25s}  "
            f"gate={r['gate_status']}"
        )

    conn.close()

    # ── 3. Verify orchestrator import ──────────────────────────────────────
    print("\nVerifying orchestrator exports...")
    try:
        from orchestrator import (
            start_pipeline_run, run_layer1, run_layer2,
            approve_gate, reject_gate,
            load_agent_output, load_agent_input,
            load_agent_metadata, load_agent_console,
            edit_agent_output, edit_state_key,
            get_full_run_state,
        )
        print("✅ All orchestrator exports OK.")
    except ImportError as e:
        print(f"❌ Orchestrator import failed: {e}")
        print("   → Replace orchestrator.py with the v3 version.")

if __name__ == "__main__":
    main()