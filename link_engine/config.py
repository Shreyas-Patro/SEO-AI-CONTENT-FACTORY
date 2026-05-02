"""
Link engine config — uses the project root config.yaml
so we don't need to maintain two configs.
"""
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

load_dotenv()

_config = None


def _project_root() -> Path:
    """Walk up to find the directory containing config.yaml."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "config.yaml").exists():
            return parent
    return Path.cwd()


def load_config() -> dict:
    root = _project_root()
    with open(root / "config.yaml") as f:
        raw = yaml.safe_load(f)

    # Pull link_engine subsection + global API keys
    le = raw.get("link_engine", {}) or {}
    le["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY", "")
    le["llm_model"] = raw.get("anthropic", {}).get("models", {}).get("bulk", "claude-haiku-4-5-20251001")
    le["embedding_model"] = le.get("embedding_model", "all-mpnet-base-v2")
    le["output_dir"] = str(root / le.get("output_dir", "outputs/link_engine"))
    le["title_prefix_strip"] = ["how to", "guide to", "your guide to", "the guide to"]
    return le


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config