"""
Link engine config — uses the project root config.yaml AND interpolates ${ENV_VAR}.
"""
import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_config = None
_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "config.yaml").exists():
            return parent
    return Path.cwd()


def _interpolate(obj):
    """Recursively replace ${ENV_VAR} placeholders with their env values."""
    if isinstance(obj, str):
        return _ENV_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {k: _interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate(v) for v in obj]
    return obj


def load_config() -> dict:
    root = _project_root()
    with open(root / "config.yaml") as f:
        raw = yaml.safe_load(f)
    raw = _interpolate(raw)  # <-- THIS WAS MISSING

    le = raw.get("link_engine", {}) or {}
    le["anthropic_api_key"] = (
        os.getenv("ANTHROPIC_API_KEY", "").strip()
        or (raw.get("anthropic", {}) or {}).get("api_key", "").strip()
    )
    le["llm_model"] = (raw.get("anthropic", {}).get("models", {}) or {}).get(
        "bulk", "claude-haiku-4-5-20251001"
    )
    le["embedding_model"] = le.get("embedding_model", "all-mpnet-base-v2")
    le["output_dir"] = str(root / le.get("output_dir", "outputs/link_engine"))
    le["title_prefix_strip"] = [
        "how to", "guide to", "your guide to", "the guide to",
    ]
    return le


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config