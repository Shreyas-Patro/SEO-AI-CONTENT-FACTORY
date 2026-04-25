"""
SQLite operations — central database for Canvas Homes AI Engine.

KEY FIX in this version (round 3):
- Bug: 'database is locked' errors cascading through all subsequent inserts
- Root cause: when an INSERT raised (e.g. UNIQUE constraint), the connection
  was never closed because there was no try/finally. On Windows, the leaked
  handle blocked all subsequent writes for ~10 seconds (busy_timeout).
- Fix: every CRUD function now wraps DB ops in try/finally to guarantee close().
- Also added: create_article_with_unique_slug() that auto-suffixes duplicates
  ("my-slug" -> "my-slug-2" -> "my-slug-3") instead of crashing.
"""

import sqlite3
import json
import uuid
import os
from contextlib import contextmanager
from datetime import datetime
from config_loader import get_path

DB_PATH = get_path("database")


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_conn():
    """
    Context-manager wrapper that GUARANTEES the connection is closed,
    even if an exception is raised. This is the fix for the cascading
    'database is locked' errors. Use this for ALL new code:
        with db_conn() as conn:
            conn.execute(...)
            conn.commit()
    """
    conn = get_conn()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def init_db():
    """Create all tables. Safe to run multiple times."""
    with db_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            url TEXT,
            title TEXT,
            author TEXT,
            date_published TEXT,
            date_accessed TEXT,
            source_type TEXT,
            reliability_score REAL DEFAULT 0.8
        );

        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            source_id TEXT,
            source_url TEXT,
            source_title TEXT,
            source_date TEXT,
            ingestion_date TEXT,
            category TEXT,
            location TEXT,
            confidence REAL DEFAULT 1.0,
            verified INTEGER DEFAULT 0,
            used_in_articles TEXT DEFAULT '[]',
            embedding_id TEXT,
            FOREIGN KEY (source_id) REFERENCES sources(id)
        );

        CREATE TABLE IF NOT EXISTS clusters (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            seed_topic TEXT,
            status TEXT DEFAULT 'planning',
            hub_article_ids TEXT DEFAULT '[]',
            spoke_article_ids TEXT DEFAULT '[]',
            faq_article_ids TEXT DEFAULT '[]',
            keyword_map TEXT DEFAULT '{}',
            market_intel TEXT DEFAULT '{}',
            cluster_plan TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY,
            title TEXT,
            slug TEXT UNIQUE,
            cluster_id TEXT,
            article_type TEXT,
            status TEXT DEFAULT 'planned',
            current_stage TEXT DEFAULT 'planned',
            content_md TEXT DEFAULT '',
            meta_title TEXT,
            meta_description TEXT,
            schema_json TEXT DEFAULT '{}',
            word_count INTEGER DEFAULT 0,
            readability_score REAL,
            brand_tone_score REAL,
            fact_check_score REAL,
            internal_link_count INTEGER DEFAULT 0,
            target_keywords TEXT DEFAULT '[]',
            outline TEXT DEFAULT '[]',
            faq_json TEXT DEFAULT '[]',
            created_at TEXT,
            updated_at TEXT,
            history TEXT DEFAULT '[]',
            FOREIGN KEY (cluster_id) REFERENCES clusters(id)
        );

        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            cluster_id TEXT,
            article_id TEXT,
            status TEXT DEFAULT 'running',
            input_summary TEXT,
            output_summary TEXT,
            started_at TEXT,
            completed_at TEXT,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            error_log TEXT,
            FOREIGN KEY (cluster_id) REFERENCES clusters(id),
            FOREIGN KEY (article_id) REFERENCES articles(id)
        );

        CREATE TABLE IF NOT EXISTS verification_queue (
            id TEXT PRIMARY KEY,
            fact_id TEXT,
            article_id TEXT,
            claim_text TEXT,
            issue_type TEXT,
            suggested_correction TEXT,
            status TEXT DEFAULT 'pending',
            resolved_by TEXT,
            created_at TEXT,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS api_cache (
            cache_key TEXT PRIMARY KEY,
            response_json TEXT,
            created_at TEXT,
            expires_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
        CREATE INDEX IF NOT EXISTS idx_facts_location ON facts(location);
        CREATE INDEX IF NOT EXISTS idx_articles_cluster ON articles(cluster_id);
        CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_cluster ON agent_runs(cluster_id);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);
        """)
        conn.commit()
    print(f"Database initialized at {DB_PATH}")


# ─── HELPERS ───────────────────────────────────────────────────────────────

def _now():
    return datetime.utcnow().isoformat()

def _uuid():
    return str(uuid.uuid4())[:12]


# ─── FACTS CRUD ───────────────────────────────────────────────────────────

def insert_fact(content, source_url="", source_title="", source_date="",
                category="general", location="", confidence=1.0, source_id=None):
    fact_id = f"fact-{_uuid()}"
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO facts (id, content, source_id, source_url, source_title, source_date,
                              ingestion_date, category, location, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fact_id, content, source_id, source_url, source_title, source_date,
              _now(), category, location, confidence))
        conn.commit()
    return fact_id


def get_facts(category=None, location=None, verified_only=False, limit=100):
    query = "SELECT * FROM facts WHERE 1=1"
    params = []
    if category:
        query += " AND category = ?"
        params.append(category)
    if location:
        query += " AND location LIKE ?"
        params.append(f"%{location}%")
    if verified_only:
        query += " AND verified >= 1"
    query += f" ORDER BY ingestion_date DESC LIMIT {limit}"
    with db_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_fact_by_id(fact_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    return dict(row) if row else None


def update_fact(fact_id, **kwargs):
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [fact_id]
    with db_conn() as conn:
        conn.execute(f"UPDATE facts SET {sets} WHERE id = ?", vals)
        conn.commit()


# ─── SOURCES CRUD ─────────────────────────────────────────────────────────

def insert_source(url, title, author="", date_published="", source_type="research_doc"):
    source_id = f"src-{_uuid()}"
    with db_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO sources (id, url, title, author, date_published, date_accessed, source_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source_id, url, title, author, date_published, _now(), source_type))
        conn.commit()
    return source_id


# ─── CLUSTERS CRUD ────────────────────────────────────────────────────────

def create_cluster(name, seed_topic):
    cluster_id = f"cl-{_uuid()}"
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO clusters (id, name, seed_topic, status, created_at, updated_at)
            VALUES (?, ?, ?, 'planning', ?, ?)
        """, (cluster_id, name, seed_topic, _now(), _now()))
        conn.commit()
    return cluster_id


