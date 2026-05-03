import streamlit as st


def inject_styles():
    st.markdown("""
<style>
#MainMenu, footer { visibility: hidden; }
.block-container { padding: 1rem 2rem 2rem; max-width: 1400px; }

section[data-testid="stSidebar"] {
    background: #0f0f1a;
    border-right: 1px solid #1f1f35;
}
section[data-testid="stSidebar"] .stButton button {
    background: #c8ff00;
    color: #000 !important;
    font-weight: 700;
    border: none;
    border-radius: 8px;
    width: 100%;
    font-size: 13px;
}

[data-testid="metric-container"] {
    background: #161624;
    border: 1px solid #1f1f35;
    border-radius: 10px;
    padding: 12px 14px;
}

.agent-card {
    border-radius: 10px;
    padding: 10px 12px;
    margin: 4px 0;
    min-height: 64px;
    transition: all 0.2s ease;
}
.agent-active {
    background: linear-gradient(90deg, #1a3a1a 0%, #0a1f0a 100%);
    border: 2px solid #00ff88;
    box-shadow: 0 0 12px rgba(0,255,136,0.3);
}
.agent-pending {
    background: #161624;
    border: 1px solid #1f1f35;
    opacity: 0.45;
}
.agent-done {
    background: #0a1a2a;
    border: 1px solid #1f3a5a;
}
.agent-failed {
    background: #2a0a0a;
    border: 1px solid #5a1f1f;
}
.agent-name {
    font-weight: 700;
    font-size: 11px;
    color: #fff;
}
.agent-detail {
    font-family: monospace;
    font-size: 9px;
    color: #aaa;
    margin-top: 3px;
}
.pulse {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #00ff88;
    box-shadow: 0 0 6px #00ff88;
    animation: pulse 1.4s ease-in-out infinite;
    margin-right: 5px;
}
@keyframes pulse {
    0%,100% { opacity: 1; transform: scale(1); }
    50%     { opacity: 0.5; transform: scale(1.3); }
}

.iter-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    background: #1f3a5a;
    color: #c8ff00;
    font-size: 10px;
    font-family: monospace;
}

.layer-tag {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 6px;
    font-size: 9px;
    font-weight: bold;
    color: #000;
    margin-right: 4px;
}
.layer-1 { background: #6ee7b7; }
.layer-2 { background: #fbbf24; }
.layer-3 { background: #c8ff00; }
</style>
""", unsafe_allow_html=True)