"""Admin-only: change API keys, manage users."""
import os
import streamlit as st
from pathlib import Path

from dashboard.auth import is_admin


def _load_env() -> dict:
    env_path = Path(".env")
    if not env_path.exists():
        return {}
    out = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _save_env(env_dict: dict):
    """Update .env preserving order."""
    env_path = Path(".env")
    lines = []
    for k, v in env_dict.items():
        lines.append(f"{k}={v}")
    env_path.write_text("\n".join(lines) + "\n")


def render():
    if not is_admin():
        st.error("Admins only.")
        return

    st.markdown("### 🔐 Admin Settings")

    # ── API Keys ──
    st.markdown("#### API Keys")
    st.caption("Updates to .env. Restart the app for changes to take effect.")

    env = _load_env()
    keys = {
        "ANTHROPIC_API_KEY": "Anthropic (Claude)",
        "SERPAPI_API_KEY": "SerpAPI",
        "PERPLEXITY_API_KEY": "Perplexity (optional)",
    }

    new_env = {}
    for var, label in keys.items():
        existing = env.get(var, "")
        masked = "*" * len(existing) if existing else ""
        st.markdown(f"**{label}** — currently: `{masked or 'not set'}`")
        new = st.text_input(f"New {label}", value="", type="password",
                            key=f"adm_{var}", label_visibility="collapsed")
        if new:
            new_env[var] = new
        else:
            new_env[var] = existing

    if st.button("💾 Save API keys", type="primary"):
        _save_env({**env, **new_env})
        st.success("Saved to .env. Restart the app to pick up changes.")

    st.markdown("---")

    # ── User management info ──
    st.markdown("#### User Management")
    st.caption("Users are configured in `.streamlit/secrets.toml`")

    users = dict(st.secrets.get("users", {}))
    for username, data in users.items():
        st.markdown(f"- **{username}** — role: `{data.get('role', 'editor')}` — {data.get('email', '?')}")

    st.code("""
# To add or change a user:
# 1. Generate bcrypt hash:
import bcrypt
print(bcrypt.hashpw(b"new_password", bcrypt.gensalt()).decode())

# 2. Edit .streamlit/secrets.toml:
# [users.new_user]
# password = "$2b$12$..."
# role = "editor"
# name = "New User"
# email = "new@canvas-homes.com"

# 3. Restart the app.
""", language="python")

    st.markdown("---")

    # ── Current settings ──
    st.markdown("#### System Info")
    from config_loader import current_year, get_config
    cfg = get_config()
    st.markdown(f"- Current year: **{current_year()}**")
    st.markdown(f"- Quality min fact score: `{cfg['quality']['min_fact_check_confidence']}`")
    st.markdown(f"- Quality min brand score: `{cfg['quality']['min_brand_tone_score']}`")
    st.markdown(f"- Max iterations per article: `{cfg['quality']['max_quality_loop_iterations']}`")
    st.markdown(f"- Topic budget cap: `${cfg['budget']['max_llm_dollars_per_topic']}`")