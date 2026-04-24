"""
Run this from the project root to fix the database issues:
    python fix_db.py
"""
import sqlite3
import os

DB_PATH = os.path.join("data", "canvas.db")

conn = sqlite3.connect(DB_PATH, timeout=10)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=10000")

# ── 1. Show what's currently in the DB ────────────────────────────────────
clusters = conn.execute("SELECT id, name, seed_topic FROM clusters").fetchall()
print(f"Found {len(clusters)} clusters:")
for c in clusters:
    print(f"  {c[0]} | {c[1]} | topic={c[2]}")

articles = conn.execute("SELECT id, slug, cluster_id FROM articles").fetchall()
print(f"\nFound {len(articles)} articles:")
for a in articles:
    print(f"  {a[0]} | {a[1]} | cluster={a[2]}")

runs = conn.execute("SELECT id, agent_name, status FROM agent_runs").fetchall()
print(f"\nFound {len(runs)} agent runs")

# ── 2. Wipe everything so we start fresh ──────────────────────────────────
print("\nCleaning all pipeline data...")
conn.execute("DELETE FROM articles")
conn.execute("DELETE FROM agent_runs")
conn.execute("DELETE FROM clusters")
conn.commit()
print("Done — all clusters, articles, and agent runs removed")

# ── 3. Verify ─────────────────────────────────────────────────────────────
count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
print(f"Articles remaining: {count}")
count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
print(f"Clusters remaining: {count}")

conn.close()
print("\nDB is clean. You can now run the pipeline fresh.")