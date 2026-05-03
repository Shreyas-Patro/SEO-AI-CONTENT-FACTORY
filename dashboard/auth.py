"""
Hardcoded multi-user auth for Canvas Homes dashboard.

Users live in .streamlit/secrets.toml. Passwords are bcrypt hashed.
Roles: admin (everything), editor (no API key changes).
"""
import streamlit as st
import bcrypt


def _verify_password(password: str, stored: str) -> bool:
    return password == stored

def _get_users() -> dict:
    """Load users from secrets.toml."""
    if "users" not in st.secrets:
        st.error("No users configured. Add users to .streamlit/secrets.toml")
        st.stop()
    return dict(st.secrets["users"])


def login_page():
    """Render login form. Returns username or None."""
    st.markdown("# 🏠 Canvas Homes Dashboard")
    st.markdown("### Sign In")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Sign In", type="primary", use_container_width=True)

    if submit:
        users = _get_users()
        if username in users and _verify_password(password, users[username]["password"]):
            st.session_state["auth_user"] = username
            st.session_state["auth_role"] = users[username].get("role", "editor")
            st.session_state["auth_name"] = users[username].get("name", username)
            st.rerun()
        else:
            st.error("Invalid username or password")

    return None


def require_login():
    """Call at top of every page. Redirects to login if not authenticated."""
    if "auth_user" not in st.session_state:
        login_page()
        st.stop()


def is_admin() -> bool:
    return st.session_state.get("auth_role") == "admin"


def logout_button(location=st.sidebar):
    if "auth_user" in st.session_state:
        location.markdown(f"**Signed in as:** {st.session_state.get('auth_name')}")
        location.caption(f"Role: {st.session_state.get('auth_role')}")
        if location.button("Sign Out"):
            for k in ("auth_user", "auth_role", "auth_name"):
                st.session_state.pop(k, None)
            st.rerun()