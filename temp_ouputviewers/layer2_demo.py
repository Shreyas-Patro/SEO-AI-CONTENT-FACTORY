"""
Canvas Homes — AI Agent Pipeline Dashboard
Run from project root:  streamlit run dashboard.py
"""

import streamlit as st
import sys, os, json, time, threading, io, contextlib
from datetime import datetime

# ── PATH SETUP ─────────────────────────────────────────────────────────────
# __file__ is the dashboard.py itself — its directory IS the project root.
# We also force os.chdir so config_loader.py finds config.yaml correctly,
# since it builds its path relative to __file__ (which is fine) but
# Streamlit sometimes changes cwd to something else on Windows.
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)                          # ensure cwd = project root
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── PAGE CONFIG ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Canvas Homes · AI Pipeline",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── STYLES ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── fonts & base ── */
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap');

html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }

/* ── hide streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding: 1.2rem 2rem 2rem; }

/* ── sidebar ── */
section[data-testid="stSidebar"] { background: #0f0f1a; border-right: 1px solid #1f1f35; }
section[data-testid="stSidebar"] * { color: #e8e8f2 !important; }
section[data-testid="stSidebar"] .stTextInput input {
    background: #161624; border: 1px solid #2a2a45;
    border-radius: 8px; color: #e8e8f2 !important; font-size: 14px;
}
section[data-testid="stSidebar"] .stButton button {
    background: #c8ff00; color: #000 !important; font-weight: 700;
    border: none; border-radius: 8px; width: 100%; font-size: 13px;
}
section[data-testid="stSidebar"] .stButton button:hover { opacity: 0.88; }

/* ── metric cards ── */
[data-testid="metric-container"] {
    background: #161624; border: 1px solid #1f1f35;
    border-radius: 10px; padding: 14px 16px;
}
[data-testid="metric-container"] label { color: #8888aa !important; font-size: 11px; font-family: 'Space Mono', monospace; }
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #c8ff00 !important; font-size: 28px; font-family: 'Space Mono', monospace;
}

/* ── agent status badges ── */
.badge {
    display: inline-block; padding: 3px 10px; border-radius: 4px;
    font-family: 'Space Mono', monospace; font-size: 10px; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase;
}
.badge-pending  { background: rgba(136,136,170,0.12); color: #8888aa; }
.badge-running  { background: rgba(0,212,255,0.12);   color: #00d4ff; }
.badge-done     { background: rgba(200,255,0,0.12);   color: #aacc00; }
.badge-error    { background: rgba(255,68,68,0.12);   color: #ff6b6b; }
.badge-approved { background: rgba(0,255,136,0.12);  color: #00ff88; }
.badge-blocked  { background: rgba(255,159,67,0.12); color: #ff9f43; }

/* ── console ── */
.console-box {
    background: #050508; border: 1px solid #1f1f35; border-radius: 8px;
    padding: 14px 16px; font-family: 'Space Mono', monospace; font-size: 11px;
    line-height: 1.8; color: #8888aa; max-height: 320px; overflow-y: auto;
    white-space: pre-wrap; word-break: break-all;
}
.log-info    { color: #5a9fff; }
.log-success { color: #00ff88; }
.log-warn    { color: #ffcc00; }
.log-error   { color: #ff6b6b; }
.log-data    { color: #bb88ff; }
.log-accent  { color: #c8ff00; font-weight: 700; }

/* ── section headers ── */
.section-label {
    font-family: 'Space Mono', monospace; font-size: 9px; color: #8888aa;
    letter-spacing: 0.15em; text-transform: uppercase;
    border-bottom: 1px solid #1f1f35; padding-bottom: 6px; margin-bottom: 12px;
}
.layer-chip {
    display: inline-block; font-family: 'Space Mono', monospace; font-size: 9px;
    padding: 3px 10px; border-radius: 3px; font-weight: 700;
    letter-spacing: 0.1em; text-transform: uppercase; margin-right: 8px;
}
.chip-l1 { background: rgba(200,255,0,0.1); color: #c8ff00; border: 1px solid rgba(200,255,0,0.2); }
.chip-l2 { background: rgba(0,212,255,0.1); color: #00d4ff; border: 1px solid rgba(0,212,255,0.2); }
.chip-gate { background: rgba(255,159,67,0.1); color: #ff9f43; border: 1px solid rgba(255,159,67,0.25); }

/* ── cards ── */
.data-card {
    background: #161624; border: 1px solid #1f1f35; border-radius: 10px;
    padding: 14px 16px; margin-bottom: 8px;
}
.data-card-accent { border-left: 3px solid #c8ff00; }
.data-card h4 { color: #e8e8f2; font-size: 13px; font-weight: 600; margin: 0 0 6px; }
.data-card p  { color: #8888aa; font-size: 12px; line-height: 1.6; margin: 0; }

/* ── AEO quality badges ── */
.q-none     { background: rgba(255,68,68,0.2);  color: #ff8888; padding: 2px 7px; border-radius: 3px; font-size: 9px; font-family:'Space Mono',monospace; }
.q-weak     { background: rgba(255,107,53,0.2); color: #ffaa77; padding: 2px 7px; border-radius: 3px; font-size: 9px; font-family:'Space Mono',monospace; }
.q-moderate { background: rgba(255,204,0,0.15); color: #ffcc44; padding: 2px 7px; border-radius: 3px; font-size: 9px; font-family:'Space Mono',monospace; }
.q-strong   { background: rgba(0,255,136,0.12); color: #44ff88; padding: 2px 7px; border-radius: 3px; font-size: 9px; font-family:'Space Mono',monospace; }

/* ── article type badges ── */
.type-hub      { background: rgba(200,255,0,0.1);   color: #c8ff00; border: 1px solid rgba(200,255,0,0.2);   padding: 2px 8px; border-radius: 4px; font-size: 9px; font-family:'Space Mono',monospace; }
.type-spoke    { background: rgba(0,212,255,0.08);  color: #00d4ff; border: 1px solid rgba(0,212,255,0.15); padding: 2px 8px; border-radius: 4px; font-size: 9px; font-family:'Space Mono',monospace; }
.type-sub_spoke{ background: rgba(176,109,255,0.1); color: #b06dff; border: 1px solid rgba(176,109,255,0.2);padding: 2px 8px; border-radius: 4px; font-size: 9px; font-family:'Space Mono',monospace; }
.type-faq      { background: rgba(255,159,67,0.1);  color: #ff9f43; border: 1px solid rgba(255,159,67,0.2); padding: 2px 8px; border-radius: 4px; font-size: 9px; font-family:'Space Mono',monospace; }

/* ── approval gate ── */
.gate-box {
    background: rgba(255,159,67,0.05); border: 1px solid rgba(255,159,67,0.3);
    border-radius: 12px; padding: 20px 24px; margin: 16px 0;
}
.gate-title { color: #ff9f43; font-size: 18px; font-weight: 700; margin-bottom: 6px; }
.gate-sub { color: #8888aa; font-size: 13px; line-height: 1.6; }

/* ── trend direction pill ── */
.dir-declining { background: rgba(255,68,68,0.12); border: 1px solid rgba(255,68,68,0.3); color: #ff6b6b; padding: 4px 12px; border-radius: 4px; font-family:'Space Mono',monospace; font-size:11px; font-weight:700; display:inline-block; }
.dir-rising    { background: rgba(0,255,136,0.1);  border: 1px solid rgba(0,255,136,0.25);color: #00ff88; padding: 4px 12px; border-radius: 4px; font-family:'Space Mono',monospace; font-size:11px; font-weight:700; display:inline-block; }
.dir-stable    { background: rgba(255,204,0,0.1);  border: 1px solid rgba(255,204,0,0.25); color: #ffcc00; padding: 4px 12px; border-radius: 4px; font-family:'Space Mono',monospace; font-size:11px; font-weight:700; display:inline-block; }

/* ── kw group ── */
.kw-group {
    background: #161624; border: 1px solid #1f1f35; border-left: 3px solid #c8ff00;
    border-radius: 8px; padding: 12px 14px; margin-bottom: 8px;
}
.kw-tag {
    display: inline-block; font-family: 'Space Mono', monospace; font-size: 9px;
    padding: 2px 7px; background: rgba(255,255,255,0.04);
    border: 1px solid #1f1f35; border-radius: 3px; color: #8888aa;
    margin: 2px;
}
.kw-tag-type { background: rgba(0,212,255,0.08); border-color: rgba(0,212,255,0.2); color: #00d4ff; }

/* ── rising tag ── */
.rising-tag {
    display: inline-block; font-family: 'Space Mono', monospace; font-size: 10px;
    padding: 3px 10px; background: rgba(200,255,0,0.06);
    border: 1px solid rgba(200,255,0,0.15); border-radius: 3px; color: #aacc00; margin: 2px;
}

/* ── outline item ── */
.outline-h2 { color: #e8e8f2; font-size: 12px; font-weight: 600; padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
.outline-h3 { color: #8888aa; font-size: 11px; padding: 3px 0 3px 16px; border-bottom: 1px solid rgba(255,255,255,0.03); }

/* ── faq card ── */
.faq-card {
    background: #161624; border: 1px solid #1f1f35; border-radius: 8px;
    padding: 12px 14px; margin-bottom: 8px;
}
.faq-q { color: #e8e8f2; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.faq-a { color: #8888aa; font-size: 12px; line-height: 1.7; }
.faq-meta { margin-top: 8px; }

/* ── comp bar ── */
.comp-bar-bg  { background: #1f1f35; border-radius: 2px; height: 4px; margin-top: 4px; }
.comp-bar-fill-high { background: #ff4444; height: 4px; border-radius: 2px; }
.comp-bar-fill-med  { background: #ffcc00; height: 4px; border-radius: 2px; }
.comp-bar-fill-low  { background: #00ff88; height: 4px; border-radius: 2px; }

/* ── cost row ── */
.cost-row {
    display: flex; justify-content: space-between;
    font-family: 'Space Mono', monospace; font-size: 11px;
    padding: 5px 0; border-bottom: 1px solid rgba(255,255,255,0.03);
}
.cost-label { color: #8888aa; }
.cost-val   { color: #c8ff00; font-weight: 700; }

/* ── run history table ── */
.run-row {
    display: flex; gap: 12px; align-items: center; padding: 7px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 11px;
}
.run-agent { font-family:'Space Mono',monospace; font-size:10px; color:#00d4ff; min-width:140px; }
.run-status { min-width: 80px; }
.run-cost   { font-family:'Space Mono',monospace; color:#c8ff00; min-width:60px; }
.run-summary{ color:#8888aa; flex:1; }
</style>
""", unsafe_allow_html=True)

# ── SESSION STATE INIT ──────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "topic": "hennur",
        "pipeline_stage": "idle",   # idle | running_0..4 | gate | done
        "step_status": {i: "pending" for i in range(5)},
        "step_data": {i: None for i in range(5)},
        "architect_input": None,
        "console_lines": [],
        "gate_approved": False,
        "total_cost": 0.0,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "current_cluster_id": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ── CONSOLE HELPERS ─────────────────────────────────────────────────────────
def clog(msg, kind="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.console_lines.append((ts, msg, kind))

def _render_console():
    lines = st.session_state.console_lines[-120:]  # last 120 lines
    html = '<div class="console-box">'
    for ts, msg, kind in lines:
        cls = f"log-{kind}"
        safe = msg.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        html += f'<span style="color:#4444666;">[{ts}]</span> <span class="{cls}">{safe}</span>\n'
    html += "</div>"
    return html

# ── CAPTURE STDOUT FROM AGENTS ──────────────────────────────────────────────
class _LogCapture(io.StringIO):
    def write(self, s):
        super().write(s)
        s = s.strip()
        if not s:
            return
        kind = "info"
        if any(x in s for x in ["✅", "complete", "Complete"]):  kind = "success"
        elif any(x in s for x in ["❌", "Error", "error", "Failed"]): kind = "error"
        elif any(x in s for x in ["⚠️", "Warning", "warning"]):  kind = "warn"
        elif any(x in s for x in ["$", "cost", "tokens", "Cost"]): kind = "data"
        elif any(x in s for x in ["✅", "→", "Running", "Fetching", "LLM"]): kind = "info"
        clog(s, kind)

@contextlib.contextmanager
def _capture():
    cap = _LogCapture()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = cap
    sys.stderr = cap
    try:
        yield cap
    finally:
        sys.stdout = old_out
        sys.stderr = old_err

# ── IMPORT AGENTS (lazy, with error handling) ──────────────────────────────
@st.cache_resource(show_spinner=False)
def _load_agents():
    try:
        from agents.trend_scout     import run_trend_scout
        from agents.competitor_spy  import run_competitor_spy
        from agents.keyword_mapper  import run_keyword_mapper
        from agents.content_architect import run_content_architect
        from agents.faq_architect   import run_faq_architect
        from db.sqlite_ops import (
            get_stats, list_clusters, get_agent_runs,
            get_articles_by_cluster, get_cluster, init_db
        )
        init_db()
        return {
            "trend_scout":        run_trend_scout,
            "competitor_spy":     run_competitor_spy,
            "keyword_mapper":     run_keyword_mapper,
            "content_architect":  run_content_architect,
            "faq_architect":      run_faq_architect,
            "get_stats":          get_stats,
            "list_clusters":      list_clusters,
            "get_agent_runs":     get_agent_runs,
            "get_articles_by_cluster": get_articles_by_cluster,
            "get_cluster":        get_cluster,
        }
    except Exception as e:
        return {"error": str(e)}

agents = _load_agents()

# ── BUILD ARCHITECT INPUT (mirrors test_market_intel.py logic) ──────────────
def build_architect_input(topic, trend_data, comp_data, keyword_data):
    analysis = trend_data.get("analysis", {})
    raw      = trend_data.get("raw_data", {})
    km       = keyword_data.get("keyword_map", {})
    clf      = trend_data.get("classification", {})
    return {
        "topic":             topic,
        "topic_type":        clf.get("primary_category", "locality"),
        "detected_locality": (clf.get("detected_localities", [topic]) or [topic])[0],
        "trend": {
            "direction":      analysis.get("trend_direction", "unknown"),
            "summary":        analysis.get("trend_summary", ""),
            "seasonal":       raw.get("trends", {}).get("is_seasonal", False),
            "rising_queries": raw.get("trends", {}).get("rising_queries", [])[:10],
            "average_interest": raw.get("trends", {}).get("average_interest", 0),
            "recent_interest":  raw.get("trends", {}).get("recent_interest", 0),
        },
        "keyword_groups":     km.get("keyword_groups", []),
        "quick_win_keywords": km.get("quick_win_keywords", []),
        "strategic_keywords": km.get("strategic_keywords", []),
        "total_keywords":     km.get("total_keywords", 0),
        "aeo_targets":        analysis.get("aeo_targets", []),
        "faq": {
            "paa_questions":    raw.get("paa_questions", []),
            "related_searches": raw.get("related_searches", [])[:30],
            "autocomplete":     raw.get("autocomplete", [])[:20],
        },
        "competition": {
            "competitor_coverage": comp_data.get("analysis", {}).get("competitor_coverage", []),
            "coverage_gaps":       comp_data.get("analysis", {}).get("coverage_gaps", []),
            "our_advantages":      comp_data.get("analysis", {}).get("our_advantages", []),
            "competitor_presence": trend_data.get("competitor_tracker", {}),
        },
        "content_priorities": {
            "top_5_queries":  analysis.get("top_5_priority_queries", []),
            "content_gaps":   analysis.get("content_gaps", []),
            "related_topics": analysis.get("related_topics_to_explore", []),
        },
    }

# ── RENDER HELPERS ──────────────────────────────────────────────────────────
def _badge(status):
    labels = {"pending":"PENDING","running":"RUNNING","done":"DONE",
              "error":"ERROR","approved":"APPROVED","blocked":"AWAITING"}
    return f'<span class="badge badge-{status}">{labels.get(status, status.upper())}</span>'

def _qual_badge(q):
    q = (q or "?").lower()
    cls = {"none":"q-none","weak":"q-weak","moderate":"q-moderate","strong":"q-strong"}.get(q,"q-weak")
    return f'<span class="{cls}">{q.upper()}</span>'

def _type_badge(t):
    t = (t or "spoke").lower()
    return f'<span class="type-{t}">{t.upper()}</span>'

def _dir_pill(d):
    d = (d or "unknown").lower()
    cls = "dir-declining" if "declin" in d else "dir-rising" if "ris" in d else "dir-stable"
    icon = "📉" if "declin" in d else "📈" if "ris" in d else "➡️"
    return f'<span class="{cls}">{icon} {d.upper()}</span>'

def _trunc(s, n=80):
    s = s or "—"
    return s[:n] + "…" if len(s) > n else s

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🏠 Canvas Homes")
    st.markdown('<div style="font-family:Space Mono,monospace;font-size:10px;color:#8888aa;letter-spacing:0.15em;margin-bottom:16px;">AI AGENT PIPELINE</div>', unsafe_allow_html=True)

    if "error" in agents:
        st.error(f"Import error: {agents['error']}")
        st.stop()

    # Topic input
    st.markdown("**Topic**")
    topic = st.text_input("", value=st.session_state.topic, placeholder="e.g. hennur, whitefield...",
                          label_visibility="collapsed", key="topic_input_field")
    st.session_state.topic = topic

    st.markdown("---")

    # Pipeline steps
    steps = [
        (0, "📡 Trend Scout",       "SERP · Trends · AEO",       "l1"),
        (1, "🕵️ Competitor Spy",    "Coverage · Gaps",            "l1"),
        (2, "🗺️ Keyword Mapper",    "Clusters · Strategy",        "l1"),
        (3, "🏗️ Content Architect", "Article Briefs",             "l2"),
        (4, "❓ FAQ Architect",     "AEO-Optimised FAQs",         "l2"),
    ]
    for idx, name, sub, layer in steps:
        status = st.session_state.step_status[idx]
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**{name}**")
            st.caption(sub)
        with col2:
            st.markdown(_badge(status), unsafe_allow_html=True)

    if st.session_state.pipeline_stage == "gate":
        st.markdown("---")
        st.markdown('<span class="chip-gate">⊙ HUMAN GATE</span>', unsafe_allow_html=True)
        st.caption("Layer 1 complete — review before Layer 2")

    st.markdown("---")

    # Cost tracker
    st.markdown("**Run Costs**")
    st.markdown(f"""
    <div class="cost-row"><span class="cost-label">Total cost</span><span class="cost-val">${st.session_state.total_cost:.4f}</span></div>
    <div class="cost-row"><span class="cost-label">Tokens in</span><span class="cost-val">{st.session_state.total_tokens_in:,}</span></div>
    <div class="cost-row"><span class="cost-label">Tokens out</span><span class="cost-val">{st.session_state.total_tokens_out:,}</span></div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    # DB Stats
    if "get_stats" in agents:
        try:
            db_stats = agents["get_stats"]()
            st.markdown("**Database**")
            st.markdown(f"""
            <div class="cost-row"><span class="cost-label">Clusters</span><span class="cost-val">{db_stats['total_clusters']}</span></div>
            <div class="cost-row"><span class="cost-label">Articles</span><span class="cost-val">{db_stats['total_articles']}</span></div>
            <div class="cost-row"><span class="cost-label">Agent runs</span><span class="cost-val">{db_stats['total_agent_runs']}</span></div>
            <div class="cost-row"><span class="cost-label">Total spend</span><span class="cost-val">${db_stats['total_cost_usd']:.4f}</span></div>
            """, unsafe_allow_html=True)
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════════════
# MAIN HEADER
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div style="display:flex;align-items:center;gap:16px;margin-bottom:20px;">
  <div style="background:#c8ff00;border-radius:8px;width:40px;height:40px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;color:#000;font-family:Space Mono,monospace;">CH</div>
  <div>
    <div style="font-size:22px;font-weight:700;letter-spacing:-0.02em;">AI Agent Pipeline</div>
    <div style="font-size:12px;color:#8888aa;font-family:Space Mono,monospace;">CANVAS HOMES · BANGALORE REAL ESTATE INTELLIGENCE</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
tab_pipeline, tab_history, tab_db = st.tabs(["▶  Pipeline", "📋  Run History", "🗄️  Database"])

# ════════════════════════════════════════════════════════════════════
# TAB 1 — PIPELINE
# ════════════════════════════════════════════════════════════════════
with tab_pipeline:

    # ── CONTROL ROW ──────────────────────────────────────────────────
    ctrl1, ctrl2, ctrl3 = st.columns([2, 1, 1])
    with ctrl1:
        st.markdown(f"**Topic:** `{st.session_state.topic}`")
    with ctrl2:
        run_all = st.button("▶  Run Full Pipeline", type="primary",
                            disabled=st.session_state.pipeline_stage not in ("idle", "done"))
    with ctrl3:
        reset = st.button("↺  Reset", disabled=False)

    if reset:
        for k in ["pipeline_stage","step_status","step_data","architect_input",
                  "console_lines","gate_approved","total_cost","total_tokens_in",
                  "total_tokens_out","current_cluster_id"]:
            if k == "step_status":
                st.session_state[k] = {i: "pending" for i in range(5)}
            elif k == "step_data":
                st.session_state[k] = {i: None for i in range(5)}
            elif k in ("total_cost","total_tokens_in","total_tokens_out"):
                st.session_state[k] = 0.0 if "cost" in k else 0
            elif k == "console_lines":
                st.session_state[k] = []
            elif k == "pipeline_stage":
                st.session_state[k] = "idle"
            else:
                st.session_state[k] = None
        st.rerun()

    st.markdown("---")

    # ── CONSOLE ───────────────────────────────────────────────────────
    with st.expander("🖥️  CLI Console", expanded=True):
        console_placeholder = st.empty()
        console_placeholder.markdown(_render_console(), unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════
    # RUN PIPELINE
    # ══════════════════════════════════════════════════════════════════
    if run_all and st.session_state.pipeline_stage == "idle":
        st.session_state.pipeline_stage = "running"
        topic = st.session_state.topic.strip() or "hennur"

        # ── LAYER 1 HEADER ─────────────────────────────────────────
        st.markdown('<span class="chip-l1">LAYER 1</span> **Market Intelligence**', unsafe_allow_html=True)

        # ── AGENT 0: TREND SCOUT ───────────────────────────────────
        st.session_state.step_status[0] = "running"
        clog(f"Starting pipeline for topic: \"{topic}\"", "accent")
        clog("─" * 50, "info")
        clog("[Trend Scout] Initializing...", "info")
        console_placeholder.markdown(_render_console(), unsafe_allow_html=True)

        with st.spinner("📡 Trend Scout running — fetching 15 SERP queries + Google Trends..."):
            try:
                with _capture():
                    trend_data = agents["trend_scout"](topic)
                st.session_state.step_data[0] = trend_data
                st.session_state.step_status[0] = "done"
                cost = trend_data.get("cost_usd", 0)
                st.session_state.total_cost += cost
                clog(f"[Trend Scout] ✅ Complete — cost: ${cost:.4f}", "success")
                console_placeholder.markdown(_render_console(), unsafe_allow_html=True)
            except Exception as e:
                st.session_state.step_status[0] = "error"
                clog(f"[Trend Scout] ❌ FAILED: {e}", "error")
                console_placeholder.markdown(_render_console(), unsafe_allow_html=True)
                st.error(f"Trend Scout failed: {e}")
                st.session_state.pipeline_stage = "idle"
                st.stop()

        # ── AGENT 1: COMPETITOR SPY ────────────────────────────────
        st.session_state.step_status[1] = "running"
        clog("[Competitor Spy] Searching 4 competitors via site: queries...", "info")
        console_placeholder.markdown(_render_console(), unsafe_allow_html=True)

        with st.spinner("🕵️ Competitor Spy running — searching MagicBricks, NoBroker, 99acres, Housing.com..."):
            try:
                with _capture():
                    comp_data = agents["competitor_spy"](topic)
                st.session_state.step_data[1] = comp_data
                st.session_state.step_status[1] = "done"
                cost = comp_data.get("cost_usd", 0)
                st.session_state.total_cost += cost
                clog(f"[Competitor Spy] ✅ Complete — cost: ${cost:.4f}", "success")
                console_placeholder.markdown(_render_console(), unsafe_allow_html=True)
            except Exception as e:
                st.session_state.step_status[1] = "error"
                clog(f"[Competitor Spy] ❌ FAILED: {e}", "error")
                console_placeholder.markdown(_render_console(), unsafe_allow_html=True)
                st.error(f"Competitor Spy failed: {e}")
                st.session_state.pipeline_stage = "idle"
                st.stop()

        # ── AGENT 2: KEYWORD MAPPER ────────────────────────────────
        st.session_state.step_status[2] = "running"
        clog("[Keyword Mapper] Clustering all signals into article groups...", "info")
        console_placeholder.markdown(_render_console(), unsafe_allow_html=True)

        with st.spinner("🗺️ Keyword Mapper running — building content cluster strategy..."):
            try:
                with _capture():
                    kw_data = agents["keyword_mapper"](
                        topic,
                        st.session_state.step_data[0],
                        st.session_state.step_data[1]
                    )
                st.session_state.step_data[2] = kw_data
                st.session_state.step_status[2] = "done"
                cost = kw_data.get("cost_usd", 0)
                st.session_state.total_cost += cost
                clog(f"[Keyword Mapper] ✅ Complete — cost: ${cost:.4f}", "success")
                console_placeholder.markdown(_render_console(), unsafe_allow_html=True)
            except Exception as e:
                st.session_state.step_status[2] = "error"
                clog(f"[Keyword Mapper] ❌ FAILED: {e}", "error")
                console_placeholder.markdown(_render_console(), unsafe_allow_html=True)
                st.error(f"Keyword Mapper failed: {e}")
                st.session_state.pipeline_stage = "idle"
                st.stop()

        # ── BUILD ARCHITECT INPUT & GATE ───────────────────────────
        arch_input = build_architect_input(
            topic,
            st.session_state.step_data[0],
            st.session_state.step_data[1],
            st.session_state.step_data[2],
        )
        st.session_state.architect_input = arch_input
        st.session_state.pipeline_stage = "gate"
        clog("─" * 50, "info")
        clog("⊙ HUMAN APPROVAL GATE — Layer 1 complete. Awaiting review.", "warn")
        console_placeholder.markdown(_render_console(), unsafe_allow_html=True)
        st.rerun()

    # ══════════════════════════════════════════════════════════════════
    # RENDER LAYER 1 OUTPUTS (when data exists)
    # ══════════════════════════════════════════════════════════════════
    if any(st.session_state.step_data[i] is not None for i in range(3)):
        st.markdown("---")
        st.markdown('<span class="chip-l1">LAYER 1</span> **Market Intelligence — Outputs**', unsafe_allow_html=True)

        l1_tabs = st.tabs(["📡 Trend Scout", "🕵️ Competitor Spy", "🗺️ Keyword Mapper"])

        # ── TREND SCOUT OUTPUT ─────────────────────────────────────
        with l1_tabs[0]:
            d = st.session_state.step_data[0]
            if not d:
                st.info("Run the pipeline to see Trend Scout output.")
            else:
                raw      = d.get("raw_data", {})
                analysis = d.get("analysis", {})
                trends   = raw.get("trends", {})
                paa      = raw.get("paa_questions", [])
                related  = raw.get("related_searches", [])
                auto     = raw.get("autocomplete", [])
                aeo_sc   = raw.get("aeo_scores", [])
                comp_tr  = d.get("competitor_tracker", {})
                aeo_tgt  = analysis.get("aeo_targets", [])
                rising   = trends.get("rising_queries", [])
                dir_     = (trends.get("direction") or analysis.get("trend_direction","unknown")).lower()
                high_aeo = len([s for s in aeo_sc if s.get("score",0) >= 70])

                # Stats row
                c1,c2,c3,c4,c5,c6 = st.columns(6)
                c1.metric("PAA Questions", len(paa))
                c2.metric("Related Searches", len(related))
                c3.metric("Autocomplete", len(auto))
                c4.metric("High AEO Opps", high_aeo)
                c5.metric("SERP Calls", d.get("serp_calls_used", 15))
                c6.metric("LLM Cost", f"${d.get('cost_usd',0):.4f}")

                st.markdown("---")

                # Google Trends
                st.markdown('<div class="section-label">Google Trends</div>', unsafe_allow_html=True)
                st.markdown(f'**Direction:** {_dir_pill(dir_)}', unsafe_allow_html=True)
                t1, t2 = st.columns(2)
                t1.metric("12-month average", trends.get("average_interest","—"))
                t2.metric("Recent interest",  trends.get("recent_interest","—"))
                st.caption(analysis.get("trend_summary",""))
                if trends.get("is_seasonal"):
                    st.info("🗓️ Seasonal — time publishing to peak periods")

                if rising:
                    st.markdown("**Rising micro-trends:**")
                    tags = " ".join(f'<span class="rising-tag">↗ {q}</span>' for q in rising)
                    st.markdown(tags, unsafe_allow_html=True)

                st.markdown("---")

                # AEO Targets
                st.markdown('<div class="section-label">AEO Targets — passed to FAQ Architect</div>', unsafe_allow_html=True)
                st.caption("Queries where Google's AI gives a weak or no answer. Publish here = get cited in AI overviews.")
                for t in aeo_tgt[:8]:
                    q_class = {"none":"q-none","weak":"q-weak","moderate":"q-moderate","strong":"q-strong"}.get((t.get("current_answer_quality") or "weak").lower(),"q-weak")
                    st.markdown(f"""
                    <div class="data-card data-card-accent">
                        <h4>{t.get('query','—')}</h4>
                        <p><span class="{q_class}">{(t.get('current_answer_quality') or '?').upper()}</span>
                        &nbsp;&nbsp;<span class="kw-tag">{t.get('content_type','?')}</span></p>
                        <p style="margin-top:6px;">{_trunc(t.get('our_strategy',''),120)}</p>
                    </div>
                    """, unsafe_allow_html=True)

                st.markdown("---")

                # PAA
                st.markdown('<div class="section-label">People Also Ask ({} questions)</div>'.format(len(paa)), unsafe_allow_html=True)
                st.caption("Exact questions real users type into Google → become article H2s and FAQ answers.")
                for i, q in enumerate(paa[:15], 1):
                    st.markdown(f'<div class="data-card"><span style="font-family:Space Mono,monospace;font-size:9px;color:#c8ff00;">{i:02d}</span>&nbsp;&nbsp;{q}</div>', unsafe_allow_html=True)
                if len(paa) > 15:
                    with st.expander(f"Show remaining {len(paa)-15} questions"):
                        for i, q in enumerate(paa[15:], 16):
                            st.write(f"{i}. {q}")

                st.markdown("---")

                # Competitor SERP presence
                st.markdown('<div class="section-label">Competitor SERP Presence</div>', unsafe_allow_html=True)
                max_c = max(comp_tr.values(), default=1)
                for comp, count in sorted(comp_tr.items(), key=lambda x: x[1], reverse=True):
                    pct = int((count / 15) * 100)
                    fill_cls = "comp-bar-fill-high" if count/max_c >= 0.8 else "comp-bar-fill-med" if count/max_c >= 0.5 else "comp-bar-fill-low"
                    st.markdown(f"""
                    <div style="display:flex;justify-content:space-between;font-family:Space Mono,monospace;font-size:10px;color:#8888aa;margin-bottom:2px;">
                        <span>{comp}</span><span style="color:#e8e8f2;">{count}/15</span>
                    </div>
                    <div class="comp-bar-bg"><div class="{fill_cls}" style="width:{pct}%;"></div></div>
                    <div style="margin-bottom:10px;"></div>
                    """, unsafe_allow_html=True)

                st.markdown("---")

                # Content gaps
                gaps = analysis.get("content_gaps", [])
                if gaps:
                    st.markdown('<div class="section-label">Content Gaps</div>', unsafe_allow_html=True)
                    for g in gaps[:5]:
                        pri = (g.get("priority","medium")).upper()
                        icon = {"HIGH":"🔴","MEDIUM":"🟡","LOW":"🟢"}.get(pri,"⚪")
                        st.markdown(f"""
                        <div class="data-card" style="border-left:3px solid {'#ff4444' if pri=='HIGH' else '#ffcc00' if pri=='MEDIUM' else '#00ff88'};">
                            <h4>{icon} {g.get('gap','—')}</h4>
                            <p>→ {g.get('opportunity','')}</p>
                        </div>
                        """, unsafe_allow_html=True)

                # Top 5 priorities
                top5 = analysis.get("top_5_priority_queries", [])
                if top5:
                    st.markdown("---")
                    st.markdown('<div class="section-label">Top 5 Priority Queries — write these first</div>', unsafe_allow_html=True)
                    for i, q in enumerate(top5, 1):
                        st.markdown(f"""
                        <div class="data-card">
                            <div style="font-family:Space Mono,monospace;font-size:9px;color:#c8ff00;margin-bottom:3px;">PRIORITY {i}</div>
                            <h4>{q.get('query','—')}</h4>
                            <p>{_trunc(q.get('suggested_action',''),100)}</p>
                        </div>
                        """, unsafe_allow_html=True)

        # ── COMPETITOR SPY OUTPUT ──────────────────────────────────
        with l1_tabs[1]:
            d = st.session_state.step_data[1]
            if not d:
                st.info("Run the pipeline to see Competitor Spy output.")
            else:
                analysis = d.get("analysis", {})
                comp_cov = analysis.get("competitor_coverage", [])
                gaps     = analysis.get("coverage_gaps", [])
                adv      = analysis.get("our_advantages", [])
                raw      = d.get("raw_results", {})

                total_arts = sum(c.get("articles_found", len(d.get("raw_results",{}).get(c.get("competitor",""),[]))) for c in comp_cov)
                c1,c2,c3,c4 = st.columns(4)
                c1.metric("Competitors Analysed", 4)
                c2.metric("Articles Found", total_arts)
                c3.metric("Coverage Gaps", len(gaps))
                c4.metric("Our Advantages", len(adv))

                st.markdown("---")

                # Competitor breakdown
                st.markdown('<div class="section-label">Competitor Coverage</div>', unsafe_allow_html=True)
                st.caption("site:domain searches — what each competitor has published on this topic.")
                for c in comp_cov:
                    with st.expander(f"**{c.get('competitor','?')}** — {c.get('articles_found',0)} articles"):
                        for art in c.get("articles",[]):
                            st.markdown(f"""
                            <div class="data-card">
                                <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                                    <h4>{_trunc(art.get('title','—'),60)}</h4>
                                    {_type_badge(art.get('content_type','?'))}
                                </div>
                                <p>~{art.get('estimated_word_count','?')} words</p>
                                <p style="color:#00ff88;margin-top:4px;">✓ {_trunc(art.get('strengths',''),70)}</p>
                                <p style="color:#ff6b6b;">✗ {_trunc(art.get('weaknesses',''),70)}</p>
                            </div>
                            """, unsafe_allow_html=True)

                st.markdown("---")

                # Coverage Gaps
                if gaps:
                    st.markdown('<div class="section-label">Coverage Gaps — All Competitors Miss These</div>', unsafe_allow_html=True)
                    st.caption("Topics NONE of the 4 competitors cover. Canvas Homes publishes here first.")
                    for g in gaps:
                        pri  = (g.get("priority","medium")).upper()
                        icon = {"HIGH":"🔴","MEDIUM":"🟡","LOW":"🟢"}.get(pri,"⚪")
                        st.markdown(f"""
                        <div class="data-card" style="border-left:3px solid {'#ff4444' if pri=='HIGH' else '#ffcc00' if pri=='MEDIUM' else '#00ff88'};">
                            <h4>{icon} [{pri}] {g.get('gap','—')}</h4>
                            <p>Suggested type: {g.get('suggested_article_type','?')}</p>
                        </div>
                        """, unsafe_allow_html=True)

                st.markdown("---")

                # Advantages
                if adv:
                    st.markdown('<div class="section-label">Canvas Homes Advantages</div>', unsafe_allow_html=True)
                    for a in adv:
                        st.markdown(f'<div class="data-card"><p>✨ {a}</p></div>', unsafe_allow_html=True)

        # ── KEYWORD MAPPER OUTPUT ──────────────────────────────────
        with l1_tabs[2]:
            d = st.session_state.step_data[2]
            if not d:
                st.info("Run the pipeline to see Keyword Mapper output.")
            else:
                km     = d.get("keyword_map", {})
                groups = km.get("keyword_groups", [])
                qw     = km.get("quick_win_keywords", [])
                sk     = km.get("strategic_keywords", [])

                c1,c2,c3,c4 = st.columns(4)
                c1.metric("Total Keywords", km.get("total_keywords",0))
                c2.metric("Article Groups", len(groups))
                c3.metric("Quick Wins", len(qw))
                c4.metric("Strategic KWs", len(sk))

                st.markdown("---")
                st.markdown('<div class="section-label">Keyword Groups — Each = One Article to Write</div>', unsafe_allow_html=True)
                st.caption("Primary keyword = SEO target. Secondary = subheadings. Long-tail = body copy.")

                for g in groups:
                    pri  = (g.get("priority","medium")).upper()
                    diff = (g.get("difficulty","medium")).upper()
                    vol  = (g.get("estimated_volume","medium")).upper()
                    st.markdown(f"""
                    <div class="kw-group">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                            <strong style="color:#e8e8f2;font-size:13px;">{g.get('group_name','—')}</strong>
                            <span class="kw-tag-type kw-tag">{g.get('suggested_article_type','?')}</span>
                        </div>
                        <div style="font-family:Space Mono,monospace;font-size:10px;color:#00d4ff;margin-bottom:8px;">▸ {g.get('primary_keyword','—')}</div>
                        <div>
                            <span class="kw-tag" style="border-color:{'rgba(255,68,68,0.3)' if pri=='HIGH' else 'rgba(255,204,0,0.3)' if pri=='MEDIUM' else 'rgba(0,255,136,0.3)'};">{pri}</span>
                            <span class="kw-tag">DIFF: {diff}</span>
                            <span class="kw-tag">VOL: {vol}</span>
                            {''.join(f'<span class="kw-tag">{k}</span>' for k in g.get('secondary_keywords',[])[:4])}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                if qw:
                    st.markdown("---")
                    st.markdown('<div class="section-label">⚡ Quick Wins — Low Difficulty, Write First</div>', unsafe_allow_html=True)
                    for i, kw in enumerate(qw, 1):
                        st.markdown(f'<div class="data-card"><span style="font-family:Space Mono,monospace;font-size:9px;color:#c8ff00;">{i:02d}</span>&nbsp;&nbsp;{kw}</div>', unsafe_allow_html=True)

                if sk:
                    st.markdown("---")
                    st.markdown('<div class="section-label">♟ Strategic Keywords — Pillar Content Needed</div>', unsafe_allow_html=True)
                    for i, kw in enumerate(sk, 1):
                        st.markdown(f'<div class="data-card"><span style="font-family:Space Mono,monospace;font-size:9px;color:#8888aa;">{i:02d}</span>&nbsp;&nbsp;{kw}</div>', unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════
    # APPROVAL GATE
    # ══════════════════════════════════════════════════════════════════
    if st.session_state.pipeline_stage == "gate":
        st.markdown("---")
        D = st.session_state.architect_input or {}
        paa_  = D.get("faq",{}).get("paa_questions",[])
        kgs_  = D.get("keyword_groups",[])
        aeo_  = D.get("aeo_targets",[])
        gaps_ = D.get("competition",{}).get("coverage_gaps", D.get("content_priorities",{}).get("content_gaps",[]))

        st.markdown("""
        <div class="gate-box">
            <div class="gate-title">⊙ Human Approval Gate</div>
            <div class="gate-sub">Layer 1 Market Intelligence is complete. Review the output summary below, then approve to pass this data to Content Architect and FAQ Architect.</div>
        </div>
        """, unsafe_allow_html=True)

        g1,g2,g3,g4 = st.columns(4)
        g1.metric("PAA Questions",   len(paa_))
        g2.metric("Article Groups",  len(kgs_))
        g3.metric("AEO Targets",     len(aeo_))
        g4.metric("Coverage Gaps",   len(gaps_))

        with st.expander("📦 View Architect Input Packet (JSON)"):
            st.json(D)

        col_approve, col_reject = st.columns([2, 1])
        with col_approve:
            if st.button("✅  Approve — Proceed to Layer 2", type="primary"):
                st.session_state.gate_approved = True
                st.session_state.pipeline_stage = "layer2"
                clog("✅ APPROVED — Architect input passed to Layer 2", "success")
                st.rerun()
        with col_reject:
            if st.button("✗  Reject & Stop"):
                st.session_state.pipeline_stage = "idle"
                clog("✗ REJECTED — pipeline stopped at approval gate", "error")
                st.rerun()

    # ══════════════════════════════════════════════════════════════════
    # LAYER 2 — RUN ARCHITECTS
    # ══════════════════════════════════════════════════════════════════
    if st.session_state.pipeline_stage == "layer2" and st.session_state.gate_approved:
        st.markdown("---")
        st.markdown('<span class="chip-l2">LAYER 2</span> **Planning — Content & FAQ Architects**', unsafe_allow_html=True)

        topic    = st.session_state.topic
        kw_data  = st.session_state.step_data[2]
        arch_in  = st.session_state.architect_input

        # Build keyword_data in the format content_architect expects
        keyword_data_for_arch = {
            "keyword_map": {
                "total_keywords": arch_in.get("total_keywords", 0),
                "keyword_groups": arch_in.get("keyword_groups", []),
                "quick_win_keywords": arch_in.get("quick_win_keywords", []),
                "strategic_keywords": arch_in.get("strategic_keywords", []),
            }
        }

        # ── CONTENT ARCHITECT ────────────────────────────────────
        if st.session_state.step_data[3] is None:
            st.session_state.step_status[3] = "running"
            clog("[Content Architect] Designing hub-spoke cluster...", "info")

            with st.spinner("🏗️ Content Architect running — designing article briefs and cluster structure..."):
                try:
                    with _capture():
                        arch_result = agents["content_architect"](topic, keyword_data_for_arch)
                    st.session_state.step_data[3] = arch_result
                    st.session_state.step_status[3] = "done"
                    st.session_state.current_cluster_id = arch_result.get("cluster_id")
                    cost = arch_result.get("cost_usd", 0)
                    st.session_state.total_cost += cost
                    clog(f"[Content Architect] ✅ Complete — {arch_result.get('articles_created',0)} articles — cost: ${cost:.4f}", "success")
                except Exception as e:
                    st.session_state.step_status[3] = "error"
                    clog(f"[Content Architect] ❌ FAILED: {e}", "error")
                    st.error(f"Content Architect failed: {e}")
                    st.session_state.pipeline_stage = "idle"
                    st.stop()

        # ── FAQ ARCHITECT ────────────────────────────────────────
        if st.session_state.step_data[3] is not None and st.session_state.step_data[4] is None:
            cluster_id = st.session_state.current_cluster_id
            if cluster_id:
                try:
                    from db.sqlite_ops import get_articles_by_cluster
                    db_articles = get_articles_by_cluster(cluster_id)
                except Exception:
                    db_articles = []

                if db_articles:
                    st.session_state.step_status[4] = "running"
                    clog(f"[FAQ Architect] Generating FAQs for {len(db_articles)} articles...", "info")
                    faq_results = []

                    with st.spinner(f"❓ FAQ Architect running — generating AEO-optimised FAQs for {len(db_articles)} articles..."):
                        for art in db_articles:
                            try:
                                with _capture():
                                    faq_res = agents["faq_architect"](
                                        art["id"], keyword_data_for_arch, cluster_id
                                    )
                                faq_results.append(faq_res)
                                cost = faq_res.get("cost_usd", 0)
                                st.session_state.total_cost += cost
                                clog(f"[FAQ Architect] {art['title'][:40]}... — {len(faq_res.get('faqs',[]))} FAQs — ${cost:.4f}", "success")
                            except Exception as e:
                                clog(f"[FAQ Architect] Article {art['id']} failed: {e}", "warn")

                    st.session_state.step_data[4] = faq_results
                    st.session_state.step_status[4] = "done"
                    st.session_state.pipeline_stage = "done"
                    clog("─" * 50, "info")
                    clog(f"✅ ALL AGENTS COMPLETE — Total cost: ${st.session_state.total_cost:.4f}", "success")
                else:
                    st.warning("No articles found in DB for FAQ generation. Content Architect may not have stored articles correctly.")
                    st.session_state.pipeline_stage = "done"

        st.rerun()

    # ══════════════════════════════════════════════════════════════════
    # LAYER 2 OUTPUTS
    # ══════════════════════════════════════════════════════════════════
    if any(st.session_state.step_data[i] is not None for i in range(3, 5)):
        st.markdown("---")
        st.markdown('<span class="chip-l2">LAYER 2</span> **Planning — Outputs**', unsafe_allow_html=True)

        l2_tabs = st.tabs(["🏗️ Content Architect", "❓ FAQ Architect"])

        # ── CONTENT ARCHITECT OUTPUT ─────────────────────────────
        with l2_tabs[0]:
            d = st.session_state.step_data[3]
            if not d:
                st.info("Approve the gate to run Content Architect.")
            else:
                plan     = d.get("cluster_plan", {})
                articles = plan.get("articles", [])
                hubs     = [a for a in articles if a.get("type") == "hub"]
                spokes   = [a for a in articles if a.get("type") == "spoke"]
                faqs_a   = [a for a in articles if a.get("type") == "faq"]
                subs     = [a for a in articles if a.get("type") == "sub_spoke"]

                c1,c2,c3,c4,c5 = st.columns(5)
                c1.metric("Total Articles", len(articles))
                c2.metric("Hub Articles",   len(hubs))
                c3.metric("Spoke Articles", len(spokes))
                c4.metric("FAQ Pages",      len(faqs_a))
                c5.metric("Total Words", f"{sum(a.get('word_count_target',0) for a in articles):,}")

                st.markdown("---")
                st.markdown(f"**Cluster:** `{plan.get('cluster_name', plan.get('cluster_id','—'))}`")
                st.markdown('<div class="section-label">Article Briefs — Writing Agent Input</div>', unsafe_allow_html=True)
                st.caption("Each brief is a complete instruction set: title, outline, keywords, internal links, word count, special notes.")

                for art in articles:
                    art_type = art.get("type","spoke")
                    wc       = art.get("word_count_target",0)
                    outline  = art.get("outline",[])
                    links    = art.get("internal_links",[])
                    kws      = art.get("target_keywords",{})
                    notes    = art.get("notes","")

                    with st.expander(f"{_type_badge(art_type)} &nbsp; **{art.get('title','—')}**", expanded=(art_type=="hub")):
                        st.markdown(f'/{art.get("slug","—")}', help="URL slug")
                        bc1, bc2, bc3 = st.columns(3)
                        bc1.metric("Words", f"{wc:,}")
                        bc2.metric("FAQs", art.get("faq_count",0))
                        bc3.metric("Internal Links", len(links))

                        st.markdown(f'**Primary keyword:** `{kws.get("primary","—")}`')
                        if kws.get("secondary"):
                            tags = " ".join(f'<span class="kw-tag">{k}</span>' for k in kws["secondary"])
                            st.markdown(tags, unsafe_allow_html=True)

                        if outline:
                            st.markdown("**Outline:**")
                            for h in outline:
                                is_h3 = h.strip().startswith("H3") or h.startswith("  ")
                                clean = h.replace("H2:","").replace("H3:","").replace("  ","").strip()
                                css   = "outline-h3" if is_h3 else "outline-h2"
                                st.markdown(f'<div class="{css}">{clean}</div>', unsafe_allow_html=True)

                        if links:
                            st.markdown("**Internal Links:**")
                            for lnk in links:
                                st.markdown(f'<div style="font-size:11px;color:#8888aa;padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.03);">→ <span style="font-family:Space Mono,monospace;color:#00d4ff;">/{lnk.get("target_slug","")}</span> as "<span style="color:#e8e8f2;">{lnk.get("anchor_text","")}</span>"</div>', unsafe_allow_html=True)

                        if notes:
                            st.markdown(f'<div class="data-card" style="margin-top:10px;"><p>📝 {notes}</p></div>', unsafe_allow_html=True)

                # Linking matrix
                matrix = plan.get("linking_matrix", {})
                if matrix:
                    st.markdown("---")
                    st.markdown('<div class="section-label">Internal Linking Matrix</div>', unsafe_allow_html=True)
                    for from_slug, to_slugs in list(matrix.items())[:8]:
                        targets = " ".join(f'<span class="kw-tag">{s}</span>' for s in to_slugs)
                        st.markdown(f'<div style="display:flex;gap:8px;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.03);font-size:11px;"><span style="font-family:Space Mono,monospace;font-size:10px;color:#00d4ff;min-width:180px;">{from_slug}</span><div>{targets}</div></div>', unsafe_allow_html=True)

        # ── FAQ ARCHITECT OUTPUT ──────────────────────────────────
        with l2_tabs[1]:
            faq_results = st.session_state.step_data[4]
            if not faq_results:
                st.info("Approve the gate to run FAQ Architect.")
            else:
                total_faqs = sum(len(r.get("faqs",[])) for r in faq_results)
                c1,c2,c3 = st.columns(3)
                c1.metric("Articles Processed", len(faq_results))
                c2.metric("Total FAQs", total_faqs)
                c3.metric("Avg Words/Answer", "40–60")

                st.markdown("---")
                st.markdown('<div class="section-label">AEO-Optimised FAQs — FAQPage Schema Ready</div>', unsafe_allow_html=True)
                st.caption("Each answer: self-contained, 40-60 words, starts with keyword, includes specific facts. Ready for FAQPage schema markup.")

                for r in faq_results:
                    faqs = r.get("faqs", [])
                    if not faqs:
                        continue
                    art_id = r.get("article_id","—")
                    with st.expander(f"**Article {art_id}** — {len(faqs)} FAQs"):
                        for faq in faqs:
                            aeo_score = faq.get("aeo_score","—")
                            st.markdown(f"""
                            <div class="faq-card">
                                <div class="faq-q">❓ {faq.get('question','—')}</div>
                                <div class="faq-a">{faq.get('answer','—')}</div>
                                <div class="faq-meta">
                                    <span class="kw-tag">🎯 {faq.get('target_keyword','—')}</span>
                                    <span class="kw-tag">AEO: {aeo_score}/100</span>
                                    <span class="kw-tag">{faq.get('schema_type','FAQPage')}</span>
                                </div>
                                {f'<div style="font-size:10px;color:#8888aa;margin-top:6px;font-style:italic;">🎤 {faq.get("voice_search_variant","")}</div>' if faq.get("voice_search_variant") else ''}
                            </div>
                            """, unsafe_allow_html=True)

    # Pipeline done message
    if st.session_state.pipeline_stage == "done":
        st.markdown("---")
        st.success(f"✅ Pipeline complete — Total cost: **${st.session_state.total_cost:.4f}**")

# ════════════════════════════════════════════════════════════════════
# TAB 2 — RUN HISTORY
# ════════════════════════════════════════════════════════════════════
with tab_history:
    st.markdown("### Agent Run History")
    if "get_agent_runs" not in agents:
        st.error("Cannot connect to database.")
    else:
        try:
            runs = agents["get_agent_runs"](limit=100)
            if not runs:
                st.info("No agent runs recorded yet. Run the pipeline first.")
            else:
                # Summary metrics
                total_cost = sum(r.get("cost_usd",0) for r in runs)
                completed  = sum(1 for r in runs if r.get("status")=="completed")
                failed     = sum(1 for r in runs if r.get("status")=="failed")

                c1,c2,c3,c4 = st.columns(4)
                c1.metric("Total Runs",    len(runs))
                c2.metric("Completed",     completed)
                c3.metric("Failed",        failed)
                c4.metric("Total Spent",   f"${total_cost:.4f}")

                st.markdown("---")

                # Filter by agent
                agent_names = sorted(set(r.get("agent_name","?") for r in runs))
                sel = st.multiselect("Filter by agent", agent_names, default=agent_names)
                filtered = [r for r in runs if r.get("agent_name") in sel]

                # Render run rows
                for r in filtered:
                    status = r.get("status","?")
                    badge_cls = {"completed":"badge-done","failed":"badge-error","running":"badge-running"}.get(status,"badge-pending")
                    cost_str = f"${r.get('cost_usd',0):.4f}"
                    started  = (r.get("started_at","") or "")[:19].replace("T"," ")
                    tok      = f"{r.get('tokens_in',0):,}in / {r.get('tokens_out',0):,}out"
                    summary  = _trunc(r.get("output_summary") or r.get("input_summary") or "—", 80)

                    st.markdown(f"""
                    <div class="run-row">
                        <span class="run-agent">{r.get('agent_name','?')}</span>
                        <span class="run-status"><span class="badge {badge_cls}">{status.upper()}</span></span>
                        <span class="run-cost">{cost_str}</span>
                        <span style="font-family:Space Mono,monospace;font-size:9px;color:#4444666;min-width:140px;">{started}</span>
                        <span style="font-family:Space Mono,monospace;font-size:9px;color:#8888aa;min-width:120px;">{tok}</span>
                        <span class="run-summary">{summary}</span>
                    </div>
                    """, unsafe_allow_html=True)

                    if r.get("error_log"):
                        with st.expander(f"Error — {r.get('id','')}"):
                            st.code(r["error_log"])

        except Exception as e:
            st.error(f"Error loading run history: {e}")

# ════════════════════════════════════════════════════════════════════
# TAB 3 — DATABASE VIEWER
# ════════════════════════════════════════════════════════════════════
with tab_db:
    st.markdown("### Database Explorer")
    if "get_stats" not in agents:
        st.error("Cannot connect to database.")
    else:
        try:
            stats = agents["get_stats"]()
            c1,c2,c3,c4,c5,c6 = st.columns(6)
            c1.metric("Total Facts",       stats["total_facts"])
            c2.metric("Verified Facts",    stats["verified_facts"])
            c3.metric("Total Articles",    stats["total_articles"])
            c4.metric("Published",         stats["published_articles"])
            c5.metric("Total Clusters",    stats["total_clusters"])
            c6.metric("Pending Verif.",    stats["pending_verifications"])

            st.markdown("---")

            # Clusters table
            st.markdown("### Clusters")
            clusters = agents["list_clusters"]()
            if not clusters:
                st.info("No clusters yet.")
            else:
                for cl in clusters:
                    with st.expander(f"**{cl.get('name','?')}** — `{cl.get('id','?')}` — {cl.get('status','?')}"):
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown(f"**Seed topic:** {cl.get('seed_topic','—')}")
                            st.markdown(f"**Created:** {(cl.get('created_at','') or '')[:19].replace('T',' ')}")
                            st.markdown(f"**Status:** {cl.get('status','?')}")

                        with c2:
                            hub_ids   = json.loads(cl.get("hub_article_ids","[]") or "[]")
                            spoke_ids = json.loads(cl.get("spoke_article_ids","[]") or "[]")
                            faq_ids   = json.loads(cl.get("faq_article_ids","[]") or "[]")
                            st.markdown(f"**Hubs:** {len(hub_ids)}")
                            st.markdown(f"**Spokes:** {len(spoke_ids)}")
                            st.markdown(f"**FAQs:** {len(faq_ids)}")

                        # Articles in this cluster
                        try:
                            arts = agents["get_articles_by_cluster"](cl["id"])
                            if arts:
                                st.markdown("**Articles:**")
                                for a in arts:
                                    faq_count = len(json.loads(a.get("faq_json","[]") or "[]"))
                                    st.markdown(f"""
                                    <div class="data-card" style="margin-bottom:4px;">
                                        <div style="display:flex;justify-content:space-between;align-items:center;">
                                            <span>{_type_badge(a.get('article_type','?'))} &nbsp; {a.get('title','—')[:60]}</span>
                                            <span style="font-family:Space Mono,monospace;font-size:9px;color:#8888aa;">{faq_count} FAQs</span>
                                        </div>
                                        <div style="font-family:Space Mono,monospace;font-size:9px;color:#8888aa;margin-top:4px;">/{a.get('slug','—')} · {a.get('status','?')}</div>
                                    </div>
                                    """, unsafe_allow_html=True)
                        except Exception:
                            pass

        except Exception as e:
            st.error(f"Error loading database: {e}")