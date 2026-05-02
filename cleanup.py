"""
cleanup.py — inspect the clusters schema, then delete Koramangala duplicates.

Run with: python cleanup.py
"""
from db.sqlite_ops import db_conn

TOPIC = "Koramangala"

with db_conn() as conn:
    # 1. Show the clusters table schema so we know what column to filter on
    print("=== clusters table schema ===")
    cur = conn.execute("PRAGMA table_info(clusters)")
    cols = [row[1] for row in cur.fetchall()]
    print("columns:", cols)

    # 2. Find the right "topic-like" column
    candidate_cols = ["topic", "name", "topic_name", "title", "primary_keyword"]
    topic_col = next((c for c in candidate_cols if c in cols), None)

    if not topic_col:
        # Fallback: use pipeline_runs.topic to find the cluster_id, then delete by id
        print("\nNo topic column on clusters — joining via pipeline_runs.topic")
        cur = conn.execute(
            "SELECT cluster_id FROM pipeline_runs "
            "WHERE topic = ? AND cluster_id IS NOT NULL",
            (TOPIC,),
        )
        cluster_ids = [r[0] for r in cur.fetchall()]
        print(f"Found {len(cluster_ids)} cluster(s) for topic={TOPIC!r}: {cluster_ids}")

        if cluster_ids:
            placeholders = ",".join("?" for _ in cluster_ids)
            cur = conn.execute(
                f"DELETE FROM articles WHERE cluster_id IN ({placeholders})",
                cluster_ids,
            )
            print(f"Deleted {cur.rowcount} articles")
            cur = conn.execute(
                f"DELETE FROM clusters WHERE id IN ({placeholders})",
                cluster_ids,
            )
            print(f"Deleted {cur.rowcount} clusters")
            conn.commit()
            print("✓ Cleaned")
        else:
            print("Nothing to delete.")
    else:
        print(f"\nFiltering clusters by column: {topic_col}")
        cur = conn.execute(
            f"SELECT id FROM clusters WHERE {topic_col} = ?", (TOPIC,)
        )
        cluster_ids = [r[0] for r in cur.fetchall()]
        print(f"Found {len(cluster_ids)} cluster(s): {cluster_ids}")

        if cluster_ids:
            placeholders = ",".join("?" for _ in cluster_ids)
            cur = conn.execute(
                f"DELETE FROM articles WHERE cluster_id IN ({placeholders})",
                cluster_ids,
            )
            print(f"Deleted {cur.rowcount} articles")
            cur = conn.execute(
                f"DELETE FROM clusters WHERE id IN ({placeholders})",
                cluster_ids,
            )
            print(f"Deleted {cur.rowcount} clusters")
            conn.commit()
            print("✓ Cleaned")
        else:
            print("Nothing to delete.")