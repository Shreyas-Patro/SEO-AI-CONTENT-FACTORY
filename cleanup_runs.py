"""
Utility to cancel all running pipeline executions.

Usage:
    python cleanup_runs.py
"""

from db.sqlite_ops import db_conn


def cancel_running_pipelines():
    with db_conn() as conn:
        cursor = conn.execute(
            "UPDATE pipeline_runs SET status='cancelled' WHERE status='running'"
        )
        conn.commit()
        return cursor.rowcount


def main():
    try:
        updated = cancel_running_pipelines()
        print(f"✅ Cleaned up successfully. Rows updated: {updated}")
    except Exception as e:
        print("❌ Error during cleanup:", str(e))


if __name__ == "__main__":
    main()