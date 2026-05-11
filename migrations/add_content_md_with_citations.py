"""
Add content_md_with_citations column to articles table.
Safe to run multiple times.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.sqlite_ops import db_conn


def run():
    with db_conn() as conn:
        cur = conn.execute("PRAGMA table_info(articles)")
        cols = [row[1] for row in cur.fetchall()]
        if "content_md_with_citations" in cols:
            print("✓ content_md_with_citations already exists, nothing to do")
            return
        conn.execute(
            "ALTER TABLE articles ADD COLUMN content_md_with_citations TEXT DEFAULT ''"
        )
        conn.commit()
        print("✓ Added content_md_with_citations column to articles")


if __name__ == "__main__":
    run()