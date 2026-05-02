
from db.sqlite_ops import db_conn
with db_conn() as conn:
    conn.execute("DELETE FROM articles WHERE cluster_id IN (SELECT id FROM clusters WHERE topic='Koramangala')")
    conn.execute("DELETE FROM clusters WHERE topic='Koramangala'")
    conn.commit()
print('Cleaned')
"@ | Out-File -Encoding utf8 cleanup.py"