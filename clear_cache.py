import sqlite3
import hashlib
import json
from datetime import datetime, timedelta
 
DB_PATH = "./data/canvas.db"
 
topic = "HSR Layout"
old_key = f"trends_v2:{hashlib.md5(topic.lower().encode()).hexdigest()}"
 
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
 
# Show schema
c.execute("PRAGMA table_info(api_cache)")
cols = c.fetchall()
print("api_cache columns:", [(col[1], col[2]) for col in cols])
 
# Show all trends keys
c.execute("SELECT cache_key FROM api_cache WHERE cache_key LIKE '%trends%'")
print("Trends cache keys:", c.fetchall())
 
# Delete all trends cache entries
c.execute("DELETE FROM api_cache WHERE cache_key LIKE '%trends%'")
conn.commit()
print(f"Deleted {c.rowcount} trends cache row(s)")
 
# Test that cache read/write works with real column names
print("\nTesting cache read/write...")
test_key = "test_cache_check_123"
test_value = json.dumps({"ok": True})
expires = (datetime.utcnow() + timedelta(days=1)).isoformat()
created = datetime.utcnow().isoformat()
 
try:
    c.execute(
        "INSERT OR REPLACE INTO api_cache (cache_key, response_json, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (test_key, test_value, created, expires)
    )
    conn.commit()
    print("  Write OK")
 
    c.execute("SELECT response_json FROM api_cache WHERE cache_key = ?", (test_key,))
    row = c.fetchone()
    if row:
        parsed = json.loads(row[0])
        print(f"  Read OK: {parsed}")
    else:
        print("  Read FAILED")
 
    c.execute("DELETE FROM api_cache WHERE cache_key = ?", (test_key,))
    conn.commit()
except Exception as e:
    print(f"  Cache test FAILED: {e}")
 
conn.close()
print("\nDone.")
print("Now share your db/sqlite_ops.py so I can check column names match.")
