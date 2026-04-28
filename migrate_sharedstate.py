"""
Migration script v3.3 — Phase 2: copy files from data/artifacts/ to runs/.

Your previous diagnostic showed the v2 `artifacts` table was an INDEX of files
already on disk under data/artifacts/<run_id>/<agent>/<kind>.json.

This script:
1. Reads the v2 artifacts table (or v2 backup if step 4 already ran).
2. Copies each indexed file to runs/<run_id>/<agent>/<kind>.json.
3. Indexes them in the new artifact_index table.
4. Re-reconstructs state.json now that files are actually present.
5. Renames data/artifacts/ to data/artifacts_legacy_backup/ so old paths
   don't get accidentally written to.

SAFETY:
- Idempotent: skips files that already exist at the destination.
- Original files in data/artifacts/ are preserved (we COPY, not MOVE) until
  the very end, where we rename the parent dir as a backup.
"""

import json
import shutil
from pathlib import Path

from db.sqlite_ops import db_conn
from db.artifacts import (
    init_artifact_tables, RUNS_ROOT, save_artifact, save_state, load_state,
)


def find_artifacts_table():
    """Returns the table name to read from (handles both pre- and post-rename)."""
    with db_conn() as conn:
        tbls = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    if "artifacts" in tbls:
        return "artifacts"
    if "artifacts_legacy_backup" in tbls:
        return "artifacts_legacy_backup"
    return None


# ─── 1. Copy files from data/artifacts to runs/ ───────────────────────────

def copy_artifact_files():
    print("\n[1/3] Copying artifact files from data/artifacts/ to runs/...")
    table = find_artifacts_table()
    if not table:
        print("  warn: No artifacts table found — nothing to copy")
        return 0

    print(f"  Reading from table: {table}")
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT pipeline_run_id, agent_name, artifact_kind, file_path "
            f"FROM {table}"
        ).fetchall()]

    print(f"  Found {len(rows)} indexed files")

    copied = 0
    skipped_missing = 0
    skipped_already_exists = 0
    failed = 0

    # Get list of valid pipeline_run IDs
    with db_conn() as conn:
        valid_runs = {r[0] for r in conn.execute("SELECT id FROM pipeline_runs").fetchall()}

    skipped_no_run = 0

    for row in rows:
        run_id = row["pipeline_run_id"]
        agent = row["agent_name"]
        kind = row["artifact_kind"]
        old_path_str = row["file_path"]

        if run_id not in valid_runs:
            skipped_no_run += 1
            continue

        # Normalize path separators (Windows mix of \ and / from old data)
        old_path = Path(old_path_str.replace("\\", "/"))

        if not old_path.exists():
            skipped_missing += 1
            continue

        # Determine destination
        if kind == "console":
            new_path = RUNS_ROOT / run_id / agent / "console.log"
        else:
            new_path = RUNS_ROOT / run_id / agent / f"{kind}.json"

        new_path.parent.mkdir(parents=True, exist_ok=True)

        if new_path.exists() and new_path.stat().st_size > 0:
            skipped_already_exists += 1
            # Still index it
            byte_size = new_path.stat().st_size
            with db_conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO artifact_index
                        (run_id, agent_name, kind, file_path, byte_size, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (run_id, agent, kind, str(new_path), byte_size, row.get("created_at")))
                conn.commit()
            continue

        try:
            shutil.copy2(old_path, new_path)
            byte_size = new_path.stat().st_size

            # Index the new location
            with db_conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO artifact_index
                        (run_id, agent_name, kind, file_path, byte_size, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (run_id, agent, kind, str(new_path), byte_size, row.get("created_at")))
                conn.commit()

            copied += 1
        except Exception as e:
            print(f"    warn: Failed to copy {old_path} -> {new_path}: {e}")
            failed += 1

    print(f"  OK Copied {copied} files")
    if skipped_already_exists:
        print(f"  OK Skipped {skipped_already_exists} (already at destination, indexed anyway)")
    if skipped_missing:
        print(f"  warn: {skipped_missing} files indexed in DB but not found on disk")
    if skipped_no_run:
        print(f"  warn: {skipped_no_run} files belong to runs not in pipeline_runs")
    if failed:
        print(f"  warn: {failed} files failed to copy")

    return copied