def get_cluster(cluster_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
    return dict(row) if row else None


def update_cluster(cluster_id, **kwargs):
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [cluster_id]
    with db_conn() as conn:
        conn.execute(f"UPDATE clusters SET {sets} WHERE id = ?", vals)
        conn.commit()


def list_clusters(status=None):
    with db_conn() as conn:
        if status:
            rows = conn.execute("SELECT * FROM clusters WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM clusters ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ─── ARTICLES CRUD ────────────────────────────────────────────────────────

def _slug_exists(slug):
    """Check if a slug is already in the articles table."""
    with db_conn() as conn:
        row = conn.execute("SELECT 1 FROM articles WHERE slug = ?", (slug,)).fetchone()
    return row is not None


def _make_unique_slug(slug):
    """
    Given a desired slug, return one that's guaranteed not to exist in the DB.
    Appends -2, -3, -4 etc. if collisions occur.
    """
    if not slug:
        slug = f"untitled-{_uuid()[:6]}"

    if not _slug_exists(slug):
        return slug

    i = 2
    while i < 100:
        candidate = f"{slug}-{i}"
        if not _slug_exists(candidate):
            return candidate
        i += 1

    # Fallback: append a UUID
    return f"{slug}-{_uuid()[:6]}"


def create_article(title, slug, cluster_id, article_type, target_keywords=None, outline=None):
    """
    Create an article. Auto-suffixes slug on collision rather than raising.
    This prevents the 'UNIQUE constraint failed' cascade we saw on the MG Road run.
    """
    article_id = f"art-{_uuid()}"
    safe_slug = _make_unique_slug(slug or "")

    with db_conn() as conn:
        conn.execute("""
            INSERT INTO articles (id, title, slug, cluster_id, article_type, status,
                                 target_keywords, outline, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?)
        """, (article_id, title, safe_slug, cluster_id, article_type,
              json.dumps(target_keywords or []), json.dumps(outline or []),
              _now(), _now()))
        conn.commit()
    return article_id


def get_article(article_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    return dict(row) if row else None


def get_article_by_slug(slug):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM articles WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def update_article(article_id, **kwargs):
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [article_id]
    with db_conn() as conn:
        conn.execute(f"UPDATE articles SET {sets} WHERE id = ?", vals)
        conn.commit()


def add_article_history(article_id, stage, changes_summary, content_snapshot):
    article = get_article(article_id)
    if not article:
        return
    history = json.loads(article.get("history", "[]"))
    history.append({
        "stage": stage,
        "timestamp": _now(),
        "changes_summary": changes_summary,
        "content_length": len(content_snapshot),
        "content_snapshot": content_snapshot
    })
    update_article(article_id, history=json.dumps(history))


def get_articles_by_cluster(cluster_id, status=None):
    query = "SELECT * FROM articles WHERE cluster_id = ?"
    params = [cluster_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY article_type, created_at"
    with db_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ─── AGENT RUNS CRUD ──────────────────────────────────────────────────────

def start_agent_run(agent_name, cluster_id=None, article_id=None, input_summary=""):
    run_id = f"run-{_uuid()}"
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO agent_runs (id, agent_name, cluster_id, article_id, status,
                                   input_summary, started_at)
            VALUES (?, ?, ?, ?, 'running', ?, ?)
        """, (run_id, agent_name, cluster_id, article_id, input_summary, _now()))
        conn.commit()
    return run_id


def complete_agent_run(run_id, output_summary="", tokens_in=0, tokens_out=0, cost_usd=0.0):
    with db_conn() as conn:
        conn.execute("""
            UPDATE agent_runs SET status='completed', output_summary=?, completed_at=?,
            tokens_in=?, tokens_out=?, cost_usd=? WHERE id=?
        """, (output_summary, _now(), tokens_in, tokens_out, cost_usd, run_id))
        conn.commit()


def fail_agent_run(run_id, error_log=""):
    with db_conn() as conn:
        conn.execute("""
            UPDATE agent_runs SET status='failed', error_log=?, completed_at=? WHERE id=?
        """, (error_log, _now(), run_id))
        conn.commit()


def get_agent_runs(cluster_id=None, agent_name=None, limit=50):
    query = "SELECT * FROM agent_runs WHERE 1=1"
    params = []
    if cluster_id:
        query += " AND cluster_id = ?"
        params.append(cluster_id)
    if agent_name:
        query += " AND agent_name = ?"
        params.append(agent_name)
    query += f" ORDER BY started_at DESC LIMIT {limit}"
    with db_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ─── VERIFICATION QUEUE ───────────────────────────────────────────────────

def add_to_verification_queue(fact_id=None, article_id=None, claim_text="",
                               issue_type="unverifiable", suggested_correction=""):
    item_id = f"vq-{_uuid()}"
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO verification_queue (id, fact_id, article_id, claim_text,
                                           issue_type, suggested_correction, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (item_id, fact_id, article_id, claim_text, issue_type, suggested_correction, _now()))
        conn.commit()
    return item_id


def get_pending_verifications(limit=50):
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM verification_queue WHERE status='pending' ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_verification(item_id, resolved_by="human"):
    with db_conn() as conn:
        conn.execute("""
            UPDATE verification_queue SET status='resolved', resolved_by=?, resolved_at=? WHERE id=?
        """, (resolved_by, _now(), item_id))
        conn.commit()


# ─── API CACHE ────────────────────────────────────────────────────────────

def cache_get(key):
    with db_conn() as conn:
        row = conn.execute(
            "SELECT response_json FROM api_cache WHERE cache_key=? AND expires_at > ?",
            (key, _now())
        ).fetchone()
    if row:
        return json.loads(row["response_json"])
    return None


def cache_set(key, data, ttl_days=7):
    from datetime import timedelta
    expires = (datetime.utcnow() + timedelta(days=ttl_days)).isoformat()
    with db_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO api_cache (cache_key, response_json, created_at, expires_at)
            VALUES (?, ?, ?, ?)
        """, (key, json.dumps(data), _now(), expires))
        conn.commit()


def cache_clear_for_topic(topic):
    """Helpful for debugging — clear all cache entries containing a topic string."""
    with db_conn() as conn:
        conn.execute("DELETE FROM api_cache WHERE cache_key LIKE ?", (f"%{topic}%",))
        conn.commit()


# ─── STATS ────────────────────────────────────────────────────────────────

def get_stats():
    with db_conn() as conn:
        stats = {
            "total_facts": conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
            "verified_facts": conn.execute("SELECT COUNT(*) FROM facts WHERE verified >= 1").fetchone()[0],
            "total_articles": conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
            "published_articles": conn.execute("SELECT COUNT(*) FROM articles WHERE status='published'").fetchone()[0],
            "total_clusters": conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0],
            "pending_verifications": conn.execute("SELECT COUNT(*) FROM verification_queue WHERE status='pending'").fetchone()[0],
            "total_agent_runs": conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0],
            "total_cost_usd": conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_runs").fetchone()[0],
        }
    return stats


if __name__ == "__main__":
    init_db()
    stats = get_stats()
    print("Database stats:", json.dumps(stats, indent=2))