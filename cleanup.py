from db.sqlite_ops import db_conn

with db_conn() as conn:
    conn.execute("UPDATE pipeline_runs SET status='cancelled' WHERE status='running'")
    conn.commit()

print("Cleaned up")
