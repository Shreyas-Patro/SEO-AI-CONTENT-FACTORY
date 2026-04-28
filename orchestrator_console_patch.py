"""
PATCH: Add load_agent_console to orchestrator.py

The v3 dashboard imports `load_agent_console` from orchestrator, but it
was never added in the v3 bundle. This adds the function.

INSTALL:
    1. Open orchestrator.py
    2. Find the block where load_agent_output / load_agent_input /
       load_agent_metadata are defined (probably near the bottom)
    3. Paste the function below right next to them
    4. Save and restart Streamlit

The function reads runs/<run_id>/<agent>/console.json — a list of
captured stdout lines that AgentBase writes during agent execution.
"""

import json
from pathlib import Path


def load_agent_console(run_id: str, agent_name: str) -> str:
    """
    Read the captured console output for a given agent run.

    AgentBase writes captured stdout/stderr to:
        runs/<run_id>/<agent_name>/console.json

    Format on disk is either:
        - a JSON list of strings  -> joined with newlines
        - a JSON object with a "lines" key
        - a plain text file
    Returns "" if the file doesn't exist or can't be parsed.
    """
    base = Path("runs") / run_id / agent_name / "console.json"
    if not base.exists():
        # also tolerate console.txt as a fallback
        alt = Path("runs") / run_id / agent_name / "console.txt"
        if alt.exists():
            try:
                return alt.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return ""
        return ""

    try:
        raw = base.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    # Try to parse as JSON; fall back to raw text
    try:
        data = json.loads(raw)
    except Exception:
        return raw

    if isinstance(data, list):
        return "\n".join(str(x) for x in data)
    if isinstance(data, dict):
        if "lines" in data and isinstance(data["lines"], list):
            return "\n".join(str(x) for x in data["lines"])
        if "text" in data:
            return str(data["text"])
        # fallback: dump it
        return json.dumps(data, indent=2)
    return str(data)