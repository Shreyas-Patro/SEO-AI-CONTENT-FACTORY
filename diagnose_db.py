"""
Diagnostic script — shows your current SQLite schema so we can see
what migration needs to do.

Run with: python diagnose_db.py
"""

import sqlite3
import json
from pathlib import Path

# Try to find the DB the same way the rest of the app does
try:
    from config_loader import get_path
    DB_PATH = get_path("database")
except Exception as e:
    print(f"Couldn't load config: {e}")
    print("Falling back to common paths...")
    for p in ["data/canvas.db", "canvas.db", "data/canvas-ai.db"]:
        if Path(p).exists():
            DB_PATH = p
            break
    else:
        print("No DB found. Update DB_PATH in this script manually.")
        raise SystemExit(1)


print("=" * 70)
print(f"DB PATH: {DB_PATH}")
print(f"DB SIZE: {Path(DB_PATH).stat().st_size / 1024:.1f} KB" if Path(DB_PATH).exists() else "DB DOES NOT EXIST")
print("=" * 70)

if not Path(DB_PATH).exists():
    print("\nNo DB file found yet. Migration will create it from scratch.")
    raise SystemExit(0)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# 1. List all tables
print("\n[ALL TABLES]")
rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
table_names = [r[0] for r in rows]
for t in table_names:
    count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t:35s} {count} rows")

# 2. For each interesting table, show columns
INTERESTING = [
    "pipeline_runs",
    "pipeline_run_artifacts",
    "artifact_index",
    "agent_runs",
    "articles",
    "clusters",
    "facts",
]

for t in INTERESTING:
    if t not in table_names:
        continue
    print(f"\n[COLUMNS: {t}]")
    cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
    for c in cols:
        d = dict(c)
        print(f"  {d['name']:25s} {d['type']:15s} default={d['dflt_value']}  notnull={d['notnull']}  pk={d['pk']}")

# 3. Show a sample row from pipeline_runs if it exists
if "pipeline_runs" in table_names:
    print("\n[SAMPLE: most recent pipeline_runs row]")
    row = conn.execute("SELECT * FROM pipeline_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    if row:
        for k in row.keys():
            v = row[k]
            v_str = str(v)[:80] if v is not None else "NULL"
            print(f"  {k:25s} = {v_str}")
    else:
        print("  (table is empty)")

# 4. If there's a legacy blob-storage table, show its first row
for legacy_name in ["pipeline_run_artifacts", "agent_artifacts", "run_artifacts"]:
    if legacy_name in table_names:
        print(f"\n[LEGACY BLOB TABLE: {legacy_name}]")
        cols = conn.execute(f"PRAGMA table_info({legacy_name})").fetchall()
        col_names = [dict(c)["name"] for c in cols]
        print(f"  Columns: {col_names}")
        sample = conn.execute(f"SELECT * FROM {legacy_name} LIMIT 1").fetchone()
        if sample:
            print("  Sample row (truncated):")
            for k in sample.keys():
                v = sample[k]
                v_str = str(v)[:100] if v is not None else "NULL"
                print(f"    {k:20s} = {v_str}")

conn.close()

print("\n" + "=" * 70)
print("DONE. Paste this output back so I can fix migration to match your schema.")
print("=" * 70)