# ─── 2. Reconstruct state.json (now that files exist) ─────────────────────

def reconstruct_shared_state():
    print("\n[2/3] Reconstructing PipelineState.shared from migrated files...")

    AGENT_TO_STATE_KEY = {
        "trend_scout":               "trend_data",
        "competitor_spy":            "competitor_data",
        "keyword_mapper":            "keyword_map",
        "content_architect":         "cluster_plan",
        "faq_architect":             "faq_plan",
        "research_prompt_generator": "research_prompt",
    }

    with db_conn() as conn:
        runs = [dict(r) for r in conn.execute(
            "SELECT id, topic, cluster_id FROM pipeline_runs"
        ).fetchall()]

    rebuilt = 0
    for run in runs:
        run_id = run["id"]
        run_path = RUNS_ROOT / run_id
        if not run_path.exists():
            continue

        state = load_state(run_id)
        state["topic"] = run.get("topic", "")
        state["cluster_id"] = run.get("cluster_id")
        state["shared"] = state.get("shared", {})

        agents_completed = []
        for agent_dir in run_path.iterdir():
            if not agent_dir.is_dir():
                continue
            agent_name = agent_dir.name
            output_file = agent_dir / "output.json"
            if not output_file.exists():
                continue
            try:
                output_data = json.loads(output_file.read_text(encoding="utf-8"))
                state_key = AGENT_TO_STATE_KEY.get(agent_name)
                if state_key:
                    if agent_name == "content_architect" and isinstance(output_data, dict):
                        plan = output_data.get("cluster_plan", output_data)
                        state["shared"][state_key] = plan
                        if output_data.get("cluster_id"):
                            state["shared"]["cluster_id"] = output_data["cluster_id"]
                    else:
                        state["shared"][state_key] = output_data
                agents_completed.append(agent_name)
            except Exception as e:
                print(f"    warn: Could not parse {output_file}: {e}")

        state["agents_completed"] = agents_completed
        state["stage"] = "migrated_completed" if "research_prompt_generator" in agents_completed else "migrated"
        save_state(run_id, state)

        if agents_completed:
            print(f"  OK {run_id}: {len(agents_completed)} agents -> {agents_completed}")
        rebuilt += 1

    print(f"  Reconstructed shared state for {rebuilt} runs")


# ─── 3. Final cleanup ─────────────────────────────────────────────────────

def cleanup_legacy_files():
    print("\n[3/3] Renaming data/artifacts to data/artifacts_legacy_backup...")
    src = Path("data/artifacts")
    dst = Path("data/artifacts_legacy_backup")

    if not src.exists():
        print("  OK No data/artifacts directory found (already cleaned)")
        return

    if dst.exists():
        print(f"  OK Backup already exists at {dst}; leaving src as-is for safety")
        return

    try:
        src.rename(dst)
        print(f"  OK Renamed {src} -> {dst}")
        print(f"     (you can delete this folder once you've verified the dashboard works)")
    except Exception as e:
        print(f"  warn: Could not rename: {e}")
        print(f"     (this is non-critical — the new system uses runs/ folder anyway)")


# ─── Main ─────────────────────────────────────────────────────────────────

def migrate():
    print("=" * 70)
    print("CANVAS HOMES v3 MIGRATION — PHASE 2 (file copy)")
    print("=" * 70)

    init_artifact_tables()

    copied = copy_artifact_files()
    reconstruct_shared_state()

    if copied > 0:
        cleanup_legacy_files()

    print("\n" + "=" * 70)
    print("PHASE 2 COMPLETE")
    print("=" * 70)
    print("\nVerify with:")
    print("  python -c \"from pathlib import Path; ")
    print("    [print(p) for p in Path('runs/prun-cf7ca2f3-4f8').rglob('*.json')]\"")
    print("\nThen:")
    print("  streamlit run dashboard.py")


if __name__ == "__main__":
    migrate()